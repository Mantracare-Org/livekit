import logging
import json
import asyncio
import os
import datetime
from mantra.email_alerts import send_crash_email
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



logging.basicConfig(level=logging.DEBUG, handlers=[_handler])
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
from mantra.utils import SessionRecorder, upload_to_s3, send_to_backend, normalize_to_iso8601, save_call_log_to_db



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

class AssistantFunctions:
    def __init__(self, job_metadata: str, room_name: str):
        self.job_metadata = job_metadata
        self.room_name = room_name
        self.handoff_triggered = False
        self.agent = None

    # @llm.function_tool(description="Transfer the call to a human assistant when requested or if the issue is too complex.")
    # async def transfer_to_human(
    #     self,
    #     reason: Annotated[str, "The reason why a human is needed"]
    # ):
    #     logger.info(f"Handoff requested. Reason: {reason}")
    #     self.handoff_triggered = True
    #     
    #     # Parse metadata to get call/lead IDs
    #     try:
    #         payload = json.loads(self.job_metadata) if self.job_metadata else {}
    #     except Exception:
    #         payload = {}
    # 
    #     # Notify backend
    #     webhook_payload = {
    #         "event": "HANDOFF_REQUESTED",
    #         "data": {
    #             "room_name": self.room_name,
    #             "reason": reason,
    #             "call_id": payload.get("call_id") or payload.get("voice_id"),
    #             "lead_id": payload.get("lead_id"),
    #             "client_name": payload.get("client_name", "User"),
    #         }
    #     }
    #     await send_to_backend(webhook_payload)
    #     
    #     if self.agent:
    #         logger.info("Handoff triggered — switching to passive monitoring instructions")
    #         await self.agent.update_instructions(
    #             "A human has joined the call. You are now in PASSIVE MONITORING MODE. "
    #             "DO NOT speak. DO NOT respond to the user. DO NOT generate any audio. "
    #             "Just observe and maintain the transcript for the final summary."
    #         )
    # 
    #     return "I am connecting you to a human assistant now. Please stay on the line. I will remain on the call to record and summarize our conversation."

@server.rtc_session(agent_name="mantra-agent")
async def entrypoint(ctx: JobContext):
    entrypoint_start_time = asyncio.get_event_loop().time()
    # Plain log to verify entrypoint is reached
    logger.info(f"Entrypoint reached for room: {ctx.room.name}")
    
    await ctx.connect()

    # logger.info(f"{Fore.GREEN}➕ Room Created / Connected: {ctx.room.name}{Style.RESET_ALL}")
    logger.info(f"--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # Initialize function context for handoff
    fnc_ctx = AssistantFunctions(ctx.job.metadata, ctx.room.name)
    call_state = {
        "user_joined": False,
        "timeline": [{"event": "Agent Session Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}]
    }

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Parse metadata to get call_id safely
    call_id = ctx.job.id
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            call_id = payload.get("call_id") or payload.get("voice_id") or ctx.job.id
        except:
            pass

    # Ensure call is tracked in Redis (critical for inbound calls that bypass the queue)
    try:
        redis_url = os.getenv("REDIS_URL")
        import redis.asyncio as redis
        r = redis.from_url(redis_url, decode_responses=True)
        # If it's an inbound call, it won't be in the hash yet.
        is_tracked = await r.hexists("calls:active", call_id)
        if not is_tracked:
            CARTESIA_MAX_CONCURRENCY = int(os.getenv("CARTESIA_MAX_CONCURRENCY", "5"))
            active_count = await r.hlen("calls:active")
            if active_count >= CARTESIA_MAX_CONCURRENCY:
                logger.warning(f"Capacity full ({active_count}/{CARTESIA_MAX_CONCURRENCY}). Rejecting inbound call {call_id}.")
                await r.aclose()
                await ctx.room.disconnect()
                return
            await r.hset("calls:active", call_id, ctx.room.name)
            await r.set(f"calls:status:{call_id}", "in_progress_inbound")
            logger.info(f"Registered inbound call {call_id} in Redis calls:active")
        await r.aclose()
    except Exception as e:
        logger.error(f"Failed to register active call in Redis: {e}")

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

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        call_state["timeline"].append({"event": "Remote Participant Disconnected", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
        logger.info(f"Participant {participant.identity} disconnected. Force-ending call.")
        asyncio.create_task(_force_disconnect_room(ctx))

    initial_instructions = """You are a warm, polite, and empathetic Care Support Assistant on a phone call.

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

POLITENESS & EMPATHY:
- Always be polite, courteous, and respectful.
- Show genuine empathy and understanding. Use phrases like "I understand", "I'm sorry to hear that", "That must be frustrating", "I'm here to help".
- Be patient and kind, even if the user seems confused or annoyed.
- Use a warm, caring, and reassuring tone.
- Never be rude, dismissive, or impatient.

ENDING THE CALL:
- When the user says "bye", "goodbye", "thank you and bye", "that's all", "I'm done", "disconnect", "hang up", "end the call", "end this call", or any similar farewell or disconnect request, give a brief, warm goodbye and STOP.
- If the user explicitly asks you to disconnect or end the call, comply immediately with a brief goodbye.
- Do NOT ask follow-up questions after the user indicates they want to end the call.
- Your final response should be something like: "Thank you for calling. Take care and goodbye!" — short and final.
- Do NOT extend the conversation once the purpose is fulfilled or the user says goodbye.

PRONUNCIATION (CRITICAL):
- ALWAYS write the brand name as "MantraCare" (as a single word). NEVER write "Mantra Care" with a space.
- ALWAYS write "MantraAssist" (as a single word). NEVER write "Mantra Assist" with a space.
- These are spoken brand names on a phone call — single-word format ensures correct pronunciation.

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
    # Collect Cartesia API keys dynamically from environment variables
    cartesia_keys = []
    for key_env in ["CARTESIA_API_KEY", "CARTESIA_API_KEY_2", "CARTESIA_API_KEY_3"]:
        key_val = os.getenv(key_env)
        if key_val:
            if "," in key_val:
                cartesia_keys.extend([k.strip() for k in key_val.split(",") if k.strip()])
            else:
                cartesia_keys.append(key_val.strip())
    
    # If no keys are specified in variables, let it default to standard CARTESIA_API_KEY logic
    if not cartesia_keys:
        cartesia_keys = [None]

    language = "en"
        
    if language:
        language = str(language).lower()

    # Diagnostic: log the resolved TTS language so we can verify in production
    logger.info(f"TTS Language resolved to: '{language}' (None means auto-detect)")
    logger.info(f"TTS Keys count: {len(cartesia_keys)}")
    logger.info(f"TTS Model: sonic-3 | Voice: {voice_id} | Speed: {voice_speed}")

    # Setup Fallback TTS using the pool of keys to cycle on rate limits (429) / connection failures
    tts_pool = [
        cartesia.TTS(
            model="sonic-3",
            voice=voice_id,
            speed=voice_speed,
            language=language,
            api_key=api_key,
        )
        for api_key in cartesia_keys
    ]

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.2,
                "max_delay": 0.8,
            },
            interruption={
                "mode": "vad",
                "resume_false_interruption": True,
                "false_interruption_timeout": 0.5,
                "min_words": 1,
            },
            preemptive_generation={
                "preemptive_tts": True,
            },
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.08,
            min_silence_duration=0.15,
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
        tools=[] # [fnc_ctx.transfer_to_human] commented out
    )
    fnc_ctx.agent = agent

    @session.on("agent_state_changed")
    def on_agent_state(ev):
        call_state["agent_state"] = ev.new_state
        if getattr(ev, "old_state", None) == "speaking" and ev.new_state != "speaking":
            call_state["last_activity"] = asyncio.get_event_loop().time()

    @session.on("user_state_changed")
    def on_user_state(ev):
        if ev.new_state == "speaking":
            call_state["last_activity"] = asyncio.get_event_loop().time()
            call_state["prompted_inactivity"] = False

    async def inactivity_monitor():
        logger.info("Inactivity monitor started.")
        while not call_state.get("user_joined"):
            await asyncio.sleep(1.0)
            
        call_state["last_activity"] = asyncio.get_event_loop().time()
        call_state["prompted_inactivity"] = False
        
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.0)
            now = asyncio.get_event_loop().time()
            agent_state = call_state.get("agent_state", "initializing")
            last_activity = call_state.get("last_activity", now)
            
            time_since_activity = now - last_activity
            
            if agent_state in ["listening", "idle"]:
                if time_since_activity > 10.0:
                    # logger.warning(f"{Fore.YELLOW}No response for 10s. Destroying room.{Style.RESET_ALL}")
                    call_state["timeline"].append({"event": "Inactivity Timeout Disconnect", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
                    asyncio.create_task(_force_disconnect_room(ctx))
                    break
                elif time_since_activity > 5.0 and not call_state.get("prompted_inactivity", False):
                    logger.info("No response for 5s. Prompting user...")
                    call_state["prompted_inactivity"] = True
                    try:
                        session.generate_reply(
                            user_input="[System: The user has been silent. Briefly ask if they are still there (e.g. 'Are you still there?' or 'Hello?'). Keep it extremely short.]"
                        )
                    except RuntimeError as e:
                        logger.warning(f"Failed to generate inactivity reply (session may be closing): {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error generating inactivity reply: {e}")

    GOODBYE_TRIGGERS = ["bye", "goodbye", "take care", "bye bye", "that will be all", "that's all", "talk to you later", "disconnect", "hang up", "end the call", "end this call"]

    async def goodbye_monitor():
        logger.info("Goodbye monitor started.")
        await asyncio.sleep(5.0)
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.5)
            try:
                messages = list(session.history.messages())
                if not messages:
                    continue
                last_agent_text = ""
                for msg in reversed(messages):
                    role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
                    if role.lower() == "assistant":
                        last_agent_text = (msg.text if hasattr(msg, "text") else str(msg.content)).lower()
                        break
                if not last_agent_text:
                    continue
                if any(t in last_agent_text for t in GOODBYE_TRIGGERS):
                    last_agent_text_sent = call_state.get("last_goodbye_checked", "")
                    if last_agent_text != last_agent_text_sent:
                        call_state["last_goodbye_checked"] = last_agent_text
                        logger.info("Goodbye detected in agent response. Waiting 4s before disconnect.")
                        await asyncio.sleep(4.0)
                        if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                            call_state["timeline"].append({
                                "event": "Natural Call End - Trigger Words",
                                "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                            })
                            logger.info("Ending call after natural goodbye.")
                            asyncio.create_task(_force_disconnect_room(ctx))
                            break
            except Exception as e:
                logger.debug(f"Goodbye monitor check error: {e}")

    # Call duration limiter logic
    async def call_limiter():
        logger.info("Call limiter started — waiting for remote participant to join.")
        try:
            # Wait for remote participant to join before starting the 2m/3m timers
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(1.0)
            
            logger.info("Remote participant detected in room.")
            elapsed = asyncio.get_event_loop().time() - entrypoint_start_time
            logger.info(
                f"Participant joined at t={elapsed:.2f}s. "
                f"Setting limiter timers: "
                f"Farewell reply in {max(0.0, 150.0-elapsed):.2f}s, "
                f"Hard kill in {max(0.0, 180.0-elapsed):.2f}s."
            )
            
            # Event that lets us cancel the force-disconnect if the call ends naturally
            _force_disconnect_cancelled = asyncio.Event()

            async def force_disconnect_timer():
                try:
                    disconnect_delay = max(0.0, 180.0 - (asyncio.get_event_loop().time() - entrypoint_start_time))
                    logger.info(f"Force-disconnect timer armed: t+{disconnect_delay:.2f}s")
                    await asyncio.wait_for(_force_disconnect_cancelled.wait(), timeout=disconnect_delay)
                except asyncio.TimeoutError:
                    pass  # Timeout expired — proceed to disconnect
                except asyncio.CancelledError:
                    logger.info("Force-disconnect timer cancelled.")
                    return  # Cancelled — exit cleanly
                else:
                    logger.info("Call ended naturally — force-disconnect timer exiting.")
                    return  # Event was set — call ended naturally, exit cleanly

                if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                    logger.warning("HARD DISCONNECT: 3m limit reached. Force disconnecting room.")
                    call_state["timeline"].append({"event": "Max Call Duration Reached", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
                    await _force_disconnect_room(ctx)
                else:
                    logger.info("Room already disconnected — force-disconnect skipping.")

            disconnect_task = asyncio.create_task(force_disconnect_timer())

            # Stage 1: 2m 30s mark — update agent instructions for a natural farewell
            # We do NOT call generate_reply() here, so the agent won't interrupt the user.
            # The updated instructions are picked up on the agent's next natural turn.
            stage1_delay = max(0.0, 150.0 - (asyncio.get_event_loop().time() - entrypoint_start_time))
            await asyncio.sleep(stage1_delay)
            elapsed = asyncio.get_event_loop().time() - entrypoint_start_time
            logger.info(f"Farewell stage hit at t={elapsed:.2f}s")
            
            if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                logger.info("Updating agent instructions for farewell.")
                current_inst = agent.instructions
                if isinstance(current_inst, str):
                    farewell_inst = (
                        "IMPORTANT: The call time is ending now. "
                        "On your next turn, say a quick, natural one-sentence goodbye "
                        "and do not continue the conversation. Do not ask questions."
                    )
                    await agent.update_instructions(current_inst + "\n\n" + farewell_inst)
                logger.info("Farewell instructions set.")
                
                try:
                    logger.info("Waiting for session to become inactive (25s timeout).")
                    await asyncio.wait_for(session.wait_for_inactive(), timeout=25.0)
                    logger.info("Session became inactive naturally.")
                except asyncio.TimeoutError:
                    logger.warning("Session did not go inactive within 25s — force-disconnect at 3m will handle it.")
            else:
                logger.warning("Room already disconnected — skipping farewell.")
        except asyncio.CancelledError:
            logger.info("Call limiter cancelled (call ended naturally before limits).")
            try:
                _force_disconnect_cancelled.set()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error in call limiter: {e}")

    try:
        await session.start(agent=agent, room=ctx.room)
        limiter_task = asyncio.create_task(call_limiter())
        inactivity_task = asyncio.create_task(inactivity_monitor())
        goodbye_task = asyncio.create_task(goodbye_monitor())
        
        # Check if agent track was already published before we attached the listener
        for publication in ctx.room.local_participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                recorder.start_recording(publication.track, "agent")

        # Check if remote tracks were already subscribed before we attached the listener
        for participant in ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                    recorder.start_recording(publication.track, f"participant_{participant.identity}")
        
        if ctx.room.name.startswith("test_"):
            logger.info("Test room detected. Skipping wait for remote participant to initialize synthesis.")
            call_state["user_joined"] = True
        else:
            logger.info("Waiting for remote participant to join...")
            wait_start = asyncio.get_event_loop().time()
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(0.5)
                if asyncio.get_event_loop().time() - wait_start > 60.0:
                    logger.warning("Remote participant did not join within 60 seconds (likely no answer). Disconnecting.")
                    await _force_disconnect_room(ctx)
                    return
                
            logger.info("Remote participant joined. Initializing conversation...")
            call_state["user_joined"] = True
            call_state["timeline"].append({"event": "Remote Participant Joined", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
            await asyncio.sleep(0.5)
        
        logger.info(f"Generating greeting for {client_name}...")
        try:
            session.generate_reply(instructions=f"Greet the user named {client_name} and follow the opening script in your instructions.")
            logger.info("Greeting generation requested.")
        except RuntimeError as e:
            logger.warning(f"Could not generate greeting (session may be closed): {e}")
        
        # Block until the room connection drops or the session closes
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.0)

    except asyncio.CancelledError:
        logger.info("Call entrypoint coroutine cancelled.")
    except Exception as e:
        logger.error(f"Error in entrypoint execution: {e}", exc_info=True)
        context_data = {
            "Room Name": getattr(ctx.room, "name", "N/A"),
            "Job ID": getattr(ctx.job, "id", "N/A"),
            "Process ID (PID)": os.getpid()
        }
        try:
            if ctx.job.metadata:
                context_data["Job metadata"] = ctx.job.metadata
        except:
            pass
        # Do not block main exception handling logic, the email function handles to_thread internally
        try:
            await send_crash_email(service_name="Livekit Voice Agent worker", error=e, context_data=context_data)
        except Exception as email_err:
            logger.error(f"Failed to dispatch crash email: {email_err}")
    finally:
        logger.info("Entering entrypoint finally block (cleaning up and finalizing)...")
        # 1. Cancel background tasks
        for task_name in ['limiter_task', 'inactivity_task', 'goodbye_task']:
            task = locals().get(task_name)
            if task and not task.done():
                task.cancel()
            
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
                if "timeline" in call_state:
                    call_state["timeline"].append({"event": "Call Finalization Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})

                # 1. Pre-load call metadata
                try:
                    call_payload = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
                except Exception as e:
                    logger.error(f"Failed to parse metadata: {e}")
                    call_payload = {}

                # Determine call status based on whether the user joined and actually spoke
                user_spoke = False
                for msg in history_snapshot:
                    role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
                    if role.lower() == "user":
                        user_spoke = True
                        break

                call_id = call_payload.get("call_id") or call_payload.get("voice_id") or ctx.job.id
                
                # Always check if there was a SIP-level error in Redis first
                redis_status = None
                try:
                    redis_url = os.getenv("REDIS_URL")
                    import redis.asyncio as redis
                    r = redis.from_url(redis_url, decode_responses=True)
                    redis_status = await r.get(f"sip_error_status:{call_id}")
                    await r.aclose()
                except Exception as redis_err:
                    logger.error(f"Failed to fetch precise SIP status from Redis: {redis_err}")
                
                if redis_status:
                    # If UI server caught a SIP error (Busy/No Answer), trust it completely
                    call_status = redis_status
                elif not call_state["user_joined"]:
                    # Fallback: if it waited less than 25s before terminating, it's Busy/Rejected.
                    # If it waited more than 25s, it's a No Answer timeout.
                    elapsed_time = asyncio.get_event_loop().time() - entrypoint_start_time
                    if elapsed_time >= 25.0:
                        call_status = "No Answer"
                    else:
                        call_status = "Busy"
                elif not user_spoke:
                    call_status = "No Answer"
                else:
                    call_status = "Completed"

                # 2. Flush recording tasks and build recording
                try:
                    await recorder.stop_recording()
                    if call_status == "Completed":
                        mp3_bytes = recorder.get_combined_mp3_bytes()
                        if mp3_bytes:
                            call_id = call_payload.get("call_id") or call_payload.get("voice_id") or ctx.job.id
                            s3_key = f"recordings/{call_id}.mp3"
                            loop = asyncio.get_running_loop()
                            recording_url = await loop.run_in_executor(None, upload_to_s3, mp3_bytes, s3_key) or ""
                            logger.info(f"S3 recording: {'uploaded' if recording_url else 'upload failed'}")
                        else:
                            logger.info("No audio data captured for recording")
                    else:
                        logger.info(f"Skipping recording upload because call_status is {call_status}")
                except Exception as e:
                    logger.error(f"Recording/S3 step failed: {e}", exc_info=True)

                # 3. Build transcript from captured history snapshot
                try:
                    transcript_data = SessionRecorder.build_transcript(list(history_snapshot))
                    logger.info(f"Transcript built ({len(history_snapshot)} messages)")
                except Exception as e:
                    logger.error(f"Transcript step failed: {e}", exc_info=True)

                # 4. Calculate duration
                if hasattr(recorder, "recording_duration_seconds"):
                    duration = int(recorder.recording_duration_seconds)
                else:
                    duration = 0


                # 5. Run unified analysis to generate summary, stage transition, and metadata
                current_stage_id = call_payload.get("stage_id")
                stage_details = call_payload.get("stageDetails", [])
                
                summary_text = ""
                new_stage_id = current_stage_id
                next_call_on = None
                client_custom_fields = call_payload.get("client_custom_fields", {})
                if not isinstance(client_custom_fields, dict):
                    client_custom_fields = {}

                if call_status in ["Busy", "Incomplete", "No Answer"]:
                    logger.info(f"Call status is {call_status}. Skipping LLM analysis and applying 'Not Answering' logic.")
                    summary_text = f"Call failed with status: {call_status}. The user did not speak or answer."
                    duration = 0
                    not_answering_id = current_stage_id
                    for stage in stage_details:
                        desc = stage.get("description", "").lower()
                        if "not answering" in desc or "failed" in desc or "incomplete" in desc or "busy" in desc:
                            not_answering_id = stage.get("stage_id")
                            break
                    new_stage_id = not_answering_id
                    
                    # Set next_call_on to 24 hours from now
                    current_time = datetime.datetime.now()
                    tomorrow = current_time + datetime.timedelta(hours=24)
                    next_call_on = tomorrow.strftime("%Y-%m-%d %H:%M:%S")
                else:
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
                        "url": "",
                        "timeline": call_state.get("timeline", [])
                    }
                }

            except Exception as e:
                logger.error(f"Pipeline error in finalize: {e}", exc_info=True)

            # 8. Send to MantraAssist backend and save to local DB
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

                # Save to local Postgres DB
                try:
                    c_id = webhook_payload.get("data", {}).get("call_id", ctx.job.id)
                    await save_call_log_to_db(
                        call_id=str(c_id),
                        call_log=json.dumps(webhook_payload.get("data", {}), indent=2),
                        status=call_status,
                        recording_url=recording_url
                    )
                except Exception as db_err:
                    logger.error(f"Error calling save_call_log_to_db: {db_err}")

                logger.info("Delivering post-call webhook to backend...")
                logger.info(f"Webhook Payload:\n{json.dumps(webhook_payload)}")
                delivered = await send_to_backend(webhook_payload)
            except Exception as e:
                logger.error(f"Webhook delivery failed: {e}", exc_info=True)
                delivered = False

            # 9. Free Cartesia Capacity slot in Redis
            try:
                call_id = call_payload.get("call_id")
                if call_id:
                    redis_url = os.getenv("REDIS_URL")
                    import redis.asyncio as redis
                    r = redis.from_url(redis_url, decode_responses=True)
                    await r.hdel("calls:active", call_id)
                    await r.set(f"calls:status:{call_id}", "completed")
                    await r.aclose()
                    logger.info(f"Freed capacity slot for call {call_id} in Redis")
            except Exception as e:
                logger.error(f"Failed to free Redis capacity slot: {e}")

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
        # logger.info(f"{Fore.RED}➖ Room Destroyed via API: {ctx.room.name}{Style.RESET_ALL}")
    except Exception as e:
        logger.error(f"Failed to delete room via API: {e}")
        try:
            await ctx.room.disconnect()
            # logger.info(f"{Fore.RED}➖ Room Disconnected locally: {ctx.room.name}{Style.RESET_ALL}")
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
