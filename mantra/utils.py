import os
import logging
import wave
import datetime
import numpy as np
import asyncio
from livekit import rtc
from livekit.agents import llm
from livekit.plugins import openai
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger("mantra.utils")

class SessionRecorder:
    """Records all audio tracks in a session and combines them into a single WAV on close."""
    
    SAMPLE_RATE = 48000
    NUM_CHANNELS = 1
    SAMPLE_WIDTH = 2  # 16-bit
    
    def __init__(self, recording_dir: str):
        self.recording_dir = recording_dir
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
        logger.info(f"Recording track {label} ({track_id}) to combined output")
        task = asyncio.create_task(self._consume_track(track, track_id, label))
        self._recording_tasks.append(task)
    
    async def _consume_track(self, track: rtc.Track, track_id: str, label: str):
        """Consume audio frames from a track and store them."""
        audio_stream = rtc.AudioStream(track)
        try:
            async for frame_event in audio_stream:
                self._tracks[track_id].append(bytes(frame_event.frame.data))
        except Exception as e:
            logger.error(f"Error recording track {label} ({track_id}): {e}")
        finally:
            await audio_stream.aclose()
        logger.info(f"Track {label} ({track_id}) stream ended")
    
    def save_combined(self) -> str:
        """Mix all recorded tracks into a single WAV file and return the path."""
        output_path = os.path.join(self.recording_dir, "recording.wav")
        
        if not self._tracks:
            logger.warning("No tracks were recorded")
            return output_path
        
        # Convert each track's frame list into a single numpy array
        track_arrays = []
        for track_id, frames in self._tracks.items():
            if frames:
                raw = b"".join(frames)
                arr = np.frombuffer(raw, dtype=np.int16)
                track_arrays.append(arr)
        
        if not track_arrays:
            logger.warning("All tracks were empty")
            return output_path
        
        # Pad shorter arrays with silence so they're all the same length
        max_len = max(len(a) for a in track_arrays)
        padded = []
        for arr in track_arrays:
            if len(arr) < max_len:
                arr = np.pad(arr, (0, max_len - len(arr)), mode='constant')
            padded.append(arr)
        
        # Mix by summing in float32 then clipping back to int16 range
        mixed = np.zeros(max_len, dtype=np.float32)
        for arr in padded:
            mixed += arr.astype(np.float32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        
        # Write the combined WAV
        with wave.open(output_path, 'wb') as wav:
            wav.setnchannels(self.NUM_CHANNELS)
            wav.setsampwidth(self.SAMPLE_WIDTH)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(mixed.tobytes())
        
        logger.info(f"Saved combined recording ({len(track_arrays)} tracks, {max_len / self.SAMPLE_RATE:.1f}s) to {output_path}")
        self.end_time = datetime.datetime.now()
        return output_path

    def save_transcript(self, history: list):
        """Save the conversation transcript to a text file."""
        output_path = os.path.join(self.recording_dir, "transcript.txt")
        full_transcript = ""
        try:
            with open(output_path, "w") as f:
                for msg in history:
                    # Handle role which might be an enum or string
                    role = msg.role
                    if hasattr(role, 'name'):
                        role = role.name
                    elif hasattr(role, 'value'):
                        role = role.value
                    
                    # Handle content which might be a list or string
                    content = msg.content
                    if isinstance(content, list):
                        content = " ".join([str(c) for c in content])
                        
                    if content:
                        text = f"{str(role).upper()}: {content}\n"
                        f.write(text)
                        full_transcript += text
            logger.info(f"Saved transcript to {output_path}")
            return full_transcript
        except Exception as e:
            logger.error(f"Error saving transcript: {e}")
            return ""

    async def generate_and_save_summary(self, llm_engine: openai.LLM, history: list):
        """Use the LLM to generate a summary based on the history and save it."""
        output_path = os.path.join(self.recording_dir, "summary.txt")
        
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

Give me the general summary of the conversation and after that the structured internal summary.
TRANSCRIPT:
"""
        for msg in history:
            role = msg.role
            if hasattr(role, 'name'):
                role = role.name
            elif hasattr(role, 'value'):
                role = role.value
            
            content = msg.content
            if isinstance(content, list):
                content = " ".join([str(c) for c in content])
                
            summary_prompt += f"{str(role).upper()}: {content}\n"
            
        try:
            # Create a simple chat context for the summary
            messages = [
                llm.ChatMessage(role="system", content=["You are a helpful assistant that summarizes medical appointment calls."]),
                llm.ChatMessage(role="user", content=[summary_prompt])
            ]
            
            # llm.chat returns a stream, we need to collect it
            stream = llm_engine.chat(chat_ctx=llm.ChatContext(items=messages))
            response = await stream.collect()
            summary_text = response.text
            
            with open(output_path, "w") as f:
                f.write(summary_text)
            logger.info(f"Saved summary to {output_path}")
            return summary_text
        except Exception as e:
            logger.error(f"Error generating/saving summary: {e}")
            return ""

async def push_to_mcp_server(log_data: dict):
    """Connect to the MCP server and push call log data."""
    server_params = StdioServerParameters(
        command="python3",
        args=[os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp", "server.py"))],
        env=os.environ.copy()
    )
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("insert_call_log", arguments={"log_data": log_data})
                logger.info(f"MCP Server Response: {result}")
                return result
    except Exception as e:
        logger.error(f"Error pushing to MCP server: {e}")
        return None
