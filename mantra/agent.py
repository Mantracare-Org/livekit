import logging
import json
import asyncio
import os
import datetime
import sys
from typing import Annotated

# ── Suppress OpenTelemetry 429 errors ──────────────────────────────────
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
os.environ.setdefault("OTEL_LOGS_EXPORTER", "none")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("https_proxy", None)
os.environ.pop("http_proxy", None)

# ── Colorama for cross-platform colored terminal logs ──────────────────
from colorama import Fore, Back, Style, init as colorama_init
colorama_init(autoreset=True)

class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Back.WHITE,
    }

    def format(self, record):
        record.raw_msg = record.getMessage()
        color = self.LEVEL_COLORS.get(record.levelno, Fore.WHITE)
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)

# LLM Selection Logic
_is_inference = os.getenv("LIVEKIT_AGENTS_INFERENCE") == "1"
_proc_type = "Inference Subprocess" if _is_inference else "Main Worker"

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(ColorFormatter(
    f"%(asctime)s INFO (Type: {_proc_type}, PID: {os.getpid()}) %(name)s: %(message)s"
))
_file_handler = logging.FileHandler("/tmp/agent.log")
_file_handler.setFormatter(logging.Formatter(
    f"%(asctime)s INFO (Type: {_proc_type}, PID: {os.getpid()}) %(name)s: %(message)s"
))
logging.basicConfig(level=logging.DEBUG, handlers=[_handler, _file_handler])
logger = logging.getLogger("mantra.agent")
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)
logger.info("Initializing process...")

# Also suppress noisy OTEL SDK logs once the SDK initialises
logging.getLogger("opentelemetry").setLevel(logging.ERROR)

from dotenv import load_dotenv

from livekit import rtc, api
from livekit.agents.llm import (
    ChatContext,
    ChatMessage,
)
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
from livekit.plugins import openai, google, cartesia, silero, deepgram

# Import our production helpers
from mantra.utils import SessionRecorder, upload_to_s3, send_to_backend, normalize_to_iso8601



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


server = AgentServer()

# --- Transfer/Handoff Configuration ---
TRANSFER_NUMBERS = {}
_raw_transfer = os.getenv("TRANSFER_NUMBERS")
if _raw_transfer:
    try:
        TRANSFER_NUMBERS = json.loads(_raw_transfer)
        logger.info(f"Loaded {len(TRANSFER_NUMBERS)} department transfer mappings")
    except Exception:
        logger.warning("Failed to parse TRANSFER_NUMBERS from env — must be JSON object e.g. {\"refund\": \"+911234567890\"}")
TRANSFER_DEFAULT_NUMBER = os.getenv("TRANSFER_DEFAULT_NUMBER", "")
TRANSFER_SIP_TRUNK_ID = os.getenv("TRANSFER_SIP_TRUNK_ID", "")
if TRANSFER_DEFAULT_NUMBER:
    logger.info(f"Transfer default number configured: {TRANSFER_DEFAULT_NUMBER}")
if TRANSFER_SIP_TRUNK_ID:
    logger.info(f"Transfer SIP trunk configured: {TRANSFER_SIP_TRUNK_ID}")
# -------------------------------------------------

class AssistantFunctions:
    def __init__(self, job_metadata: str, room_name: str, room: rtc.Room = None):
        self.job_metadata = job_metadata
        self.room_name = room_name
        self.room = room
        self.handoff_triggered = False
        self.agent = None

    @llm.function_tool(
        description="Transfer the call to a human agent in a specific department when the user requests it, "
                    "you cannot resolve their issue, or they seem frustrated. "
                    "Specify the department (e.g., 'refund', 'support', 'billing', 'general') "
                    "based on what the user needs."
    )
    async def transfer_to_human(
        self,
        reason: Annotated[str, "Why the human agent is needed — be specific about the user's request"],
        department: Annotated[str, "The department to transfer to (e.g., refund, support, billing, general)"] = "general"
    ):
        logger.info(f"Handoff requested. Reason: {reason}, Department: {department}")
        self.handoff_triggered = True
        self.last_reason = reason
        self.last_department = department

        # Parse metadata to get call/lead IDs
        try:
            payload = json.loads(self.job_metadata) if self.job_metadata else {}
        except Exception:
            payload = {}

        # Determine target number from department mapping
        dept_lower = department.lower().strip()
        target_number = TRANSFER_NUMBERS.get(dept_lower, TRANSFER_DEFAULT_NUMBER)
        trunk_id = TRANSFER_SIP_TRUNK_ID or payload.get("trunk_id") or payload.get("call_from_id") or ""

        if target_number and trunk_id:
            try:
                lk_api = api.LiveKitAPI(
                    url=os.getenv("LIVEKIT_URL"),
                    api_key=os.getenv("LIVEKIT_API_KEY"),
                    api_secret=os.getenv("LIVEKIT_API_SECRET")
                )
                timestamp = datetime.datetime.now().strftime("%H%M%S%f")
                call_id = payload.get("call_id") or payload.get("voice_id") or self.room_name
                human_identity = f"human_{call_id}_{timestamp}"
                await lk_api.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk_id,
                        sip_call_to=target_number,
                        room_name=self.room_name,
                        participant_identity=human_identity,
                        participant_name=f"Human - {department.title()}"
                    )
                )
                await lk_api.aclose()
                logger.info(f"Human agent ({target_number}) added to room {self.room_name} for {department} department")
            except Exception as e:
                logger.error(f"Failed to add human agent via SIP: {e}")
        else:
            missing = []
            if not target_number:
                missing.append("target phone number")
            if not trunk_id:
                missing.append("SIP trunk ID")
            logger.warning(f"Cannot transfer: missing {', '.join(missing)}. Backend notification sent anyway.")

        # Notify backend
        webhook_payload = {
            "event": "HANDOFF_REQUESTED",
            "data": {
                "room_name": self.room_name,
                "reason": reason,
                "department": department,
                "call_id": payload.get("call_id") or payload.get("voice_id"),
                "lead_id": payload.get("lead_id"),
                "client_name": payload.get("client_name", "User"),
            }
        }
        await send_to_backend(webhook_payload)

        # Override agent instructions to enforce absolute silence
        if self.agent:
            try:
                await self.agent.update_instructions(
                    "You are SILENT. The call has been transferred to a human agent. "
                    "Say absolutely nothing. Do not speak, do not acknowledge, do not say goodbye. "
                    "The human agent handles everything from here. SILENT."
                )
                logger.info("Agent instructions overridden to enforce silence")
            except Exception as e:
                logger.error(f"Failed to update agent instructions: {e}")

        # Physically mute the agent's audio — can't speak if no output
        if self.room:
            try:
                for pub in self.room.local_participant.track_publications.values():
                    if pub.kind == rtc.TrackKind.KIND_AUDIO:
                        await pub.mute()
                        logger.info("Agent audio muted — silent mode engaged")
            except Exception as e:
                logger.error(f"Failed to mute agent audio: {e}")

        return "TRANSFER_COMPLETE. Do not speak."

@server.rtc_session(agent_name="mantra-agent")
async def entrypoint(ctx: JobContext):
    entrypoint_start_time = asyncio.get_event_loop().time()
    # Plain log to verify entrypoint is reached
    logger.info(f"Entrypoint reached for room: {ctx.room.name}")
    
    await ctx.connect()

    logger.info(f"--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # Initialize function context for handoff
    # Fully in-memory recorder — no disk I/O
    recorder = SessionRecorder()
    fnc_ctx = AssistantFunctions(ctx.job.metadata, ctx.room.name, ctx.room)
    call_state = {"user_joined": False}

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

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

TRANSFER CAPABILITY (CRITICAL):
- You HAVE a function called "transfer_to_human" that transfers the call to a real human agent.
- When the user asks to speak to a human, you cannot resolve their issue, or they seem frustrated — USE the transfer_to_human function IMMEDIATELY. Do NOT say you cannot transfer. You CAN transfer. Use the function.
- If the user mentions a specific department (refund, billing, support), pass it as the department parameter. Otherwise use "general".
- After the function executes, you will be muted. Say nothing. The human takes over.
- NEVER say "I can't transfer" or "I don't have that capability" — you DO have it. Call the function.

PRONUNCIATION (CRITICAL):
- ALWAYS write the brand name as "MantraCare" (as a single word). NEVER write "Mantra Care" with a space.
- ALWAYS write "MantraAssist" (as a single word). NEVER write "Mantra Assist" with a space.
- These are spoken brand names on a phone call — single-word format ensures correct pronunciation.

Follow these specific instructions:
"""
    client_name = "User"
    is_inbound = False
    
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            
            if payload.get("direction") == "inbound":
                is_inbound = True

            
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
            initial_instructions += "4. If the user asks to speak to a human, asks to be transferred, or mentions a department — you MUST call the transfer_to_human function IMMEDIATELY. Do NOT keep talking. Call the function.\n"

            
            if is_inbound:
                initial_instructions += "\n--- INBOUND CALL CONTEXT ---\n"
                initial_instructions += "- This is an INBOUND call. The caller reached out to you.\n"
                initial_instructions += "- Greet warmly and ask how you can help.\n"
                initial_instructions += "- Do not assume you know why they are calling. Let them explain.\n"
                initial_instructions += "- Identify yourself: 'Mantra Care' or as instructed in your prompt.\n"
                initial_instructions += "- If the caller seems confused, help them understand who you are.\n"
                
            logger.info(f"Loaded full context for {client_name} (inbound: {is_inbound})")
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
        voice_speed = ai_p.get("voice_speed") or payload.get("voice_speed") or 1
    else:
        model_name = "openai"
        voice_input = "arushi"
        voice_id = VOICE_MAPPING["arushi"]
        voice_speed = 1.0

    # Safe parsing and clamping for speed (0.1 to 2.0)
    try:
        voice_speed = float(voice_speed)
        voice_speed = max(0.1, min(2.0, voice_speed))
    except (ValueError, TypeError):
        voice_speed = 1.0

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
    # Collect Cartesia API keys dynamically from environment variables, pairing each with its account-specific pronunciation dictionary
    cartesia_configs = []
    for key_env, dict_env in [
        ("CARTESIA_API_KEY", "CARTESIA_PRONUNCIATION_DICT_ID"),
        ("CARTESIA_API_KEY_2", "CARTESIA_PRONUNCIATION_DICT_ID_2"),
        ("CARTESIA_API_KEY_3", "CARTESIA_PRONUNCIATION_DICT_ID_3")
    ]:
        key_val = os.getenv(key_env)
        dict_val = os.getenv(dict_env)
        if key_val:
            if "," in key_val:
                # If multiple keys are given in a comma-separated string, they share the same dict_id
                cartesia_configs.extend([{"key": k.strip(), "dict_id": dict_val.strip() if dict_val else None} for k in key_val.split(",") if k.strip()])
            else:
                cartesia_configs.append({"key": key_val.strip(), "dict_id": dict_val.strip() if dict_val else None})
    
    # If no keys are specified in variables, let it default to standard CARTESIA_API_KEY logic
    if not cartesia_configs:
        cartesia_configs = [{"key": None, "dict_id": os.getenv("CARTESIA_PRONUNCIATION_DICT_ID")}]

    language = "en"
        
    if language:
        language = str(language).lower()

    # Diagnostic: log the resolved TTS language so we can verify in production
    logger.info(f"TTS Language resolved to: '{language}' (None means auto-detect)")
    logger.info(f"TTS Pronunciation Dicts: {[cfg['dict_id'] for cfg in cartesia_configs]} ({len(cartesia_configs)} keys)")

    # Setup Fallback TTS using the pool of keys to cycle on rate limits (429) / connection failures
    tts_pool = [
        cartesia.TTS(
            model="sonic-3",
            voice=voice_id,
            speed=voice_speed,
            language=language,
            api_key=cfg["key"],
            pronunciation_dict_id=None,
        )
        for cfg in cartesia_configs
    ]

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.3,
                "max_delay": 1.5,
            },
            interruption={
                "mode": "vad",
                "resume_false_interruption": True,
                "false_interruption_timeout": 0.8,
                "min_words": 2,
            },
            preemptive_generation={
                "preemptive_tts": True,
            },
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.1,
            min_silence_duration=0.2,
        ),
        # Using Hindi STT as it's better at catching Hinglish/Indian English
        stt=deepgram.STT(model="nova-3", language="hi", smart_format=True, numerals=True),
        llm=llm_engine,
        # # Using Hindi-Multilingual TTS to support both languages natively
        # tts=cartesia.TTS(
        #     model="sonic-3",
        #     voice=voice_id,
        #     speed=voice_speed,
        #     language="hi"
        # ),

        # Using FallbackAdapter to support multiple API keys
        tts=FallbackAdapter(tts_pool),
    )

    agent = Agent(
        instructions=initial_instructions,
        tools=[fnc_ctx.transfer_to_human]
    )
    fnc_ctx.agent = agent
    logger.info(f"Agent initialized with transfer_to_human tool: {fnc_ctx.transfer_to_human is not None}")

    # Call duration limiter logic
    async def call_limiter():
        logger.info("Call limiter started — waiting for remote participant to join.")
        try:
            # Wait for remote participant to join
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(1.0)

            logger.info("Remote participant detected in room. Starting polling loop.")
            farewell_done = False

            while True:
                elapsed = asyncio.get_event_loop().time() - entrypoint_start_time

                # Check if participants still exist
                if not ctx.room.remote_participants:
                    logger.info("No remote participants left — call ended naturally.")
                    return

                # Determine limits based on handoff state
                if fnc_ctx.handoff_triggered:
                    max_duration = 600.0  # 10 minutes when handoff is active
                else:
                    max_duration = 180.0  # 3 minutes normally

                # Farewell stage: 2m 30s (only if no handoff)
                if not farewell_done and not fnc_ctx.handoff_triggered and elapsed >= 150.0:
                    logger.info(f"Farewell stage hit at t={elapsed:.2f}s")
                    if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                        current_inst = agent.instructions
                        if isinstance(current_inst, str):
                            farewell_inst = (
                                "IMPORTANT: The call time is ending now. "
                                "On your next turn, say a quick, natural one-sentence goodbye "
                                "and do not continue the conversation. Do not ask questions."
                            )
                            await agent.update_instructions(current_inst + "\n\n" + farewell_inst)
                        logger.info("Farewell instructions set.")
                    farewell_done = True

                # Hard disconnect check
                if elapsed >= max_duration:
                    logger.warning(f"HARD DISCONNECT: {max_duration/60:.0f}m limit reached. Force disconnecting room.")
                    if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                        await _force_disconnect_room(ctx)
                    return

                await asyncio.sleep(2.0)

        except asyncio.CancelledError:
            logger.info("Call limiter cancelled (call ended naturally before limits).")
        except Exception as e:
            logger.error(f"Error in call limiter: {e}")

    try:
        await session.start(agent=agent, room=ctx.room)
        limiter_task = asyncio.create_task(call_limiter())
        
        # Check if agent track was already published before we attached the listener
        for publication in ctx.room.local_participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                recorder.start_recording(publication.track, "agent")
        
        if ctx.room.name.startswith("test_"):
            logger.info("Test room detected. Skipping wait for remote participant to initialize synthesis.")
            call_state["user_joined"] = True
        else:
            logger.info("Waiting for remote participant to join...")
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(0.5)
                
            logger.info("Remote participant joined. Initializing conversation...")
            call_state["user_joined"] = True
            await asyncio.sleep(0.5)
        
        logger.info(f"Generating greeting for {client_name} (inbound: {is_inbound})...")
        if is_inbound:
            session.generate_reply(instructions="Greet the caller warmly. Introduce yourself and ask how you can help them today.")
        else:
            session.generate_reply(instructions=f"Greet the user named {client_name} and follow the opening script in your instructions.")
        logger.info("Greeting generation requested.")
        
        @session.on("close")
        def on_session_close():
            history_snapshot = list(session.history.messages()) if session.history else []
            logger.info(f"Session closed — spawning finalize (history: {len(history_snapshot)} messages)")
        
        # Wait for the call_limiter to decide the call is done
        # (participant leaves, timeout reached, or hard disconnect)
        await limiter_task
    
    except asyncio.CancelledError:
        logger.info("Call entrypoint coroutine cancelled.")
    except Exception as e:
        logger.error(f"Error in entrypoint execution: {e}", exc_info=True)
    finally:
        logger.info("Entering entrypoint finally block (cleaning up and finalizing)...")
        # 1. Cancel limiter task
        if 'limiter_task' in locals() and not limiter_task.done():
            limiter_task.cancel()
            
        # 2. Capture history snapshot immediately before session cleans up
        history_snapshot = list(session.history.messages()) if (session and session.history) else []
        
        # 3. Shielded finalization
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

                # 2. Determine session directory
                call_id = call_payload.get("call_id") or call_payload.get("voice_id") or "unknown"
                lead_id = call_payload.get("lead_id") or "unknown"
                session_dir_name = f"session_{lead_id}_call_{call_id}"
                session_dir = os.path.join("recordings", session_dir_name)
                os.makedirs(session_dir, exist_ok=True)

                # 3. Build recordings and save locally + upload to S3
                try:
                    mp3_bytes = recorder.get_combined_mp3_bytes()
                    if mp3_bytes:
                        recording_path = os.path.join(session_dir, "recording.mp3")
                        with open(recording_path, "wb") as f:
                            f.write(mp3_bytes)
                        logger.info(f"Recording saved: {recording_path}")
                        loop = asyncio.get_running_loop()
                        recording_url = await loop.run_in_executor(None, upload_to_s3, mp3_bytes, f"recordings/{session_dir_name}/recording.mp3") or ""
                        logger.info(f"S3 recording: {'uploaded' if recording_url else 'upload failed'}")
                    else:
                        logger.info("No audio data captured for recording")
                except Exception as e:
                    logger.error(f"Recording step failed: {e}", exc_info=True)

                # 4. Build transcript from captured history snapshot
                try:
                    handoff_info = {"triggered": fnc_ctx.handoff_triggered}
                    if fnc_ctx.handoff_triggered:
                        handoff_info["reason"] = getattr(fnc_ctx, 'last_reason', "")
                        handoff_info["department"] = getattr(fnc_ctx, 'last_department', "general")
                    transcript_data = SessionRecorder.build_transcript(list(history_snapshot), handoff_info)
                    transcript_path = os.path.join(session_dir, "transcript.txt")
                    with open(transcript_path, "w") as f:
                        f.write(transcript_data)
                    logger.info(f"Transcript saved: {transcript_path} ({len(history_snapshot)} messages)")
                except Exception as e:
                    logger.error(f"Transcript step failed: {e}", exc_info=True)

                # 5. Calculate duration
                if recorder.start_time and recorder.end_time:
                    duration = int((recorder.end_time - recorder.start_time).total_seconds())

                # Determine call status based on whether the user joined
                if call_state["user_joined"]:
                    call_status = "Completed"
                else:
                    call_status = "Incomplete"

                # 6. Run unified analysis to generate summary, stage transition, and metadata
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

                        # Save summary locally
                        try:
                            summary_path = os.path.join(session_dir, "summary.txt")
                            with open(summary_path, "w") as f:
                                f.write(summary_text)
                            logger.info(f"Summary saved: {summary_path}")
                        except Exception as e:
                            logger.error(f"Summary save failed: {e}")
                    else:
                        logger.warning("Skipping analysis: LLM or history unavailable after session close")
                except Exception as e:
                    logger.error(f"Analysis or summary generation failed: {e}", exc_info=True)

                # 7. Build webhook payload
                webhook_payload = {
                    "event": "CALL_DATA_UPDATE",
                    "data": {
                        "client_id": call_payload.get("lead_id"),
                        "call_id": call_payload.get("call_id") or call_payload.get("voice_id"),
                        "call_status": call_status,
                        "status": call_status,
                        "call_transcript": transcript_data,
                        "ai_summary": summary_text,
                        "summary": summary_text,
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
                        "client_phone": call_payload.get("client_phone") or call_payload.get("phone"),
                        "trunk_id": call_payload.get("trunk_id"),
                        "url": ""
                    }
                }

                # 8. Save webhook payload locally
                try:
                    wh_path = os.path.join(session_dir, "webhook_payload.json")
                    with open(wh_path, "w") as f:
                        json.dump(webhook_payload, f, indent=2)
                    logger.info(f"Webhook payload saved: {wh_path}")
                except Exception as e:
                    logger.error(f"Webhook payload save failed: {e}")

            except Exception as e:
                logger.error(f"Pipeline error in finalize: {e}", exc_info=True)

            # 7. Send to MantraAssist backend (always attempted, even if pipeline failed)
            try:
                if webhook_payload is None:
                    webhook_payload = {
                        "event": "CALL_DATA_UPDATE",
                        "data": {
                            "ai_call_id": ctx.job.id,
                            "call_status": "Error",
                            "status": "Error",
                            "notes": "Post-call pipeline encountered an error — minimal payload sent",
                        }
                    }

                # logger.info(f"{Fore.MAGENTA}Delivering post-call webhook to backend...{Style.RESET_ALL}")
                # logger.info(f"{Fore.CYAN}Webhook Payload:\n{json.dumps(webhook_payload, indent=2)}{Style.RESET_ALL}")
                logger.info("Delivering post-call webhook to backend...")
                logger.info(f"Webhook Payload:\n{json.dumps(webhook_payload)}")
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

        await asyncio.shield(finalize())

async def _force_disconnect_room(ctx: JobContext):
    """Delete the room via LiveKit API. Falls back to local disconnect."""
    try:
        lk_api = api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL"),
            api_key=os.getenv("LIVEKIT_API_KEY"),
            api_secret=os.getenv("LIVEKIT_API_SECRET")
        )
        await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        await lk_api.aclose()
        logger.info(f"Room {ctx.room.name} deleted via API.")
    except Exception as e:
        logger.error(f"Failed to delete room via API: {e}")
        try:
            await ctx.room.disconnect()
            logger.info(f"Room {ctx.room.name} disconnected locally.")
        except Exception as e2:
            logger.error(f"Local disconnect also failed: {e2}")

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
