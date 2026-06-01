import os
import io
import wave
import json
import time
import hmac
import hashlib
import asyncio
import datetime
import logging
import httpx
import numpy as np
from typing import Dict, List, Optional, Tuple

from livekit import rtc
from livekit.plugins import openai
from livekit.agents import llm
import boto3

logger = logging.getLogger("mantra.utils")

async def send_to_backend(payload: dict, max_retries: int = 3) -> bool:
    """POST the post-call payload to the MantraAssist backend with HMAC signing."""
    base_url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
    webhook_secret = os.getenv("MANTRAASSIST_WEBHOOK_SECRET", "")
    
    if not base_url:
        logger.warning("MANTRAASSIST_BACKEND_URL not set — skipping backend webhook")
        return False

    url = f"{base_url}/webhooks/n8n"
    
    timestamp = str(int(time.time()))
    
    if not payload:
        payload_str = '{}'
    else:
        payload_str = json.dumps(payload, separators=(',', ':'))
    
    data_to_sign = f"{payload_str}.{timestamp}"
    
    headers = {
        "Content-Type": "application/json",
        "x-timestamp": timestamp,
        "x-source": "n8n",
    }
    
    if webhook_secret:
        signature = hmac.new(
            webhook_secret.encode('utf-8'),
            data_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers["x-signature"] = signature
        logger.info(f"Signing request with HMAC (timestamp: {timestamp})")
    else:
        logger.warning("MANTRAASSIST_WEBHOOK_SECRET not set — sending unsigned request")

    # Use PLIVO_PROXY if set (not removed by agent.py proxy cleanup) for outbound webhook.
    # The container may need this proxy to resolve external hostnames.
    webhook_proxy = os.getenv("PLIVO_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")

    logger.info(f"Delivering post-call webhook to: {url}" + (f" via proxy: {webhook_proxy}" if webhook_proxy else ""))

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0, proxy=webhook_proxy) as client:
                resp = await client.post(url, content=payload_str, headers=headers)
                resp.raise_for_status()
                logger.info(f"Backend webhook delivered successfully (HTTP {resp.status_code})")
                return True
        except Exception as e:
            logger.error(f"Backend webhook attempt {attempt}/{max_retries} failed: {e}")

        if attempt < max_retries:
            await asyncio.sleep(2 ** (attempt - 1))

    return False

def upload_to_s3(file_bytes: bytes, s3_key: str) -> Optional[str]:
    """Upload bytes to S3 and return the public URL."""
    bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
    region = os.getenv("AWS_REGION", "us-east-1")
    
    if not bucket_name:
        logger.warning("AWS_S3_BUCKET_NAME not set — skipping upload")
        return None

    try:
        s3 = boto3.client('s3')
        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=file_bytes, ContentType='audio/mpeg', ACL='public-read')
        url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"
        logger.info(f"Uploaded recording to S3: {url}")
        return url
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        return None

class SessionRecorder:
    def __init__(self):
        self._tracks: Dict[str, List[bytes]] = {}
        self._recording_tasks: List[asyncio.Task] = []
        self.start_time = datetime.datetime.now()
        self.end_time = None
        
        self.SAMPLE_RATE = 48000
        self.NUM_CHANNELS = 1
        self.SAMPLE_WIDTH = 2 # 16-bit

    def start_recording(self, track: rtc.Track, label: str):
        track_id = track.sid or str(id(track))
        if track_id in self._tracks:
            return
        self._tracks[track_id] = []
        task = asyncio.create_task(self._consume_track(track, track_id, label))
        self._recording_tasks.append(task)

    async def _consume_track(self, track: rtc.Track, track_id: str, label: str):
        audio_stream = rtc.AudioStream(track)
        try:
            async for frame_event in audio_stream:
                self._tracks[track_id].append(bytes(frame_event.frame.data))
        except Exception as e:
            logger.error(f"Error recording track {label}: {e}")
        finally:
            await audio_stream.aclose()

    def get_combined_mp3_bytes(self) -> bytes:
        self.end_time = datetime.datetime.now()
        if not self._tracks:
            return b""
        
        track_arrays = []
        for frames in self._tracks.values():
            if frames:
                track_arrays.append(np.frombuffer(b"".join(frames), dtype=np.int16))
        
        if not track_arrays:
            return b""

        max_len = max(len(a) for a in track_arrays)
        mixed = np.zeros(max_len, dtype=np.float32)
        for arr in track_arrays:
            if len(arr) < max_len:
                arr = np.pad(arr, (0, max_len - len(arr)), mode="constant")
            mixed += arr.astype(np.float32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

        try:
            from pydub import AudioSegment
            audio = AudioSegment(mixed.tobytes(), frame_rate=self.SAMPLE_RATE, sample_width=self.SAMPLE_WIDTH, channels=self.NUM_CHANNELS)
            buf = io.BytesIO()
            audio.export(buf, format="mp3", bitrate="128k")
            return buf.getvalue()
        except Exception as e:
            logger.error(f"MP3 conversion failed: {e}")
            return b""

    @staticmethod
    def build_transcript(history: list) -> str:
        structured = []
        for msg in history:
            role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
            content = " ".join([str(c) for c in msg.content]) if isinstance(msg.content, list) else msg.content
            if content:
                role_label = "bot" if role.lower() == "assistant" else "user"
                structured.append({role_label: content})
        return json.dumps(structured)

    @staticmethod
    async def generate_summary(llm_engine: llm.LLM, history: list) -> str:
        summary_prompt = "Generate a structured call summary, do not include any extra information other than the call transcript.\n"
        for msg in history:
            role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
            content = " ".join([str(c) for c in msg.content]) if isinstance(msg.content, list) else msg.content
            summary_prompt += f"{role.upper()}: {content}\n"
        
        try:
            messages = [
                llm.ChatMessage(role="system", content=["You are a helpful assistant."]),
                llm.ChatMessage(role="user", content=[summary_prompt]),
            ]
            stream = llm_engine.chat(chat_ctx=llm.ChatContext(items=messages))
            response = await stream.collect()
            
            import re
            clean_text = re.sub(r'[*#_~`\[\]]', '', response.text)
            clean_text = clean_text.encode('ascii', 'ignore').decode('ascii')
            lines = [" ".join(line.split()) for line in clean_text.splitlines() if line.strip()]
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Summary failed: {e}")
            return ""

    @staticmethod
    def parse_summary_data(summary_text: str):
        sentiment_score = 0.5
        next_call_on = None
        custom_fields = {"appointment_date_time": "", "doctor": "", "hospital_location": ""}
        try:
            for line in summary_text.split("\n"):
                line = line.strip()
                if "Sentiment Score:" in line:
                    sentiment_score = float(line.split(":")[1].strip().split()[0])
                elif "Next Call Date:" in line:
                    val = line.replace("Next Call Date:", "").strip().strip("*-• ")
                    if val.lower() not in ["none", "n/a", "null"] and len(val) > 5:
                        next_call_on = val
                elif "Appointment Date & Time:" in line:
                    val = line.split(":", 1)[1].strip().strip("*-• \"'")
                    if val.lower() not in ["none", "n/a", "null", ""]:
                        custom_fields["appointment_date_time"] = val
                elif "Doctor:" in line:
                    val = line.split(":", 1)[1].strip().strip("*-• \"'")
                    if val.lower() not in ["none", "n/a", "null", ""]:
                        custom_fields["doctor"] = val
                elif "Hospital Location:" in line:
                    val = line.split(":", 1)[1].strip().strip("*-• \"'")
                    if val.lower() not in ["none", "n/a", "null", ""]:
                        custom_fields["hospital_location"] = val
        except:
            pass
        return sentiment_score, next_call_on, custom_fields
