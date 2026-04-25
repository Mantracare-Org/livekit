import logging
from dotenv import load_dotenv
import json
import asyncio
import os
import wave
import datetime
import numpy as np

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    llm,
)
from livekit.agents import TurnHandlingOptions
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import assemblyai, openai, cartesia, silero, deepgram

# Load environment variables
load_dotenv()          # Load .env (OpenAI, etc.)
load_dotenv(".env.local", override=True)  # Load .env.local (LiveKit, etc.) and override if needed

logger = logging.getLogger("agent")

server = AgentServer()


class SessionRecorder:
    """Records all audio tracks in a session and combines them into a single WAV on close."""
    
    SAMPLE_RATE = 48000
    NUM_CHANNELS = 1
    SAMPLE_WIDTH = 2  # 16-bit
    
    def __init__(self, recording_dir: str):
        self.recording_dir = recording_dir
        self._tracks: dict[str, list[bytes]] = {}  # track_id -> list of frame bytes
        self._recording_tasks: list[asyncio.Task] = []
    
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
        return output_path

    def save_transcript(self, history: list):
        """Save the conversation transcript to a text file."""
        output_path = os.path.join(self.recording_dir, "transcript.txt")
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
                        f.write(f"{str(role).upper()}: {content}\n")
            logger.info(f"Saved transcript to {output_path}")
        except Exception as e:
            logger.error(f"Error saving transcript: {e}")

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
        except Exception as e:
            logger.error(f"Error generating/saving summary: {e}")


@server.rtc_session(agent_name="mantra-agent")
async def entrypoint(ctx: JobContext):
    await ctx.connect()

    logger.info(f"--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # Initialize recording
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    recording_dir = f"recordings/session_{session_id}_{ctx.room.name}"
    os.makedirs(recording_dir, exist_ok=True)
    logger.info(f"Recording session to {recording_dir}")
    
    recorder = SessionRecorder(recording_dir)

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, f"participant_{participant.identity}")

    @ctx.room.on("local_track_published")
    def on_local_track_published(publication: rtc.LocalTrackPublication, track: rtc.Track):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, "agent")

    # Base instructions focused on professional voice behavior
    initial_instructions = """You are a warm, professional Care Support Assistant on a phone call.

CORE BEHAVIOR:
- This is a PHONE CALL. Speak naturally.
- Keep responses SHORT (1-2 sentences).
- Use natural fillers: "Got it", "Sure", "Theek hai", "Haan".
- You are BILINGUAL. Start in English. If the user speaks Hindi or asks for it, switch to Hindi immediately.
- Sound like a helpful human friend, not a robot.
- Do NOT use markdown, bullet points, or special characters.
- If the user pauses, wait patiently for them to finish.

Follow these specific instructions:
"""
    client_name = "User"
    
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            
            # 1. Handle main prompt
            if "prompt" in payload:
                # Remove the impatient "not responding" rule which causes repetitive loops
                clean_prompt = payload["prompt"].replace("If the client is not responding, ask questions like 'hope you are hearing me', etc.", "")
                initial_instructions += "\n" + clean_prompt
            
            if "client_name" in payload:
                client_name = payload["client_name"]

            # 2. Extract ALL other features as context for the LLM
            context_header = "\n\n--- ADDITIONAL CALL CONTEXT ---\n"
            context_body = ""
            
            for key, value in payload.items():
                if key == "prompt":
                    continue
                
                readable_key = key.replace("_", " ").title()
                
                if isinstance(value, dict):
                    context_body += f"{readable_key}:\n"
                    for k, v in value.items():
                        rk = k.replace("_", " ").title()
                        context_body += f"  - {rk}: {v}\n"
                elif isinstance(value, list):
                    context_body += f"- {readable_key}: {', '.join(map(str, value))}\n"
                else:
                    context_body += f"- {readable_key}: {value}\n"
            
            if context_body:
                initial_instructions += context_header + context_body
            
            logger.info(f"Loaded full context for {client_name}")
        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.6,
                "max_delay": 2.0,
            },
            interruption={
                "mode": "adaptive",
                "resume_false_interruption": True,
                "false_interruption_timeout": 3.0,
                "min_words": 2,
            },
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.1,
            min_silence_duration=0.3,
        ),
        # Using Hindi STT as it's better at catching Hinglish/Indian English
        stt=deepgram.STT(model="nova-3", language="hi"),
        llm=openai.LLM(model="gpt-4o-mini"),
        # Using Hindi-Multilingual TTS to support both languages natively
        tts=cartesia.TTS(
            model="sonic-3",
            voice="95d51f79-c397-46f9-b49a-23763d3eaa2d",
            speed=1.0,
            language="hi"
        ),
    )

    agent = Agent(
        instructions=initial_instructions,
    )

    await session.start(agent=agent, room=ctx.room)
    
    # Check if agent track was already published before we attached the listener
    for publication in ctx.room.local_participant.track_publications.values():
        if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(publication.track, "agent")
    
    logger.info("Waiting for remote participant to join...")
    while not list(ctx.room.remote_participants.values()):
        await asyncio.sleep(0.5)
        
    logger.info("Remote participant joined. Initializing conversation...")
    await asyncio.sleep(2.0)
    
    await session.generate_reply(instructions=f"Greet the user named {client_name} and follow the opening script in your instructions.")
    
    # Wait for session to end, then save everything
    @session.on("close")
    def on_session_close():
        recorder.save_combined()
        
        # Save transcript
        # session.history is a ChatContext, use .messages() to get the list of ChatMessage
        history = session.history.messages()
        recorder.save_transcript(history)
        
        # Generate and save summary in background
        asyncio.create_task(recorder.generate_and_save_summary(session.llm, history))
        
        logger.info("Session closed, recording and transcripts saved.")

if __name__ == "__main__":
    cli.run_app(server)

#TODO 
"""
1. Transcript 
3. Summary 
4. If next call when ( date and time ( in string format ) ) 
5. {Decide next stage - this can be send to webhook}
6. MCP server
7. MCP reception 
"""