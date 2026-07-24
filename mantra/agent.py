import logging
import json
import asyncio
import os
import datetime
import aiohttp
from mantra.email_alerts import send_crash_email
import sys

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
_handler.setFormatter(
    ColorFormatter(
        f"%(asctime)s INFO (Type: {_proc_type}, PID: {os.getpid()}) %(name)s: %(message)s"
    )
)


logging.basicConfig(level=logging.DEBUG, handlers=[_handler])
logger = logging.getLogger("mantra.agent")
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)
logger.info("Initializing process...")

# Also suppress noisy OTEL SDK logs once the SDK initialises
logging.getLogger("opentelemetry").setLevel(logging.ERROR)

from dotenv import load_dotenv

from livekit import rtc, api
from livekit.agents import mcp as lk_mcp
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    inference,
    llm,
)
from livekit.agents import TurnHandlingOptions

from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import openai, google, silero, deepgram

# Import our production helpers
from mantra.utils import (
    SessionRecorder,
    upload_to_s3,
    send_to_backend,
    normalize_to_iso8601,
    save_call_log_to_db,
)

# Import knowledge base
from mantra.knowledge_base import PostgresKnowledgeBase
from mantra.retriever import KnowledgeRetriever
from typing import Annotated, Optional


VOICE_MAPPING = {
    "gemma": "62ae83ad-4f6a-430b-af41-a9bede9286ca",
    "alistair": "c8f7835e-28a3-4f0c-80d7-c1302ac62aae",
    "sunny": "156fb8d2-335b-4950-9cb3-a2d33befec77",
    "tyler": "820a3788-2b37-4d21-847a-b65d8a68c99a",
    "vikas": "adf97b9d-905c-41de-9fe9-afb387116d06",
    "camila": "bef2ba57-5c10-433b-b215-3bef35110a81",
    "renata": "d3793b7b-4996-409c-9d59-96dd09f47717",
    "arushi": "95d51f79-c397-46f9-b49a-23763d3eaa2d",
}

# Load environment variables
load_dotenv()  # Load .env (OpenAI, etc.)
load_dotenv(
    ".env.local", override=True
)  # Load .env.local (LiveKit, etc.) and override if needed


server = AgentServer(num_idle_processes=20)

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


_bg_tasks = set()


def create_bg_task(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


_global_kb: PostgresKnowledgeBase | None = None

def get_global_kb() -> PostgresKnowledgeBase:
    global _global_kb
    if _global_kb is None:
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        _global_kb = PostgresKnowledgeBase(dsn)
    return _global_kb


async def _resolve_from_db(phone_number: str) -> dict | None:
    """
    Look up inbound call context from the PostgreSQL org_configs table.
    """
    try:
        clean_number = phone_number.replace("+", "")
        kb = get_global_kb()
        pool = await kb._get_pool()
        
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM org_configs WHERE phone_number IN ($1, $2) AND is_active = true",
                phone_number, clean_number
            )
            
        if row:
            result = dict(row)
            if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
                try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
                except: pass
            
            # The agent expects a certain dictionary format, let's make sure it matches what the backend would return
            return {
                "org_id": result.get("org_id"),
                "kb_id": result.get("org_id"), # In our design, kb_id is org_id
                "kb_tags": result.get("kb_tags", []),
                "prompt": result.get("prompt"),
                "voice": result.get("voice"),
                "model": result.get("model"),
                "process_id": result.get("process_id"),
                "transfer_numbers": result.get("transfer_numbers", {}),
                "client_name": result.get("client_name")
            }
        return None
    except Exception as e:
        logger.error(f"Failed to query DB for phone number {phone_number}: {e}")
        return None


async def _resolve_from_mantra_backend(phone_number: str) -> dict | None:
    """
    Call MantraAssist backend to resolve inbound call context from the dialed phone number.
    Returns org_id, kb_id, kb_tags, prompt, voice, model, process_id, transfer_numbers, client_name.
    Returns None if the backend is unreachable or returns an error.
    """
    base_url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
    if not base_url:
        logger.error("MANTRAASSIST_BACKEND_URL not set — cannot resolve inbound call context")
        return None

    url = f"{base_url}/api/v1/telephony/resolve-inbound-call"
    logger.info(f"Resolving inbound call context for phone_number={phone_number} via {url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"phone_number": phone_number},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        f"Resolved inbound context: org_id={data.get('org_id')}, "
                        f"kb_id={data.get('kb_id')}, kb_tags={data.get('kb_tags')}"
                    )
                    return data
                else:
                    resp_text = await resp.text()
                    logger.error(
                        f"MantraAssist resolve-inbound-call returned {resp.status}: {resp_text}"
                    )
                    return None
    except asyncio.TimeoutError:
        logger.error("MantraAssist resolve-inbound-call timed out (10s)")
        return None
    except Exception as e:
        logger.error(f"Failed to resolve inbound call context: {e}")
        return None


async def resolve_inbound_context(phone_number: str) -> dict | None:
    """
    Resolves inbound call context, checking DB first and falling back to MantraAssist backend.
    """
    # 1. Try DB first (fast path)
    config = await _resolve_from_db(phone_number)
    if config:
        logger.info(f"Resolved inbound context from DB for {phone_number} (org_id: {config.get('org_id')})")
        return config
    
    # 2. Fall back to MantraAssist API (supplementary path)
    logger.info(f"DB miss — falling back to MantraAssist API for {phone_number}")
    return await _resolve_from_mantra_backend(phone_number)


class AssistantFunctions:
    def __init__(
        self,
        job_metadata: str,
        room_name: str,
        ctx: JobContext = None,
        kb_ids: list[str] = None,
        kb_tags: list[str] = None,
    ):
        self.job_metadata = job_metadata
        self.room_name = room_name
        self.handoff_triggered = False
        self.agent = None
        self.session = None
        self.ctx = ctx
        self.kb_ids = kb_ids or []
        self.kb_tags = kb_tags or []
        self._retriever: KnowledgeRetriever | None = None

    async def _get_kb(self) -> PostgresKnowledgeBase:
        return get_global_kb()

    async def _get_retriever(self) -> KnowledgeRetriever:
        if self._retriever is None:
            kb = await self._get_kb()
            self._retriever = KnowledgeRetriever(kb)
        return self._retriever

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

        # Guard: prevent duplicate transfers if LLM calls this twice
        if self.handoff_triggered:
            logger.warning("Handoff already in progress — ignoring duplicate request")
            return "TRANSFER_ALREADY_IN_PROGRESS."

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

        # Notify backend (skip if no URL configured)
        if os.getenv("MANTRAASSIST_BACKEND_URL"):
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

        # Interrupt any in-progress speech from the agent
        try:
            if self.agent and self.agent._session:
                self.agent._session.interrupt()
                logger.info("Agent speech interrupted for handoff")
        except Exception as e:
            logger.debug(f"Agent interrupt unavailable (non-fatal): {e}")

        return "TRANSFER_COMPLETE. Do not speak."

    @llm.function_tool(
        description="Search the knowledge base for factual information relevant to the user's question. Use this tool to retrieve accurate information about products, services, policies, procedures, pricing, locations, schedules, people, organizations, documents, regulations, FAQs, or any domain-specific content stored in the knowledge base. ALWAYS use this tool before answering questions that require factual or organization-specific information. If the user switches topics to a specific category (like 'support' or 'pricing'), you can provide that category in 'specific_tag' to override the default search scope."
    )
    async def search_knowledge_base(
        self, 
        query: Annotated[str, "The search query to look up in the knowledge base. Be specific, e.g., 'What are the symptoms of diabetes?' or 'How many paid leaves do I get?'"],
        specific_tag: Annotated[Optional[str], "An optional specific tag or category to search within (e.g., 'sales', 'support', 'pricing') if the user explicitly switches context. Leaves empty to search the default context."] = None
    ):
        tags_to_search = [specific_tag] if specific_tag else self.kb_tags
        logger.info(f"Agent requested knowledge base search for: '{query}' with tags {tags_to_search}")
        retriever = await self._get_retriever()
        result = await retriever.retrieve(query, kb_ids=self.kb_ids, tags=tags_to_search if tags_to_search else None)
        return result

    @llm.function_tool(
        description="End the call. Call this tool when the conversation is over — the user said goodbye, is not interested, or there is nothing left to discuss."
    )
    async def end_call(self):
        logger.info(
            "Agent decided to end the call via function tool. Waiting for speech to finish before disconnecting."
        )

        async def graceful_disconnect():
            if self.session:
                try:
                    await asyncio.wait_for(
                        self.session.wait_for_inactive(), timeout=12.0
                    )
                    logger.info(
                        "Agent finished speaking. Pausing briefly before disconnect."
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Agent did not finish speaking within 12s. Disconnecting anyway."
                    )
            else:
                await asyncio.sleep(4.0)
            await asyncio.sleep(1.0)
            if self.ctx:
                await _force_disconnect_room(self.ctx)

        self._disconnect_task = create_bg_task(graceful_disconnect())
        return "Call is ending. Say a brief, warm goodbye now."

    # Removed query_knowledge_base tool as per user request to inject KB directly into the main job

    # @llm.ai_callable(description="Transfer the call to a human assistant when requested or if the issue is too complex.")
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

    # Will be populated with the effective (enriched) metadata for use in finalize()
    _effective_call_metadata: dict = {}

    await ctx.connect()

    # logger.info(f"{Fore.GREEN}➕ Room Created / Connected: {ctx.room.name}{Style.RESET_ALL}")
    logger.info("--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # ── Inbound Call Context Resolution ──────────────────────────────────
    # For inbound calls, we must call MantraAssist to resolve the org context
    # BEFORE extracting KB scope, so the agent knows which org's KB to search.
    kb_ids_list = []
    kb_tags_list = []
    resolved_context = None

    if ctx.job.metadata:
        try:
            meta_payload = json.loads(ctx.job.metadata)

            # Detect inbound call and resolve context from MantraAssist
            if meta_payload.get("direction") == "inbound":
                phone_number = meta_payload.get("phone_number", "")
                if phone_number:
                    resolved_context = await resolve_inbound_context(phone_number)

                    if resolved_context is None:
                        logger.error(
                            f"Cannot resolve inbound call context for {phone_number}. "
                            "Rejecting call — MantraAssist is unreachable."
                        )
                        try:
                            await ctx.room.disconnect()
                        except Exception:
                            pass
                        return

                    # Merge resolved data into metadata so downstream code picks it up
                    # (prompt, voice, model, process_id, client_name, transfer_numbers, org_id, etc.)
                    meta_payload.update(resolved_context)
                    logger.info(
                        f"Merged inbound context into metadata for org_id={resolved_context.get('org_id')}"
                    )
                else:
                    logger.warning("Inbound call has no phone_number in metadata — cannot resolve context")

            # Extract KB scope from (potentially enriched) metadata
            # org_id is always used as a kb_id (data is ingested with kb_id=org_id)
            if meta_payload.get("org_id"):
                kb_ids_list.append(meta_payload["org_id"])

            # If a specific kb_id is also provided, add it for additional scope
            if "kb_id" in meta_payload and meta_payload["kb_id"]:
                if meta_payload["kb_id"] not in kb_ids_list:
                    kb_ids_list.append(meta_payload["kb_id"])
            if "kb_ids" in meta_payload and isinstance(meta_payload["kb_ids"], list):
                kb_ids_list.extend(meta_payload["kb_ids"])

            if "kb_tags" in meta_payload and isinstance(meta_payload["kb_tags"], list):
                kb_tags_list.extend(meta_payload["kb_tags"])

            # Remove duplicates
            kb_ids_list = list(set(kb_ids_list))
            kb_tags_list = list(set(kb_tags_list))
        except Exception as e:
            logger.error(f"Failed to parse/resolve metadata: {e}")

    logger.info(f"KB scope: kb_ids={kb_ids_list}, kb_tags={kb_tags_list}")

    fnc_ctx = AssistantFunctions(
        ctx.job.metadata,
        ctx.room.name,
        ctx=ctx,
        kb_ids=kb_ids_list,
        kb_tags=kb_tags_list,
    )
    call_state = {
        "user_joined": False,
        "timeline": [
            {
                "event": "Agent Session Started",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
        ],
    }

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Parse metadata to get call_id safely
    call_id = ctx.job.id
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            _effective_call_metadata = dict(payload)
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
            MAX_CONCURRENCY = int(
                os.getenv("MAX_CONCURRENCY", os.getenv("CARTESIA_MAX_CONCURRENCY", "5"))
            )
            active_count = await r.hlen("calls:active")
            if active_count >= MAX_CONCURRENCY:
                logger.warning(
                    f"Capacity full ({active_count}/{MAX_CONCURRENCY}). Rejecting inbound call {call_id}."
                )
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
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, f"participant_{participant.identity}")

    @ctx.room.on("local_track_published")
    def on_local_track_published(
        publication: rtc.LocalTrackPublication, track: rtc.Track
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, "agent")

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        call_state["timeline"].append(
            {
                "event": "Remote Participant Disconnected",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
        )
        logger.info(
            f"Participant {participant.identity} disconnected. Force-ending call."
        )
        create_bg_task(_force_disconnect_room(ctx))

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
- KNOWLEDGE BASE USAGE: If the user asks a factual question or inquires about policies, services, or locations, you MUST use the `search_knowledge_base` tool to find the accurate answer.

TRANSFER CAPABILITY (CRITICAL):
- You HAVE a function called "transfer_to_human" that transfers the call to a real human agent.
- When the user asks to speak to a human, you cannot resolve their issue, or they seem frustrated — USE the transfer_to_human function IMMEDIATELY. Do NOT say you cannot transfer. You CAN transfer. Use the function.
- If the user mentions a specific department (refund, billing, support), pass it as the department parameter. Otherwise use "general".
- After the function executes, you will be muted. Say nothing. The human takes over.

POLITENESS & EMPATHY:
- Always be polite, courteous, and respectful.
- Show genuine empathy and understanding. Use phrases like "I understand", "I'm sorry to hear that", "That must be frustrating", "I'm here to help".
- Be patient and kind, even if the user seems confused or annoyed.
- Use a warm, caring, and reassuring tone.
- Never be rude, dismissive, or impatient.

ENDING THE CALL (CRITICAL — YOU MUST FOLLOW THIS):
- You have a tool called `end_call`. You MUST call this tool to end every call. There is NO other way to hang up.
- NEVER say goodbye, farewell, or any closing statement WITHOUT FIRST calling the `end_call` tool. Saying "goodbye" or "take care" without calling the tool means the call stays connected forever. This is a critical failure.
- Call `end_call` IMMEDIATELY when ANY of these happen:
  * The user says bye, goodbye, thank you, that's all, I'm done, not interested, hang up, disconnect, end the call, or anything similar.
  * The user explicitly declines or rejects the offer (e.g. "not interested", "no thanks", "I don't need this").
  * The conversation has reached a natural conclusion and there is nothing left to discuss.
  * The user is clearly uninterested or disengaged.
- The CORRECT sequence is: 1) Call `end_call` tool FIRST, 2) THEN say a brief warm goodbye in your response text.
- Do NOT ask follow-up questions after the user indicates they want to end the call or is not interested.
- Keep your final goodbye SHORT: "Thank you for your time, Anurag. Take care!" — that's it.
- REMEMBER: If you find yourself writing a goodbye message, you MUST also call `end_call`. No exceptions.

PRONUNCIATION (CRITICAL):
- ALWAYS write the brand name as "MantraCare" (as a single word). NEVER write "Mantra Care" with a space.
- ALWAYS write "MantraAssist" (as a single word). NEVER write "Mantra Assist" with a space.
- These are spoken brand names on a phone call — single-word format ensures correct pronunciation.

PROSODY AND TONE (CRITICAL):
- DO NOT use exclamation marks (!) or ALL CAPS in your responses.
- The voice engine uses punctuation and casing to determine volume and emotion. Exclamation marks or ALL CAPS will cause the agent to yell or shout inappropriately.
- Keep your punctuation flat (use periods and commas). Instead of "HELLO!", write "Hello." Instead of "Great!", write "Great."

Follow these specific instructions:
"""
    client_name = "User"
    is_inbound = False
    
    if ctx.job.metadata:
        try:
            # Use the enriched metadata if inbound context was resolved, otherwise parse fresh
            if resolved_context is not None:
                payload = dict(meta_payload)  # Already enriched with MantraAssist data
            else:
                payload = json.loads(ctx.job.metadata)
            _effective_call_metadata = dict(payload)  # keep for finalize()
            
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

            # If the call arrived via an external IVR (SIP header passthrough),
            # inject a dedicated context block so the LLM understands the caller's
            # origin and reason for calling without having to re-ask.
            ivr_keys = {"account_number", "call_reason", "department", "language", "user_id", "caller_choice"}
            ivr_block = ""
            for key in payload:
                if key in ivr_keys and payload[key]:
                    ivr_block += f"- {key.replace('_', ' ').title()}: {payload[key]}\n"
            
            if ivr_block:
                initial_instructions += "\n--- EXTERNAL IVR / CALLER CONTEXT ---\n"
                initial_instructions += "The caller was routed from an automated system with the following context.\n"
                initial_instructions += "DO NOT ask the user for this information again:\n"
                initial_instructions += ivr_block

            if "prompt" in payload:
                # Remove the impatient "not responding" rule which causes repetitive loops
                clean_prompt = payload["prompt"].replace(
                    "If the client is not responding, ask questions like 'hope you are hearing me', etc.",
                    "",
                )
                initial_instructions += "\n" + clean_prompt

            if "client_name" in payload:
                client_name = payload["client_name"]

            # 2. Extract ALL other features as context for the LLM
            context_header = "\n\n--- ADDITIONAL CALL CONTEXT ---\n"
            context_body = ""

            for key, value in payload.items():
                if key == "prompt":
                    continue
                
                # For inbound calls, if the client name is just "User" or missing, don't inject it to avoid "Am I speaking with User?"
                if is_inbound and key == "client_name" and (value == "User" or not value):
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
    if "payload" in locals():
        # Handle nested ai_payload if present
        ai_p = payload.get("ai_payload")
        if not isinstance(ai_p, dict):
            ai_p = {}

        # Priority for Model: ai_payload.ai_model -> payload.model -> default "openai"
        model_name = ai_p.get("ai_model") or payload.get("model") or "openai"
        model_name = str(model_name).lower()

        # Priority for Voice: ai_payload.voice_id -> payload.voice_id -> voice_name -> voice -> default "arushi"
        _raw_voice = (
            ai_p.get("voice_id")
            or payload.get("voice_id")
            or payload.get("voice_name")
            or payload.get("voice")
            or "arushi"
        )
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
                base_url="https://api.deepseek.com",
            )
    else:
        logger.info("Using OpenAI LLM")
        llm_engine = openai.LLM(model="gpt-4o-mini")
    # TTS via LiveKit Inference — no separate Cartesia API key needed.
    # LiveKit Inference authenticates using LIVEKIT_API_KEY + LIVEKIT_API_SECRET.
    # Voice UUIDs are unchanged — all standard Cartesia voices are supported.
    language = "en"

    if language:
        language = str(language).lower()

    logger.info(f"TTS Language resolved to: '{language}' (None means auto-detect)")
    logger.info("TTS Backend: LiveKit Inference (cartesia/sonic-3)")
    logger.info(f"TTS Voice: {voice_id} | Speed: {voice_speed}")

    tts_engine = inference.TTS(
        model="cartesia/sonic-3",
        voice=voice_id,
        language=language,
        extra_kwargs={
            "speed": voice_speed,
        },
    )

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.1,
                "max_delay": 0.35,
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
        stt=deepgram.STT(
            model="nova-3", language="hi", smart_format=True, numerals=True
        ),
        llm=llm_engine,
        tts=tts_engine,
    )


    # Initialize MCP Server connection
    try:
        mcp_server = lk_mcp.CstdioServerParameters(
            command="uv",
            args=["run", "python", "mcp/server.py"]
        )
        mcp_client = lk_mcp.McpClient(mcp_server)
        await mcp_client.start()
        logger.info("Connected to local MCP database server")
        # Add MCP tools to the agent's toolset dynamically
        agent_tools = [fnc_ctx.end_call, fnc_ctx.search_knowledge_base, fnc_ctx.transfer_to_human]
        agent_tools.append(mcp_client.create_tool_context())
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        agent_tools = [fnc_ctx.end_call, fnc_ctx.search_knowledge_base, fnc_ctx.transfer_to_human]

    agent = Agent(instructions=initial_instructions, tools=agent_tools)

    fnc_ctx.agent = agent
    fnc_ctx.session = session

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
                    call_state["timeline"].append(
                        {
                            "event": "Inactivity Timeout Disconnect",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        }
                    )
                    create_bg_task(_force_disconnect_room(ctx))
                    break
                elif time_since_activity > 5.0 and not call_state.get(
                    "prompted_inactivity", False
                ):
                    logger.info("No response for 5s. Prompting user...")
                    call_state["prompted_inactivity"] = True
                    try:
                        session.generate_reply(
                            user_input="[System: The user has been silent. Briefly ask if they are still there (e.g. 'Are you still there?' or 'Hello?'). Keep it extremely short.]"
                        )
                    except RuntimeError as e:
                        logger.warning(
                            f"Failed to generate inactivity reply (session may be closing): {e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Unexpected error generating inactivity reply: {e}"
                        )

    # Safety net: if the LLM says goodbye but forgets to call end_call, force disconnect
    FAREWELL_PHRASES = [
        "goodbye",
        "good bye",
        "bye bye",
        "take care",
        "have a great day",
        "have a good day",
        "have a nice day",
        "thanks for calling",
        "thank you for calling",
        "talk to you later",
        "see you later",
    ]

    async def farewell_safety_net():
        """Detect if the agent said goodbye without calling end_call, and force disconnect."""
        await asyncio.sleep(10.0)  # Let the conversation warm up first
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(3.0)
            if not (session and hasattr(session, "history") and session.history):
                continue
            try:
                messages = list(session.history.messages())
                if not messages:
                    continue
                # Check the last assistant message
                last_msg = messages[-1]
                role = getattr(last_msg, "role", "")
                content = str(getattr(last_msg, "content", "")).lower()
                if role == "assistant" and any(
                    phrase in content for phrase in FAREWELL_PHRASES
                ):
                    logger.warning(
                        "Safety net: Agent said goodbye but end_call was never invoked. Force disconnecting."
                    )
                    call_state["timeline"].append(
                        {
                            "event": "Farewell Safety Net Triggered",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        }
                    )
                    await asyncio.sleep(3.0)  # Give TTS time to finish speaking
                    await _force_disconnect_room(ctx)
                    break
            except Exception as e:
                logger.debug(f"Farewell safety net error: {e}")

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
                f"Farewell reply in {max(0.0, 150.0 - elapsed):.2f}s, "
                f"Hard kill in {max(0.0, 180.0 - elapsed):.2f}s."
            )

            # Event that lets us cancel the force-disconnect if the call ends naturally
            _force_disconnect_cancelled = asyncio.Event()

            async def force_disconnect_timer():
                try:
                    disconnect_delay = max(
                        0.0,
                        180.0
                        - (asyncio.get_event_loop().time() - entrypoint_start_time),
                    )
                    logger.info(
                        f"Force-disconnect timer armed: t+{disconnect_delay:.2f}s"
                    )
                    await asyncio.wait_for(
                        _force_disconnect_cancelled.wait(), timeout=disconnect_delay
                    )
                except asyncio.TimeoutError:
                    pass  # Timeout expired — proceed to disconnect
                except asyncio.CancelledError:
                    logger.info("Force-disconnect timer cancelled.")
                    return  # Cancelled — exit cleanly
                else:
                    logger.info(
                        "Call ended naturally — force-disconnect timer exiting."
                    )
                    return  # Event was set — call ended naturally, exit cleanly

                if ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                    logger.warning(
                        "HARD DISCONNECT: 3m limit reached. Force disconnecting room."
                    )
                    call_state["timeline"].append(
                        {
                            "event": "Max Call Duration Reached",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        }
                    )
                    await _force_disconnect_room(ctx)
                else:
                    logger.info(
                        "Room already disconnected — force-disconnect skipping."
                    )

            create_bg_task(force_disconnect_timer())

            # Stage 1: 2m 30s mark — update agent instructions for a natural farewell
            # We do NOT call generate_reply() here, so the agent won't interrupt the user.
            # The updated instructions are picked up on the agent's next natural turn.
            stage1_delay = max(
                0.0, 150.0 - (asyncio.get_event_loop().time() - entrypoint_start_time)
            )
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
                    await agent.update_instructions(
                        current_inst + "\n\n" + farewell_inst
                    )
                logger.info("Farewell instructions set.")

                try:
                    logger.info("Waiting for session to become inactive (25s timeout).")
                    await asyncio.wait_for(session.wait_for_inactive(), timeout=25.0)
                    logger.info("Session became inactive naturally.")
                except asyncio.TimeoutError:
                    logger.warning(
                        "Session did not go inactive within 25s — force-disconnect at 3m will handle it."
                    )
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
        safety_net_task = asyncio.create_task(farewell_safety_net())

        # Check if agent track was already published before we attached the listener
        for publication in ctx.room.local_participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                recorder.start_recording(publication.track, "agent")

        # Check if remote tracks were already subscribed before we attached the listener
        for participant in ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if (
                    publication.track
                    and publication.track.kind == rtc.TrackKind.KIND_AUDIO
                ):
                    recorder.start_recording(
                        publication.track, f"participant_{participant.identity}"
                    )

        if ctx.room.name.startswith("test_"):
            logger.info(
                "Test room detected. Skipping wait for remote participant to initialize synthesis."
            )
            call_state["user_joined"] = True
        else:
            logger.info("Waiting for remote participant to join...")
            wait_start = asyncio.get_event_loop().time()
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(0.5)
                if asyncio.get_event_loop().time() - wait_start > 60.0:
                    logger.warning(
                        "Remote participant did not join within 60 seconds (likely no answer). Disconnecting."
                    )
                    await _force_disconnect_room(ctx)
                    return

            logger.info("Remote participant joined. Initializing conversation...")
            call_state["user_joined"] = True
            call_state["timeline"].append(
                {
                    "event": "Remote Participant Joined",
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                }
            )
            await asyncio.sleep(0.5)

        logger.info(f"Generating greeting for {client_name}...")
        try:
            if is_inbound:
                session.generate_reply(
                    instructions="Initiate the conversation according to your system prompt. Introduce yourself and ask how you can help."
                )
            else:
                session.generate_reply(
                    instructions=f"Greet the user named {client_name} and follow the opening script in your instructions."
                )
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
            "Process ID (PID)": os.getpid(),
        }
        try:
            if ctx.job.metadata:
                context_data["Job metadata"] = ctx.job.metadata
        except:
            pass
        # Do not block main exception handling logic, the email function handles to_thread internally
        try:
            await send_crash_email(
                service_name="Livekit Voice Agent worker",
                error=e,
                context_data=context_data,
            )
        except Exception as email_err:
            logger.error(f"Failed to dispatch crash email: {email_err}")
    finally:
        logger.info("Entering entrypoint finally block (cleaning up and finalizing)...")
        # 1. Cancel background tasks
        for task_name in [
            "limiter_task",
            "inactivity_task",
            "goodbye_task",
            "safety_net_task",
        ]:
            task = locals().get(task_name)
            if task and not task.done():
                task.cancel()

        # 2. Capture history snapshot immediately before session cleans up
        history_snapshot = (
            list(session.history.messages()) if (session and session.history) else []
        )

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
                    call_state["timeline"].append(
                        {
                            "event": "Call Finalization Started",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        }
                    )

                # 1. Pre-load call metadata
                try:
                    # Use _effective_call_metadata (which includes resolved inbound context)
                    # as the primary source, fall back to re-parsing raw job metadata
                    if _effective_call_metadata:
                        call_payload = dict(_effective_call_metadata)
                    else:
                        call_payload = (
                            json.loads(ctx.job.metadata) if ctx.job.metadata else {}
                        )
                except Exception as e:
                    logger.error(f"Failed to parse call metadata: {e}")
                    call_payload = {}

                # Determine call status based on whether the user joined and actually spoke
                user_spoke = False
                for msg in history_snapshot:
                    role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
                    if role.lower() == "user":
                        user_spoke = True
                        break

                call_id = (
                    call_payload.get("call_id")
                    or call_payload.get("voice_id")
                    or ctx.job.id
                )

                # Always check if there was a SIP-level error in Redis first
                redis_status = None
                try:
                    redis_url = os.getenv("REDIS_URL")
                    import redis.asyncio as redis

                    r = redis.from_url(redis_url, decode_responses=True)
                    redis_status = await r.get(f"sip_error_status:{call_id}")
                    await r.aclose()
                except Exception as redis_err:
                    logger.error(
                        f"Failed to fetch precise SIP status from Redis: {redis_err}"
                    )

                if redis_status and not call_state["user_joined"]:
                    # Only trust Redis SIP error if the user never joined.
                    # If the user joined and spoke, the call connected — ignore stale Redis status
                    # left by a duplicate webhook's trigger_sip exception handler.
                    call_status = redis_status
                elif not call_state["user_joined"]:
                    # Fallback: if it waited less than 25s before terminating, it's Busy/Rejected.
                    # If it waited more than 25s, it's a No Answer timeout.
                    elapsed_time = (
                        asyncio.get_event_loop().time() - entrypoint_start_time
                    )
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
                            call_id = (
                                call_payload.get("call_id")
                                or call_payload.get("voice_id")
                                or ctx.job.id
                            )
                            s3_key = f"recordings/{call_id}.mp3"
                            loop = asyncio.get_running_loop()
                            recording_url = (
                                await loop.run_in_executor(
                                    None, upload_to_s3, mp3_bytes, s3_key
                                )
                                or ""
                            )
                            logger.info(
                                f"S3 recording: {'uploaded' if recording_url else 'upload failed'}"
                            )
                        else:
                            logger.info("No audio data captured for recording")
                    else:
                        logger.info(
                            f"Skipping recording upload because call_status is {call_status}"
                        )
                except Exception as e:
                    logger.error(f"Recording/S3 step failed: {e}", exc_info=True)

                # 3. Build transcript from captured history snapshot
                try:
                    transcript_data = SessionRecorder.build_transcript(
                        list(history_snapshot)
                    )
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
                    logger.info(
                        f"Call status is {call_status}. Skipping LLM analysis and applying 'Not Answering' logic."
                    )
                    summary_text = f"Call failed with status: {call_status}. The user did not speak or answer."
                    duration = 0
                    not_answering_id = current_stage_id
                    for stage in stage_details:
                        desc = stage.get("description", "").lower()
                        if (
                            "not answering" in desc
                            or "failed" in desc
                            or "incomplete" in desc
                            or "busy" in desc
                        ):
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
                            client_country_code = call_payload.get("client_country_code") or call_payload.get("country_code", "")
                            
                            analysis = await SessionRecorder.analyze_call(
                                llm_engine=llm_engine,
                                history=list(history_snapshot),
                                current_stage_id=current_stage_id,
                                stage_details=stage_details,
                                duration=duration,
                                client_country_code=client_country_code,
                            )
                            summary_text = analysis["summary"]
                            new_stage_id = analysis["new_stage_id"]
                            next_call_on = analysis["next_call_on"]

                            if analysis.get("appointment_date_time"):
                                client_custom_fields["appointment_date_time"] = (
                                    analysis["appointment_date_time"]
                                )
                            if analysis.get("doctor"):
                                client_custom_fields["doctor"] = analysis["doctor"]
                            if analysis.get("hospital_location"):
                                client_custom_fields["hospital_location"] = analysis[
                                    "hospital_location"
                                ]

                            logger.info(
                                f"Analysis completed. New Stage ID: {new_stage_id}, Next Call On: {next_call_on}"
                            )
                        else:
                            logger.warning(
                                "Skipping analysis: LLM or history unavailable after session close"
                            )
                    except Exception as e:
                        logger.error(
                            f"Analysis or summary generation failed: {e}", exc_info=True
                        )

                # 6. Build webhook payload
                direction = call_payload.get("direction", "outbound")
                logger.info(
                    f"Building webhook payload: direction={direction}, "
                    f"call_status={call_status}, call_id={call_payload.get('call_id')}"
                )
                webhook_payload = {
                    "event": "CALL_DATA_UPDATE",
                    "data": {
                        "client_id": call_payload.get("lead_id"),
                        "call_id": call_payload.get("call_id")
                        or call_payload.get("voice_id"),
                        "call_status": call_status,
                        "status": call_status,
                        "direction": direction,
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
                        "call_custom_fields": call_payload.get(
                            "call_custom_fields", {}
                        ),
                        "client_phone": call_payload.get("client_phone")
                        or call_payload.get("phone"),
                        "trunk_id": call_payload.get("trunk_id"),
                        "url": "",
                        "timeline": call_state.get("timeline", []),
                    },
                }

                # For inbound calls, include resolution context so backend can correlate
                if direction == "inbound":
                    try:
                        inbound_context = {
                            "org_id": call_payload.get("org_id"),
                            "kb_id": call_payload.get("kb_id"),
                            "phone_number": call_payload.get("phone_number"),
                            "provider": call_payload.get("provider"),
                        }
                        inbound_context = {k: v for k, v in inbound_context.items() if v is not None}
                        if inbound_context:
                            webhook_payload["data"]["inbound_context"] = inbound_context
                            webhook_payload["data"].update(inbound_context)
                            logger.info(
                                f"Inbound webhook: added inbound_context={inbound_context} "
                                f"(direction={direction}, call_id={call_payload.get('call_id')})"
                            )
                        else:
                            logger.info(
                                f"Inbound webhook: no inbound context to add "
                                f"(direction={direction}, call_id={call_payload.get('call_id')})"
                            )
                    except Exception as ctx_err:
                        logger.error(f"Failed to add inbound_context to webhook (non-fatal): {ctx_err}")

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
                        },
                    }

                # Save to local Postgres DB
                try:
                    c_id = webhook_payload.get("data", {}).get("call_id", ctx.job.id)
                    await save_call_log_to_db(
                        call_id=str(c_id),
                        call_log=json.dumps(webhook_payload.get("data", {}), indent=2),
                        status=call_status,
                        recording_url=recording_url,
                    )
                except Exception as db_err:
                    logger.error(f"Error calling save_call_log_to_db: {db_err}")

                logger.info("Delivering post-call webhook to backend...")
                logger.info(f"Webhook Payload:\n{json.dumps(webhook_payload)}")
                delivered = await send_to_backend(webhook_payload)
            except Exception as e:
                logger.error(f"Webhook delivery failed: {e}", exc_info=True)
                delivered = False

            # 9. Free active call slot in Redis
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
            api_secret=os.getenv("LIVEKIT_API_SECRET"),
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
        try:
            import asyncio

            asyncio.run(
                send_crash_email(
                    service_name="Livekit Voice Agent Worker (Core/Startup)",
                    error=e,
                    context_data={
                        "Status": "Crashloop / Process Death",
                        "PID": os.getpid(),
                    },
                )
            )
        except Exception as email_err:
            logger.error(f"Failed to dispatch core crash email: {email_err}")
        raise


if __name__ == "__main__":
    run_agent()
