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
from urllib.parse import urlparse

from livekit import rtc
from livekit.plugins import openai
from livekit.agents import llm
import boto3

logger = logging.getLogger("mantra.utils")

def redact_proxy_credentials(proxy_url: str) -> str:
    """Removes basic auth credentials from proxy URLs before logging."""
    if not proxy_url:
        return proxy_url
    try:
        parsed = urlparse(proxy_url)
        if parsed.username or parsed.password:
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return proxy_url

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

    logger.info(f"Delivering post-call webhook to: {url}" + (f" via proxy: {redact_proxy_credentials(webhook_proxy)}" if webhook_proxy else ""))

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

    # Strip proxy env vars so requests/urllib3 doesn't pick them up
    _saved = {}
    for _var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "PLIVO_PROXY"):
        _val = os.environ.pop(_var, None)
        if _val is not None:
            _saved[_var] = _val

    try:
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        s3_kwargs = {"region_name": region}
        if aws_access_key_id and aws_secret_access_key:
            s3_kwargs["aws_access_key_id"] = aws_access_key_id
            s3_kwargs["aws_secret_access_key"] = aws_secret_access_key

        s3 = boto3.client('s3', **s3_kwargs)
        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=file_bytes, ContentType='audio/mpeg', ACL='public-read')
        url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"
        logger.info(f"Uploaded recording to S3: {url}")
        return url
    except Exception as e:
        logger.error(f"S3 upload failed: {e}", exc_info=True)
        return None
    finally:
        os.environ.update(_saved)

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
        summary_prompt = (
            "Generate a call summary as a single, coherent paragraph. It must properly state: "
            "what the patient concern/reason for calling was, the details discussed in the call, "
            "the conclusion, and any other important patient details based on the transcript. "
            "Keep it concise but detailed. Here is the transcript:\n"
        )
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
    async def analyze_call(
        llm_engine: llm.LLM,
        history: list,
        current_stage_id: Optional[int],
        stage_details: List[dict],
        duration: int
    ) -> dict:
        # Fallback values
        fallback_stage_id = current_stage_id
        
        # Parse stage details to find fallback IDs based on rules
        not_answering_id = current_stage_id
        interested_id = current_stage_id
        confirmed_id = current_stage_id
        follow_up_id = current_stage_id
        not_interested_id = current_stage_id
        
        for stage in stage_details:
            desc = stage.get("description", "").lower()
            sid = stage.get("stage_id")
            if "not answering" in desc or "failed" in desc or "incomplete" in desc:
                not_answering_id = sid
            elif "shown interest" in desc or "interested" in desc:
                interested_id = sid
            elif "confirmed" in desc or "appointment date" in desc:
                confirmed_id = sid
            elif "follow up" in desc or "call later" in desc:
                follow_up_id = sid
            elif "not interested" in desc or "no further follow" in desc:
                not_interested_id = sid

        # Construct transcript
        transcript_lines = []
        for msg in history:
            role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
            content = " ".join([str(c) for c in msg.content]) if isinstance(msg.content, list) else msg.content
            if content:
                role_label = "Assistant" if role.lower() == "assistant" else "User"
                transcript_lines.append(f"{role_label}: {content}")
        transcript_text = "\n".join(transcript_lines)

        current_time = datetime.datetime.now()
        current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

        prompt = f"""
You are an expert analyst for a care support and CRM system. Analyze the phone call transcript and metadata below.

--- CALL METADATA ---
Current Date and Time: {current_time_str}
Call Duration: {duration} seconds
Current Stage ID: {current_stage_id}

--- AVAILABLE CRM STAGES ---
{json.dumps(stage_details, indent=2)}

--- TRANSCRIPT ---
{transcript_text}

--- ANALYSIS TASK ---
1. Generate a call summary as a single, coherent paragraph. It must properly state:
   - What the patient concern/reason for calling was.
   - The details discussed in the call.
   - The conclusion (e.g. appointment booked, callback scheduled, disconnected, not interested).
   - Any other important patient details based on the transcript.
2. Determine the correct Next Stage ID (`new_stage_id`) from the AVAILABLE CRM STAGES above.
   - Select the stage ID whose description best matches the outcome of the call.
   - If the patient confirmed/booked an appointment, select the stage for "confirmed the appointment".
   - If the patient asked to call back or follow up later, select the stage for "follow up or call later".
   - If the patient showed interest but didn't book yet, select the stage for "shown interest".
   - If the patient is not interested or declined, select the stage for "not interested" or the specific declining reason stage.
   - If none of the stages match or the call did not change the state, default to the current stage ID: {current_stage_id}.
3. Extract additional metadata:
   - `next_call_on`: If a follow-up or callback is scheduled/needed (especially if stage is "follow up or call later" or "not answering / failed call"), suggest a callback date and time (e.g., "2026-06-02 15:00:00"). If the stage description specifies adding 24 hours to the current time, calculate that date and time (current time is {current_time_str}). If no follow-up is needed, use null.
   - `appointment_date_time`: If the patient booked/confirmed an appointment date/time, extract it (e.g., "2026-06-05 11:30 AM"). Otherwise, use null.
   - `doctor`: Extract any mentioned doctor's name. Otherwise, use null.
   - `hospital_location`: Extract the preferred hospital location/center name. Otherwise, use null.
   - `sentiment_score`: Rate the user's sentiment from 0.0 (very negative/angry) to 1.0 (very positive/happy), with 0.5 as neutral.

You MUST return your response as a valid JSON object with the following schema:
{{
  "summary": "string (a single paragraph call summary)",
  "new_stage_id": integer (the selected stage ID from the list),
  "next_call_on": "string or null",
  "appointment_date_time": "string or null",
  "doctor": "string or null",
  "hospital_location": "string or null",
  "sentiment_score": float
}}

Provide ONLY the JSON object. Do not include markdown code block syntax or other text wrapper.
"""

        try:
            messages = [
                llm.ChatMessage(role="system", content=["You are a helpful assistant."]),
                llm.ChatMessage(role="user", content=[prompt]),
            ]
            stream = llm_engine.chat(chat_ctx=llm.ChatContext(items=messages))
            response = await stream.collect()
            
            text = response.text.strip()
            if text.startswith("```"):
                first_newline = text.find("\n")
                if first_newline != -1:
                    text = text[first_newline:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            
            res_dict = json.loads(text)
            
            summary = res_dict.get("summary") or ""
            new_stage_id = res_dict.get("new_stage_id")
            next_call_on = res_dict.get("next_call_on")
            appointment_date_time = res_dict.get("appointment_date_time")
            doctor = res_dict.get("doctor")
            hospital_location = res_dict.get("hospital_location")
            sentiment_score = res_dict.get("sentiment_score", 0.5)
            
            if new_stage_id is not None:
                try:
                    new_stage_id = int(new_stage_id)
                except ValueError:
                    new_stage_id = fallback_stage_id
            else:
                new_stage_id = fallback_stage_id

            if not summary:
                summary = await SessionRecorder.generate_summary(llm_engine, history)

        except Exception as e:
            logger.error(f"analyze_call failed: {e}. Falling back to default heuristics.")
            summary = await SessionRecorder.generate_summary(llm_engine, history)
            new_stage_id = fallback_stage_id
            next_call_on = None
            appointment_date_time = ""
            doctor = ""
            hospital_location = ""
            sentiment_score = 0.5

        if new_stage_id in [not_answering_id, follow_up_id] and not next_call_on:
            tomorrow = current_time + datetime.timedelta(hours=24)
            next_call_on = tomorrow.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "summary": summary,
            "new_stage_id": new_stage_id,
            "next_call_on": next_call_on,
            "appointment_date_time": appointment_date_time,
            "doctor": doctor,
            "hospital_location": hospital_location,
            "sentiment_score": sentiment_score
        }

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


def normalize_to_iso8601(dt_str: Optional[str]) -> Optional[str]:
    """Convert 'YYYY-MM-DD HH:MM:SS' to ISO-8601 'YYYY-MM-DDTHH:MM:SS.000Z'.

    Returns None if input is None/empty. Passes through unparseable strings unchanged.
    """
    if not dt_str:
        return None
    try:
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except (ValueError, TypeError):
        return dt_str
