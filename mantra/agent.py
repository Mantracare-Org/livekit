import logging
import json
import asyncio
import os
import datetime
import sys
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

# Import our production helpers
from mantra.utils import SessionRecorder, upload_to_s3, send_to_backend

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

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Fully in-memory recorder — no disk I/O
    recorder = SessionRecorder()

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, f"participant_{participant.identity}")

    @ctx.room.on("local_track_published")
    def on_local_track_published(publication: rtc.LocalTrackPublication, track: rtc.Track):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, "agent")

    initial_instructions = """You are a warm, professional Care Support Assistant on a phone call.

CORE BEHAVIOR:
- This is a PHONE CALL. Speak naturally.
- Keep responses SHORT (1-2 sentences).
- Use natural fillers: "Got it", "Sure", "Theek hai", "Haan".
- You are BILINGUAL. Start in English. If the user speaks Hindi or asks for it, switch to Hindi immediately.
- Sound like a helpful human friend, not a robot.
- Do NOT use markdown, bullet points, or special characters.
- If the user pauses, wait patiently for them to finish.
- ACTIVELY LISTEN: If the user asks a question (e.g., about directions, a bus stand, or any other detail), address it directly and helpfully BEFORE returning to the main topic. Never ignore the user's questions or blindly repeat your script.
- RETAIN CONTEXT & AVOID REPETITION: Remember the user's previous answers. Do NOT repeatedly ask the same questions. If they say no or want to focus on something else, acknowledge it and move on. DO NOT be pushy.

Follow these specific instructions:
"""
    client_name = "User"
    
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            
            # Normalize client_custom_fileds to client_custom_fields
            if "client_custom_fileds" in payload:
                ccf = payload.pop("client_custom_fileds")
                if isinstance(ccf, str):
                    try:
                        ccf = json.loads(ccf)
                    except Exception:
                        pass
                payload["client_custom_fields"] = ccf
            elif "client_custom_fields" in payload:
                ccf = payload["client_custom_fields"]
                if isinstance(ccf, str):
                    try:
                        payload["client_custom_fields"] = json.loads(ccf)
                    except Exception:
                        pass
            
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
                
            # Add an overriding rule at the very end so it takes precedence over the backend prompt
            initial_instructions += "\n\n*** CRITICAL OVERRIDING RULES ***\n"
            initial_instructions += "1. NEVER repeat the same question twice. If the user dodges the question or asks a counter-question, answer them and DO NOT repeat your previous question.\n"
            initial_instructions += "2. DO NOT push for an appointment if the user hasn't explicitly agreed or if they are asking about other things. Let the conversation flow naturally.\n"
            initial_instructions += "3. Answer user's questions DIRECTLY without appending a sales pitch or appointment request at the end of every turn.\n"
            
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
                "false_interruption_timeout": 1.5,
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
            speed=1.05,
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
    
    # Wait for session to end, then finalize everything
    @session.on("close")
    def on_session_close():
        async def finalize():
            logger.info("Session closed — starting post-call processing...")

            # 1. Build recording WAV in memory
            wav_bytes = recorder.get_combined_wav_bytes()
            
            # 2. Upload recording to S3
            recording_url = ""
            if wav_bytes:
                s3_key = f"recordings/{session_id}_{ctx.room.name}.wav"
                recording_url = upload_to_s3(wav_bytes, s3_key) or ""
            
            # 3. Build transcript in memory
            history = session.history.messages()
            transcript_data = SessionRecorder.build_transcript(history)
            
            # 4. Generate summary in memory
            summary_text = await SessionRecorder.generate_summary(session.llm, history)
            
            # 5. Calculate duration
            duration = 0
            if recorder.start_time and recorder.end_time:
                duration = int((recorder.end_time - recorder.start_time).total_seconds())
            
            # 6. Parse call metadata
            try:
                call_payload = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse metadata: {e}")
                call_payload = {}
                
            # 7. Parse additional data from summary
            sentiment_score, next_call_on, parsed_custom_fields = SessionRecorder.parse_summary_data(summary_text)
            
            # Combine payload's client_custom_fields with parsed ones
            client_custom_fields = call_payload.get("client_custom_fields", {})
            for k, v in parsed_custom_fields.items():
                if v:  # Only update if the parsed value is not empty
                    client_custom_fields[k] = v
            
            # Determine call status based on duration
            call_status = "Completed"
            if duration < 10:
                call_status = "Incomplete"
            
            # 8. Build webhook payload
            webhook_payload = {
                "event": "CALL_DATA_UPDATE",
                "data": {
                    "client_id": call_payload.get("lead_id"),
                    "call_id": call_payload.get("call_id"),
                    "call_status": call_status,
                    "call_transcript": transcript_data,
                    "ai_summary": summary_text,
                    "recording_url": recording_url,
                    "call_duration_seconds": duration,
                    "next_call_on": next_call_on,
                    "ai_call_id": ctx.job.id,
                    "new_stage_id": call_payload.get("stage_id"),
                    "process_id": call_payload.get("process_id"),
                    "notes": "",
                    "metadata": call_payload.get("metadata", {}),
                    "client_custom_fields": client_custom_fields,
                    "call_custom_fields": call_payload.get("call_custom_fields", {}),
                    "url": ""
                }
            }
            
            # 9. Send to MantraAssist backend
            delivered = await send_to_backend(webhook_payload)
            
            logger.info(
                f"Post-call processing complete | "
                f"Call ID: {ctx.job.id} | "
                f"Lead: {webhook_payload['data']['client_id']} | "
                f"Status: {call_status} | "
                f"Duration: {duration}s | "
                f"S3: {'✓' if recording_url else '✗'} | "
                f"Backend: {'✓' if delivered else '✗'}"
            )

        asyncio.create_task(finalize())

def download_files():
    """Pre-download required models for production caching."""
    logger.info("Pre-downloading Silero VAD model...")
    try:
        silero.VAD.load()
    except Exception as e:
        logger.error(f"Failed to pre-download Silero VAD: {e}")

    logger.info("Pre-downloading Multilingual Turn Detector model (best effort)...")
    try:
        # This might fail due to lack of job context, but we try anyway
        # to trigger any lazy-loading of models if possible.
        MultilingualModel()
    except Exception as e:
        # We expect a RuntimeError: no job context found
        logger.info(f"Note: MultilingualModel pre-download skipped or failed (this is usually fine): {e}")

    logger.info("Pre-downloading completed.")

def run_agent():
    cli.run_app(server)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "download-files":
        download_files()
    else:
        run_agent()
