import logging
import json
import asyncio
import os
import datetime
from dotenv import load_dotenv

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

# Import our custom recorder and MCP helper
from mantra.utils import SessionRecorder, push_to_mcp_server

# Load environment variables
load_dotenv()          # Load .env (OpenAI, etc.)
load_dotenv(".env.local", override=True)  # Load .env.local (LiveKit, etc.) and override if needed

logger = logging.getLogger("mantra.agent")

server = AgentServer()

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
        stt=deepgram.STT(model="nova-3", language="hi", smart_format=True, numerals=True),
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
        async def finalize():
            recording_path = recorder.save_combined()
            
            # Save transcript and get text
            history = session.history.messages()
            transcript_text = recorder.save_transcript(history)
            
            # Generate summary and get text
            summary_text = await recorder.generate_and_save_summary(session.llm, history)
            
            # Calculate duration
            duration = 0
            if recorder.start_time and recorder.end_time:
                duration = int((recorder.end_time - recorder.start_time).total_seconds())
            
            # Prepare data for DB
            try:
                payload = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
            except:
                payload = {}
                
            log_data = {
                "org_id": payload.get("org_id", 1),
                "process_id": payload.get("process_id"),
                "stage_id": payload.get("stage_id"),
                "call_transcript": transcript_text,
                "recording_url": recording_path,
                "call_duration_seconds": duration,
                "ai_summary": summary_text,
                "call_status": "completed",
                "call_type": payload.get("call_type"),
                "ai_call_id": ctx.job.id,
                "lead_id": payload.get("lead_id"),
                "metadata": json.dumps(payload),
                "updated_at": datetime.datetime.now().isoformat()
            }
            
            # Push to DB via MCP
            logger.info("Pushing call log to database via MCP...")
            await push_to_mcp_server(log_data)
            logger.info("Session closed and data pushed to DB.")

        asyncio.create_task(finalize())

def run_agent():
    cli.run_app(server)

if __name__ == "__main__":
    run_agent()
