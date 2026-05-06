import os
import io
import logging
import wave
import datetime
import numpy as np
import asyncio
import json

import boto3
import httpx
from botocore.exceptions import ClientError

from livekit import rtc
from livekit.agents import llm
from livekit.plugins import openai

logger = logging.getLogger("mantra.utils")

# ---------------------------------------------------------------------------
# S3 Helper
# ---------------------------------------------------------------------------

def upload_to_s3(wav_bytes: bytes, s3_key: str) -> str | None:
    """Upload WAV bytes directly to S3. Returns the object URL or None on failure.
    
    Required env vars:
      AWS_S3_BUCKET_NAME, AWS_REGION
      AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  (or IAM role on ECS)
    """
    bucket = os.getenv("AWS_S3_BUCKET_NAME")
    region = os.getenv("AWS_REGION", "ap-south-1")

    if not bucket:
        logger.warning("AWS_S3_BUCKET_NAME not set — skipping S3 upload")
        return None

    try:
        s3_client = boto3.client("s3", region_name=region)
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=wav_bytes,
            ContentType="audio/wav",
        )
        url = f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"
        logger.info(f"Uploaded recording to S3: {url}")
        return url
    except ClientError as e:
        logger.error(f"S3 upload failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Backend Webhook Helper
# ---------------------------------------------------------------------------

import hmac
import hashlib
import time

async def send_to_backend(payload: dict, max_retries: int = 3) -> bool:
    """POST the post-call payload to the MantraAssist backend with HMAC signing.
    
    Endpoint: {MANTRAASSIST_BACKEND_URL}/webhooks/n8n
    Retries with exponential back-off (1s, 2s, 4s).
    """
    base_url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
    webhook_secret = os.getenv("MANTRAASSIST_WEBHOOK_SECRET", "")
    
    if not base_url:
        logger.warning("MANTRAASSIST_BACKEND_URL not set — skipping backend webhook")
        return False

    url = f"{base_url}/webhooks/n8n"
    
    # 1. Prepare signing (HMAC-SHA256)
    timestamp = str(int(time.time()))
    payload_json = json.dumps(payload, separators=(',', ':'))
    data_to_sign = f"{payload_json}.{timestamp}"
    
    headers = {
        "Content-Type": "application/json",
        "x-mantra-timestamp": timestamp,
    }
    
    if webhook_secret:
        signature = hmac.new(
            webhook_secret.encode('utf-8'),
            data_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers["x-mantra-signature"] = signature
        logger.info(f"Signing request with HMAC (timestamp: {timestamp})")
    else:
        logger.warning("MANTRAASSIST_WEBHOOK_SECRET not set — sending unsigned request")

    logger.info(f"Delivering post-call webhook to: {url}")

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                logger.info(f"Backend webhook delivered successfully (HTTP {resp.status_code})")
                return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Backend returned {e.response.status_code} on attempt {attempt}/{max_retries}: {e.response.text[:200]}")
        except Exception as e:
            logger.error(f"Backend webhook attempt {attempt}/{max_retries} failed: {e}")

        if attempt < max_retries:
            delay = 2 ** (attempt - 1)
            logger.info(f"Retrying in {delay}s...")
            await asyncio.sleep(delay)

    logger.error("Backend webhook delivery failed after all retries")
    return False


# ---------------------------------------------------------------------------
# Session Recorder  (fully in-memory — no disk I/O)
# ---------------------------------------------------------------------------

class SessionRecorder:
    """Records all audio tracks in a session entirely in-memory."""

    SAMPLE_RATE = 48000
    NUM_CHANNELS = 1
    SAMPLE_WIDTH = 2  # 16-bit

    def __init__(self):
        self._tracks: dict[str, list[bytes]] = {}  # track_id -> list of frame bytes
        self._recording_tasks: list[asyncio.Task] = []
        self.start_time = datetime.datetime.now()
        self.end_time = None

    def start_recording(self, track: rtc.Track, label: str):
        """Start recording a track with the given label (e.g. 'agent', 'user')."""
        track_id = track.sid or str(id(track))
        if track_id in self._tracks:
            return  # Already recording this track

        self._tracks[track_id] = []
        logger.info(f"Recording track {label} ({track_id})")
        task = asyncio.create_task(self._consume_track(track, track_id, label))
        self._recording_tasks.append(task)

    async def _consume_track(self, track: rtc.Track, track_id: str, label: str):
        """Consume audio frames from a track and store them in memory."""
        audio_stream = rtc.AudioStream(track)
        try:
            async for frame_event in audio_stream:
                self._tracks[track_id].append(bytes(frame_event.frame.data))
        except Exception as e:
            logger.error(f"Error recording track {label} ({track_id}): {e}")
        finally:
            await audio_stream.aclose()
        logger.info(f"Track {label} ({track_id}) stream ended")

    def get_combined_wav_bytes(self) -> bytes:
        """Mix all recorded tracks into a single WAV and return the raw bytes.
        
        No files are created — everything stays in memory.
        """
        self.end_time = datetime.datetime.now()

        if not self._tracks:
            logger.warning("No tracks were recorded")
            return b""

        # Convert each track's frame list into a single numpy array
        track_arrays = []
        for track_id, frames in self._tracks.items():
            if frames:
                raw = b"".join(frames)
                arr = np.frombuffer(raw, dtype=np.int16)
                track_arrays.append(arr)

        if not track_arrays:
            logger.warning("All tracks were empty")
            return b""

        # Pad shorter arrays with silence so they're all the same length
        max_len = max(len(a) for a in track_arrays)
        padded = []
        for arr in track_arrays:
            if len(arr) < max_len:
                arr = np.pad(arr, (0, max_len - len(arr)), mode="constant")
            padded.append(arr)

        # Mix by summing in float32 then clipping back to int16 range
        mixed = np.zeros(max_len, dtype=np.float32)
        for arr in padded:
            mixed += arr.astype(np.float32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

        # Build WAV in memory
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(self.NUM_CHANNELS)
            wav.setsampwidth(self.SAMPLE_WIDTH)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(mixed.tobytes())

        wav_bytes = buf.getvalue()
        duration = max_len / self.SAMPLE_RATE
        logger.info(f"Built combined WAV in memory ({len(track_arrays)} tracks, {duration:.1f}s, {len(wav_bytes)} bytes)")
        return wav_bytes

    @staticmethod
    def build_transcript(history: list) -> list[dict]:
        """Build a structured transcript from the conversation history.
        
        Returns a list like [{"bot": "Hello"}, {"user": "Hi"}, ...].
        """
        structured: list[dict] = []
        try:
            for msg in history:
                # Handle role which might be an enum or string
                role = msg.role
                if hasattr(role, "name"):
                    role = role.name
                elif hasattr(role, "value"):
                    role = role.value

                # Handle content which might be a list or string
                content = msg.content
                if isinstance(content, list):
                    content = " ".join([str(c) for c in content])

                if content:
                    role_label = "bot" if str(role).lower() == "assistant" else "user"
                    structured.append({role_label: content})
        except Exception as e:
            logger.error(f"Error building transcript: {e}")

        return structured

    @staticmethod
    async def generate_summary(llm_engine: openai.LLM, history: list) -> str:
        """Use the LLM to generate a post-call summary. Returns the summary text."""
        summary_prompt = """Based on the following conversation transcript, generate a structured internal summary as requested:

POST-CALL INTERNAL SUMMARY (REQUIRED OUTPUT)
After call ends, generate structured internal notes:
Appointment Status: Confirmed / Rescheduled / Uncertain
Attendance Likelihood: High / Medium / Low
Follow-up Required: Yes / No
Follow-up Date & Time (if any)
Pending Items (if any)
Rescheduling Needed: Yes / No
Overall Patient Sentiment: Calm / Neutral / Anxious
Do NOT assume emotions.
Only write what was expressed.

ADDITIONAL DATA (STRICT):
Sentiment Score: [A number between 0.0 and 1.0, where 1.0 is extremely positive/calm and 0.0 is extremely negative/anxious]
Next Call Date: [YYYY-MM-DD HH:MM:SS format if a follow-up was agreed, otherwise "None"]
Appointment Date & Time: [Extracted appointment date and time from the conversation, otherwise "None"]
Doctor: [Extracted doctor name from the conversation, otherwise "None"]
Hospital Location: [Extracted hospital location from the conversation, otherwise "None"]

Give me the general summary of the conversation and after that the structured internal summary.
TRANSCRIPT:
"""
        for msg in history:
            role = msg.role
            if hasattr(role, "name"):
                role = role.name
            elif hasattr(role, "value"):
                role = role.value

            content = msg.content
            if isinstance(content, list):
                content = " ".join([str(c) for c in content])

            summary_prompt += f"{str(role).upper()}: {content}\n"

        try:
            messages = [
                llm.ChatMessage(role="system", content=["You are a helpful assistant that summarizes medical appointment calls."]),
                llm.ChatMessage(role="user", content=[summary_prompt]),
            ]
            stream = llm_engine.chat(chat_ctx=llm.ChatContext(items=messages))
            response = await stream.collect()
            logger.info("Summary generated successfully")
            return response.text
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return ""

    @staticmethod
    def parse_summary_data(summary_text: str):
        """Extract sentiment score, next call date, and custom fields from the generated summary."""
        sentiment_score = 0.5  # Default neutral
        next_call_on = None
        custom_fields = {
            "appointment_date_time": "",
            "doctor": "",
            "hospital_location": "",
        }

        try:
            for line in summary_text.split("\n"):
                line = line.strip()
                if "Sentiment Score:" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        try:
                            score_str = parts[1].strip().split()[0]
                            sentiment_score = float(score_str)
                        except (ValueError, IndexError):
                            pass
                elif "Next Call Date:" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        date_str = line.replace("Next Call Date:", "").strip()
                        clean_date = date_str.strip().strip("*-• ").strip()
                        if clean_date and clean_date.lower() not in [
                            "none", "n/a", "null", "na", "not applicable", "not specified",
                        ] and len(clean_date) > 5:
                            next_call_on = clean_date
                elif "Appointment Date & Time:" in line:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        val = parts[1].strip().strip("*-• \"'")
                        if val.lower() not in ["none", "n/a", "null", "na", "", "not applicable", "not specified"]:
                            custom_fields["appointment_date_time"] = val
                elif "Doctor:" in line:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        val = parts[1].strip().strip("*-• \"'")
                        if val.lower() not in ["none", "n/a", "null", "na", "", "not applicable", "not specified"]:
                            custom_fields["doctor"] = val
                elif "Hospital Location:" in line:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        val = parts[1].strip().strip("*-• \"'")
                        if val.lower() not in ["none", "n/a", "null", "na", "", "not applicable", "not specified"]:
                            custom_fields["hospital_location"] = val
        except Exception as e:
            logger.error(f"Error parsing summary data: {e}")

        return sentiment_score, next_call_on, custom_fields
