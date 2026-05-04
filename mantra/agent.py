import logging
import json
import asyncio
import os
import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv

# Initialize colorama
init(autoreset=True)

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
    recording_dir = f"/tmp/recordings/session_{session_id}_{ctx.room.name}"
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
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse metadata: {e}")
                payload = {}
                
            # Parse additional data from summary
            sentiment_score, next_call_on, parsed_custom_fields = SessionRecorder.parse_summary_data(summary_text)
            
            # Combine payload's client_custom_fields with parsed ones
            client_custom_fields = payload.get("client_custom_fields", {})
            for k, v in parsed_custom_fields.items():
                if v:  # Only update if the parsed value is not empty
                    client_custom_fields[k] = v
            
            # Determine call status based on duration
            call_status = "Completed"
            if duration < 10:
                call_status = "Incomplete"
            
            # Prepare webhook payload
            webhook_payload = {
                "event": "CALL_DATA_UPDATE",
                "data": {
                    "client_id": payload.get("lead_id"),
                    "call_id": payload.get("call_id"),
                    "call_status": call_status,
                    "call_transcript": transcript_text,
                    "ai_summary": summary_text,
                    "recording_url": recording_path,
                    "call_duration_seconds": duration,
                    "next_call_on": next_call_on,
                    "ai_call_id": ctx.job.id,
                    "new_stage_id": payload.get("stage_id"),
                    "process_id": payload.get("process_id"),
                    "notes": "",
                    "metadata": payload.get("metadata", {}),
                    "client_custom_fields": client_custom_fields,
                    "call_custom_fields": payload.get("call_custom_fields", {}),
                    "url": ""
                }
            }
            
            # Save the webhook payload locally
            webhook_path = os.path.join(recording_dir, "webhook_payload.json")
            with open(webhook_path, "w", encoding="utf-8") as f:
                json.dump(webhook_payload, f, indent=4, ensure_ascii=False)
                
            print(f"\n{Fore.CYAN}{Style.BRIGHT}----------------------------------------------------------------")
            print(f"{Fore.CYAN}{Style.BRIGHT} GENERATED WEBHOOK PAYLOAD LOCALLY")
            print(f"{Fore.CYAN} Call ID: {ctx.job.id}")
            print(f"{Fore.CYAN} Lead ID: {webhook_payload['data']['client_id']}")
            print(f"{Fore.CYAN} Status: {call_status}")
            print(f"{Fore.CYAN} Duration: {duration}s")
            print(f"{Fore.CYAN} Payload saved to: {webhook_path}")
            print(f"{Fore.CYAN}{Style.BRIGHT}----------------------------------------------------------------\n")
            
            logger.info("Session closed and webhook payload generated.")

        asyncio.create_task(finalize())

def run_agent():
    cli.run_app(server)

if __name__ == "__main__":
    run_agent()
