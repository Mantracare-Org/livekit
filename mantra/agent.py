import logging
import json
import asyncio
import os
import datetime
import sys

# Force-disable global proxy for the agent process so it doesn't break WebSocket/WebRTC
# connections to LiveKit, Deepgram, and Cartesia. The UI Server handles the Plivo proxy separately.
_PROXY_VARS = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
_removed_proxies = []
for proxy_var in _PROXY_VARS:
    if proxy_var in os.environ:
        _removed_proxies.append(f"{proxy_var}={os.environ[proxy_var]}")
        del os.environ[proxy_var]
# import colorama
# from colorama import Fore, Style

# # Initialize colorama for cross-platform colored terminal logs
# colorama.init(autoreset=True)
# LLM Selection Logic
_is_inference = os.getenv("LIVEKIT_AGENTS_INFERENCE") == "1"
_proc_type = "Inference Subprocess" if _is_inference else "Main Worker"

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s INFO (Type: {_proc_type}, PID: {os.getpid()}) %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("mantra.agent")
logger.info("Initializing process...")
if _removed_proxies:
    logger.info(f"Proxy cleanup: removed {_removed_proxies} from environment")
else:
    logger.info("Proxy cleanup: no proxy env vars found (clean environment)")

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

from livekit.agents.tts import FallbackAdapter
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import assemblyai, openai, google, cartesia, silero, deepgram

# Import our production helpers
from mantra.utils import SessionRecorder, upload_to_s3, send_to_backend, normalize_to_iso8601

# MCP server for database tools (patient lookup, appointment booking, etc.)
from livekit.agents import mcp as lk_mcp



VOICE_MAPPING = {
    "gemma": "62ae83ad-4f6a-430b-af41-a9bede9286ca",
    "alistair": "c8f7835e-28a3-4f0c-80d7-c1302ac62aae",
    "sunny": "156fb8d2-335b-4950-9cb3-a2d33befec77",
    "tyler": "820a3788-2b37-4d21-847a-b65d8a68c99a",
    "vikas": "adf97b9d-905c-41de-9fe9-afb387116d06",
    "camila": "bef2ba57-5c10-433b-b215-3bef35110a81",
    "renata": "d3793b7b-4996-409c-9d59-96dd09f47717",
    "arushi": "95d51f79-c397-46f9-b49a-23763d3eaa2d"
}

# Load environment variables
load_dotenv()          # Load .env (OpenAI, etc.)
load_dotenv(".env.local", override=True)  # Load .env.local (LiveKit, etc.) and override if needed

# Second proxy cleanup: dotenv may have re-introduced proxy vars from .env files
for proxy_var in _PROXY_VARS:
    if proxy_var in os.environ:
        logger.warning(f"Proxy var {proxy_var} was re-introduced by dotenv — removing it")
        del os.environ[proxy_var]

# Startup diagnostics: verify critical API keys are present
# In Docker, .env.local is excluded by .dockerignore — keys must come from docker-compose env vars
_critical_keys = {
    "LIVEKIT_URL": os.getenv("LIVEKIT_URL"),
    "LIVEKIT_API_KEY": os.getenv("LIVEKIT_API_KEY"),
    "LIVEKIT_API_SECRET": os.getenv("LIVEKIT_API_SECRET"),
    "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY"),
    "CARTESIA_API_KEY": os.getenv("CARTESIA_API_KEY"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
}
_missing = [k for k, v in _critical_keys.items() if not v]
if _missing:
    logger.error(f"⚠️ MISSING CRITICAL ENV VARS: {_missing} — agent will NOT function correctly!")
    logger.error("Ensure these are set in your docker-compose.yml or .env file on the production server.")
else:
    logger.info("All critical API keys present ✓")

server = AgentServer()

@server.rtc_session(agent_name="mantra-agent")
async def entrypoint(ctx: JobContext):
    # Plain log to verify entrypoint is reached
    logger.info(f"Entrypoint reached for room: {ctx.room.name}")
    
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

    client_name = "User"
    is_inbound = False

    direction_override = ""
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            is_inbound = payload.get("direction") == "inbound"
        except Exception:
            payload = {}
    
    if is_inbound:
        direction_override = "\n--- INBOUND CALL CONTEXT ---\n- This is an INBOUND call. The caller reached out to you.\n- Greet warmly and ask how you can help.\n- Do not assume you know why they are calling. Let them explain.\n- Identify yourself: 'MantraCare' or as instructed in your prompt.\n- If the caller seems confused, help them understand who you are.\n"
    else:
        direction_override = ""

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
""" + direction_override

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

            # If the call arrived via an external IVR (SIP header passthrough),
            # inject a dedicated context block so the LLM understands the caller's
            # origin and reason for calling without having to re-ask.
            ivr_keys = {"account_number", "call_reason", "department", "language", "user_id", "caller_choice"}
            ivr_block = ""
            for key in payload:
                if key in ivr_keys and payload[key]:
                    readable = key.replace("_", " ").title()
                    ivr_block += f"- {readable}: {payload[key]}\n"
            if ivr_block:
                initial_instructions += "\n\n--- IVR CONTEXT (from external system) ---\n"
                initial_instructions += "This caller was pre-screened by an external IVR system:\n"
                initial_instructions += ivr_block
                if "call_reason" in payload:
                    initial_instructions += "- Address their stated reason for calling directly.\n"

            # Add an overriding rule at the very end so it takes precedence over the backend prompt
            initial_instructions += "\n\n*** CRITICAL OVERRIDING RULES ***\n"
            initial_instructions += "1. NEVER repeat the same question twice. If the user dodges the question or asks a counter-question, answer them and DO NOT repeat your previous question.\n"
            initial_instructions += "2. DO NOT push for an appointment if the user hasn't explicitly agreed or if they are asking about other things. Let the conversation flow naturally.\n"
            initial_instructions += "3. Answer user's questions DIRECTLY without appending a sales pitch or appointment request at the end of every turn.\n"

            # ── Database Tool Instructions ──
            initial_instructions += "\n\n*** DATABASE TOOLS ***\n"
            initial_instructions += "You have access to a database with patient records, doctors, hospitals, and appointments.\n"
            initial_instructions += "Use these tools when needed — do NOT ask the patient for information you can look up:\n"
            initial_instructions += "- To identify a caller: use get_patient_info with their phone number.\n"
            initial_instructions += "- To list hospitals: use get_hospitals.\n"
            initial_instructions += "- To find doctors: use get_doctors (optionally filter by hospital name).\n"
            initial_instructions += "- To check availability: use get_available_slots with doctor ID and date.\n"
            initial_instructions += "- To book: use create_appointment with patient ID, doctor ID, time, hospital.\n"
            initial_instructions += "- To reschedule or cancel: use update_appointment with the appointment ID.\n"
            initial_instructions += "- To view call history: use get_call_history with patient ID or phone.\n"
            initial_instructions += "- To query any data: use execute_query with a SQL SELECT statement.\n"
            initial_instructions += "If a tool returns an error or no results, inform the patient and suggest alternatives.\n"
            logger.info(f"Loaded full context for {client_name}")
        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")

    # 3. Select LLM and Voice based on payload
    if 'payload' in locals():
        # Handle nested ai_payload if present
        ai_p = payload.get("ai_payload")
        if not isinstance(ai_p, dict):
            ai_p = {}
        
        # Priority for Model: ai_payload.ai_model -> payload.model -> default "openai"
        model_name = ai_p.get("ai_model") or payload.get("model") or "openai"
        model_name = str(model_name).lower()
        
        # Priority for Voice: ai_payload.voice_id -> payload.voice_id -> voice_name -> voice -> default "arushi"
        _raw_voice = ai_p.get("voice_id") or payload.get("voice_id") or payload.get("voice_name") or payload.get("voice") or "arushi"
        voice_input = "arushi" if _raw_voice in (None, "null", "None") else _raw_voice
        voice_id = VOICE_MAPPING.get(str(voice_input).lower(), voice_input)
        
        # Priority for Speed: ai_payload.voice_speed -> payload.voice_speed -> default 1.05
        voice_speed = ai_p.get("voice_speed") or payload.get("voice_speed") or 1.05
    else:
        model_name = "openai"
        voice_input = "arushi"
        voice_id = VOICE_MAPPING["arushi"]
        voice_speed = 1.05

    # Safe parsing and clamping for speed (0.1 to 2.0)
    try:
        voice_speed = float(voice_speed)
        voice_speed = max(0.1, min(2.0, voice_speed))
    except (ValueError, TypeError):
        voice_speed = 1.05

    # Explicit logs for call configuration
    logger.info("--- CALL CONFIGURATION ---")
    logger.info(f"Model: {model_name}")
    logger.info(f"Voice: {voice_input} (ID: {voice_id})")
    logger.info(f"Speed: {voice_speed}")
    logger.info("--------------------------")

    if model_name == "gemini":
        logger.info("Using Gemini (Google) LLM")
        llm_engine = google.LLM(model="gemini-2.5-flash")
    elif model_name == "deepseek":
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if not deepseek_key:
            logger.warning("DEEPSEEK_API_KEY not set, falling back to OpenAI")
            llm_engine = openai.LLM(model="gpt-4o-mini")
        else:
            logger.info("Using DeepSeek LLM")
            llm_engine = openai.LLM(
                model="deepseek-v4-flash",
                api_key=deepseek_key,
                base_url="https://api.deepseek.com"
            )
    else:
        logger.info("Using OpenAI LLM")
        llm_engine = openai.LLM(model="gpt-4o-mini")

    # Collect Cartesia API keys dynamically from environment variables
    cartesia_keys = []
    for env_var in ["CARTESIA_API_KEY", "CARTESIA_API_KEY_2", "CARTESIA_API_KEY_3"]:
        val = os.getenv(env_var)
        if val:
            if "," in val:
                cartesia_keys.extend([k.strip() for k in val.split(",") if k.strip()])
            else:
                cartesia_keys.append(val.strip())
    
    # If no keys are specified in variables, let it default to standard CARTESIA_API_KEY logic
    if not cartesia_keys:
        cartesia_keys = [None]

    # Setup Fallback TTS using the pool of keys to cycle on rate limits (429) / connection failures
    tts_pool = [
        cartesia.TTS(
            model="sonic-3",
            voice=voice_id,
            speed=voice_speed,
            language="hi",
            api_key=key
        )
        for key in cartesia_keys
    ]

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.6,
                "max_delay": 2.0,
            },
            interruption={
                "mode": "vad",
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
        llm=llm_engine,
        # Using Hindi-Multilingual TTS with FallbackAdapter to support multiple API keys
        tts=FallbackAdapter(tts_pool),
    )

    # ── MCP Database Tools ──
    # Launch the MCP Postgres server as a subprocess so the LLM can query
    # patient info, doctors, hospital locations, and book appointments.
    mcp_servers = []
    try:
        db_mcp = lk_mcp.MCPServerStdio(
            command="uv",
            args=["run", "python", "mcp/server.py"],
            client_session_timeout_seconds=10,
        )
        mcp_servers.append(db_mcp)
        logger.info("MCP database server configured for agent tools")
    except Exception as e:
        logger.warning(f"Failed to configure MCP server: {e} — agent will run without database tools")

    agent = Agent(
        instructions=initial_instructions,
        mcp_servers=mcp_servers if mcp_servers else None,
    )

    await session.start(agent=agent, room=ctx.room)
    
    # Check if agent track was already published before we attached the listener
    for publication in ctx.room.local_participant.track_publications.values():
        if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(publication.track, "agent")
    
    if ctx.room.name.startswith("test_"):
        logger.info("Test room detected. Skipping wait for remote participant to initialize synthesis.")
    else:
        logger.info("Waiting for remote participant to join...")
        while not list(ctx.room.remote_participants.values()):
            await asyncio.sleep(0.5)
            
        logger.info("Remote participant joined. Initializing conversation...")
        await asyncio.sleep(2.0)
    
    if is_inbound:
        await session.generate_reply(instructions="Greet the caller warmly. Introduce yourself and ask how you can help them today.")
    else:
        await session.generate_reply(instructions=f"Greet the user named {client_name} and follow the opening script in your instructions.")
    
    # Wait for session to end, then finalize everything
    @session.on("close")
    def on_session_close():
        # Capture session data synchronously before async task runs
        # (avoids race where session cleans up resources before finalize() starts)
        history_snapshot = list(session.history.messages()) if session.history else []
        llm_engine = session.llm
        logger.info(f"Session closed — spawning finalize (history: {len(history_snapshot)} messages)")

        async def finalize():
            recording_url = ""
            transcript_data = ""
            summary_text = ""
            duration = 0
            call_status = "Error"
            webhook_payload = None

            try:
                logger.info("Starting post-call processing...")

                # 1. Pre-load call metadata
                try:
                    call_payload = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
                except Exception as e:
                    logger.error(f"Failed to parse metadata: {e}")
                    call_payload = {}

                # 2. Build recording and upload to S3
                try:
                    mp3_bytes = recorder.get_combined_mp3_bytes()
                    if mp3_bytes:
                        call_id = call_payload.get("call_id") or call_payload.get("voice_id")
                        s3_key = f"recordings/{call_id}.mp3" if call_id else f"recordings/{session_id}_{ctx.room.name}.mp3"
                        recording_url = upload_to_s3(mp3_bytes, s3_key) or ""
                        logger.info(f"S3 recording: {'uploaded' if recording_url else 'upload failed'}")
                    else:
                        logger.info("No audio data captured for recording")
                except Exception as e:
                    logger.error(f"Recording/S3 step failed: {e}", exc_info=True)

                # 3. Build transcript from captured history snapshot
                try:
                    transcript_data = SessionRecorder.build_transcript(list(history_snapshot))
                    logger.info(f"Transcript built ({len(history_snapshot)} messages)")
                except Exception as e:
                    logger.error(f"Transcript step failed: {e}", exc_info=True)

                # 4. Calculate duration
                if recorder.start_time and recorder.end_time:
                    duration = int((recorder.end_time - recorder.start_time).total_seconds())

                # Determine call status based on duration
                call_status = "Completed"
                if duration < 10:
                    call_status = "Incomplete"

                # 5. Run unified analysis to generate summary, stage transition, and metadata
                current_stage_id = call_payload.get("stage_id")
                stage_details = call_payload.get("stageDetails", [])
                
                summary_text = ""
                new_stage_id = current_stage_id
                next_call_on = None
                client_custom_fields = call_payload.get("client_custom_fields", {})
                if not isinstance(client_custom_fields, dict):
                    client_custom_fields = {}

                try:
                    if llm_engine and history_snapshot:
                        analysis = await SessionRecorder.analyze_call(
                            llm_engine=llm_engine,
                            history=list(history_snapshot),
                            current_stage_id=current_stage_id,
                            stage_details=stage_details,
                            duration=duration
                        )
                        summary_text = analysis["summary"]
                        new_stage_id = analysis["new_stage_id"]
                        next_call_on = analysis["next_call_on"]
                        
                        if analysis.get("appointment_date_time"):
                            client_custom_fields["appointment_date_time"] = analysis["appointment_date_time"]
                        if analysis.get("doctor"):
                            client_custom_fields["doctor"] = analysis["doctor"]
                        if analysis.get("hospital_location"):
                            client_custom_fields["hospital_location"] = analysis["hospital_location"]
                        
                        logger.info(f"Analysis completed. New Stage ID: {new_stage_id}, Next Call On: {next_call_on}")
                    else:
                        logger.warning("Skipping analysis: LLM or history unavailable after session close")
                except Exception as e:
                    logger.error(f"Analysis or summary generation failed: {e}", exc_info=True)

                # 6. Build webhook payload
                client_phone = None
                try:
                    remotes = list(ctx.room.remote_participants.values())
                    if remotes:
                        client_phone = remotes[0].identity
                except Exception as e:
                    logger.error(f"Failed to get remote participant identity: {e}")

                webhook_payload = {
                    "event": "CALL_DATA_UPDATE",
                    "data": {
                        "client_id": call_payload.get("lead_id"),
                        "call_id": call_payload.get("call_id") or call_payload.get("voice_id"),
                        "client_phone": client_phone,
                        "trunk_id": call_payload.get("trunk_id"),
                        "direction": call_payload.get("direction") or ("inbound" if is_inbound else "outbound"),
                        "call_status": call_status,
                        "call_transcript": transcript_data,
                        "ai_summary": summary_text,
                        "recording_url": recording_url,
                        "call_duration_seconds": duration,
                        "next_call_on": normalize_to_iso8601(next_call_on),
                        "ai_call_id": ctx.job.id,
                        "new_stage_id": new_stage_id,
                        "process_id": call_payload.get("process_id"),
                        "notes": "",
                        "metadata": call_payload.get("metadata", {}),
                        "client_custom_fields": client_custom_fields,
                        "call_custom_fields": call_payload.get("call_custom_fields", {}),
                        "url": ""
                    }
                }

            except Exception as e:
                logger.error(f"Pipeline error in finalize: {e}", exc_info=True)

            # 8. Send to MantraAssist backend (always attempted, even if pipeline failed)
            try:
                if webhook_payload is None:
                    webhook_payload = {
                        "event": "CALL_DATA_UPDATE",
                        "data": {
                            "ai_call_id": ctx.job.id,
                            "call_status": "Error",
                            "notes": "Post-call pipeline encountered an error — minimal payload sent",
                        }
                    }

                logger.info(f"Delivering post-call webhook to backend...")
                delivered = await send_to_backend(webhook_payload)
            except Exception as e:
                logger.error(f"Webhook delivery failed: {e}", exc_info=True)
                delivered = False

            logger.info(
                f"Post-call processing complete | "
                f"Call ID: {ctx.job.id} | "
                f"Lead: {webhook_payload.get('data', {}).get('client_id', 'N/A')} | "
                f"Status: {webhook_payload.get('data', {}).get('call_status', 'N/A')} | "
                f"Duration: {duration}s | "
                f"S3: {'✓' if recording_url else '✗'} | "
                f"Backend: {'✓' if delivered else '✗'}"
            )

        asyncio.create_task(finalize())

def run_agent():
    _is_start_cmd = "start" in sys.argv
    if _is_start_cmd:
        logger.info("Mantra Agent Server is starting...")
    
    try:
        cli.run_app(server)
    except Exception as e:
        logger.error(f"Failed to run agent server: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    run_agent()
