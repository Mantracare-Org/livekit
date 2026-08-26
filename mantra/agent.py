import logging
import json
import asyncio
import os
import datetime
import aiohttp
from mantra.email_alerts import send_crash_email
from mantra.language_manager import LanguageManager, MultilingualParallelSTT
import sys

# ── Suppress OpenTelemetry 429 errors ──────────────────────────────────
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
os.environ.setdefault("OTEL_LOGS_EXPORTER", "none")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("https_proxy", None)
os.environ.pop("http_proxy", None)

_is_inference = os.getenv("LIVEKIT_AGENTS_INFERENCE") == "1"
_proc_type = "Inference Subprocess" if _is_inference else "Main Worker"

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter(
        f"%(asctime)s INFO (Type: {_proc_type}, PID: {os.getpid()}) %(name)s: %(message)s"
    )
)


logging.basicConfig(level=logging.DEBUG, handlers=[_handler])
logger = logging.getLogger("mantra.agent")
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)
logger.info("Initializing process...")

POST_CALL_LLM_MODEL = os.getenv("POST_CALL_LLM_MODEL", "deepseek-v4-pro")


def build_post_call_llm() -> "llm.LLM":
    """Build a dedicated LLM engine for post-call analysis.

    Uses the Pro-tier model so transcript analysis (stage transitions,
    next_call_on, user_intent) is more reliable than the live-agent flash model.
    Falls back to the Gemini flash model if DEEPSEEK_API_KEY is missing.
    """
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        logger.warning("DEEPSEEK_API_KEY not set for post-call LLM, falling back to gemini-2.5-flash")
        return google.LLM(model="gemini-2.5-flash")
    import openai as openai_client
    client = openai_client.AsyncClient(
        api_key=deepseek_key,
        base_url="https://api.deepseek.com",
    )
    logger.info(f"Post-call LLM using model: {POST_CALL_LLM_MODEL}")
    return openai.LLM(model=POST_CALL_LLM_MODEL, client=client)

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
from livekit.agents.voice.agent import ModelSettings

from livekit.plugins import openai, google, silero, deepgram

from mantra.utils import (
    SessionRecorder,
    upload_to_s3,
    send_to_backend,
    normalize_datetime,
    save_call_log_to_db,
    save_call_event,
    report_telemetry,
    format_e164_phone_number,
    reconcile_process_and_stage_id,
)
from mantra.amd import detect_voicemail

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
    "sia": "4459a9a5-69d6-4680-b970-e13dc51845b6",
    "sneha": "6b02ffe5-e3cb-48c0-a023-c72f85953375",
    "kavita": "56e35e2d-6eb6-4226-ab8b-9776515a7094",
    "katie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "cathy": "e8e5fffb-252c-436d-b842-8879b84445b6",
}

# Load environment variables
load_dotenv()  # Load .env (OpenAI, etc.)
load_dotenv(
    ".env.local", override=True
)  # Load .env.local (LiveKit, etc.) and override if needed


AGENT_NAME = os.getenv("AGENT_NAME", "mantra-agent")
logger.info(f"Agent name configured as: {AGENT_NAME}")

AMD_ENABLED = os.getenv("AMD_ENABLED", "1") == "1"

server = AgentServer(num_idle_processes=20, shutdown_process_timeout=120.0)

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

def _as_int(value):
    """Coerce value to int for backend Zod schemas. Returns None if not coercible."""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None

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

            # Get all KB collection IDs for this org (includes org_id fallback for legacy data)
            try:
                kb_ids = await kb.get_kb_ids_for_org(result["org_id"])
            except Exception as e:
                logger.error(f"Failed to fetch kb_ids for org {result.get('org_id')}: {e}")
                kb_ids = [result.get("org_id")]

            process_id = None
            stage_id = None
            try:
                col_details = await kb.get_collection_details_for_org(result["org_id"])
                if col_details:
                    process_id = col_details.get("process_id")
                    stage_id = col_details.get("stage_id")
            except Exception as e:
                logger.error(f"Failed to fetch collection details for org {result.get('org_id')}: {e}")

            return {
                "org_id": result.get("org_id"),
                "kb_id": result.get("org_id"),
                "kb_ids": kb_ids,
                "kb_tags": result.get("kb_tags", []),
                "prompt": result.get("prompt"),
                "voice": result.get("voice"),
                "model": result.get("model"),
                "process_id": process_id,
                "stage_id": stage_id,
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
    Resolves inbound call context from the PostgreSQL org_configs table (DB only, no HTTP).
    """
    logger.info(f"[DIAG] resolve_inbound_context: looking up {phone_number} in DB...")
    config = await _resolve_from_db(phone_number)
    if config:
        logger.info(f"[DIAG] resolve_inbound_context: DB HIT — org_id={config.get('org_id')}, kb_ids={config.get('kb_ids')}, client={config.get('client_name')}")
    else:
        logger.warning(f"[DIAG] resolve_inbound_context: DB MISS for {phone_number} — using dispatch rule defaults")
    return config


class AssistantFunctions:
    def __init__(
        self,
        job_metadata: str,
        room_name: str,
        ctx: JobContext = None,
        kb_ids: list[str] = None,
        kb_tags: list[str] = None,
        call_state: dict = None,
    ):
        self.job_metadata = job_metadata
        self.room_name = room_name
        self.handoff_triggered = False
        self._end_call_triggered = False
        self.call_state = call_state
        self.agent = None
        self.session = None
        self.ctx = ctx
        self.call_id = None
        self.tos_task_id = None
        self.kb_ids = kb_ids or []
        self.kb_tags = kb_tags or []
        self._retriever: KnowledgeRetriever | None = None
        if job_metadata:
            try:
                payload = json.loads(job_metadata)
                self.call_id = str(payload.get("call_id") or payload.get("voice_id") or "")
                self.tos_task_id = payload.get("metadata", {}).get("tos_task_id")
                self.tos_task_id = payload.get("tos_task_id") or payload.get("metadata", {}).get("tos_task_id")
            except Exception:
                pass

    def _telemetry(self, message: str, call_id: str = None, data: dict = None):
        if self.tos_task_id:
            cid = call_id or self.call_id or ""
            create_bg_task(
                report_telemetry(
                    tos_task_id=self.tos_task_id,
                    message=f"[Agent Worker] {message}",
                    call_id=cid,
                    data=data,
                )
            )

    async def _get_kb(self) -> PostgresKnowledgeBase:
        return get_global_kb()

    async def _get_retriever(self) -> KnowledgeRetriever:
        if self._retriever is None:
            kb = await self._get_kb()
            self._retriever = KnowledgeRetriever(kb)
        return self._retriever

    @property
    def used_kb_process_ids(self) -> list[str]:
        if self._retriever is None:
            return []
        seen = set()
        result = []
        for meta in self._retriever.accessed_pages_meta:
            raw = meta.get("process_id")
            if isinstance(raw, list):
                for pid in raw:
                    if pid is not None and str(pid) not in seen:
                        seen.add(str(pid))
                        result.append(str(pid))
            elif raw is not None and str(raw) not in seen:
                seen.add(str(raw))
                result.append(str(raw))
            pa = meta.get("process_assignments")
            if isinstance(pa, list):
                for entry in pa:
                    if isinstance(entry, dict) and entry.get("process_id") is not None:
                        pid_str = str(entry["process_id"])
                        if pid_str not in seen:
                            seen.add(pid_str)
                            result.append(pid_str)
            psd = meta.get("process_stage_data")
            if isinstance(psd, list):
                for entry in psd:
                    pid = entry.get("id") or entry.get("process_id")
                    if pid is not None:
                        pid_str = str(pid)
                        if pid_str not in seen:
                            seen.add(pid_str)
                            result.append(pid_str)
        return result

    @property
    def used_kb_stage_ids(self) -> list[str]:
        if self._retriever is None:
            return []
        seen = set()
        result = []
        for meta in self._retriever.accessed_pages_meta:
            raw = meta.get("stage_id")
            if isinstance(raw, list):
                for sid in raw:
                    if sid is not None and str(sid) not in seen:
                        seen.add(str(sid))
                        result.append(str(sid))
            elif raw is not None and str(raw) not in seen:
                seen.add(str(raw))
                result.append(str(raw))
            s_ids = meta.get("stage_ids")
            if isinstance(s_ids, list):
                for sid in s_ids:
                    if sid is not None and str(sid) not in seen:
                        seen.add(str(sid))
                        result.append(str(sid))
            pa = meta.get("process_assignments")
            if isinstance(pa, list):
                for entry in pa:
                    if isinstance(entry, dict) and isinstance(entry.get("stage_ids"), list):
                        for sid in entry["stage_ids"]:
                            if sid is not None and str(sid) not in seen:
                                seen.add(str(sid))
                                result.append(str(sid))
            psd = meta.get("process_stage_data")
            if isinstance(psd, list):
                for entry in psd:
                    if isinstance(entry, dict):
                        stages = entry.get("stages") or entry.get("stageDetails")
                        if isinstance(stages, list):
                            for stg in stages:
                                if isinstance(stg, dict):
                                    sid = stg.get("stage_id") or stg.get("id")
                                    if sid is not None and str(sid) not in seen:
                                        seen.add(str(sid))
                                        result.append(str(sid))
        return result

    @property
    def used_process_stage_data(self) -> list:
        if self._retriever is None:
            return []
        seen_ids = set()
        result = []
        for meta in self._retriever.accessed_pages_meta:
            psd = meta.get("process_stage_data")
            if isinstance(psd, list):
                for entry in psd:
                    pid = entry.get("id")
                    if pid is not None and pid not in seen_ids:
                        seen_ids.add(pid)
                        result.append(entry)
        return result

    # @llm.function_tool(
    #     description="Transfer the call to a human agent in a specific department when the user requests it, "
    #                 "you cannot resolve their issue, or they seem frustrated. "
    #                 "Specify the department (e.g., 'refund', 'support', 'billing', 'general') "
    #                 "based on what the user needs."
    # )
    # async def transfer_to_human(
    #     self,
    #     reason: Annotated[str, "Why the human agent is needed — be specific about the user's request"],
    #     department: Annotated[str, "The department to transfer to (e.g., refund, support, billing, general)"] = "general"
    # ):
    #     logger.info(f"Handoff requested. Reason: {reason}, Department: {department}")
    # 
    #     # Guard: prevent duplicate transfers if LLM calls this twice
    #     if self.handoff_triggered:
    #         logger.warning("Handoff already in progress — ignoring duplicate request")
    #         return "TRANSFER_ALREADY_IN_PROGRESS."
    # 
    #     self.handoff_triggered = True
    #     self.last_reason = reason
    #     self.last_department = department
    # 
    #     # Parse metadata to get call/lead IDs
    #     try:
    #         payload = json.loads(self.job_metadata) if self.job_metadata else {}
    #     except Exception:
    #         payload = {}
    # 
    #     # Determine target number from department mapping
    #     dept_lower = department.lower().strip()
    #     target_number = TRANSFER_NUMBERS.get(dept_lower, TRANSFER_DEFAULT_NUMBER)
    #     trunk_id = TRANSFER_SIP_TRUNK_ID or payload.get("trunk_id") or payload.get("call_from_id") or ""
    # 
    #     if target_number and trunk_id:
    #         try:
    #             lk_api = api.LiveKitAPI(
    #                 url=os.getenv("LIVEKIT_URL"),
    #                 api_key=os.getenv("LIVEKIT_API_KEY"),
    #                 api_secret=os.getenv("LIVEKIT_API_SECRET")
    #             )
    #             timestamp = datetime.datetime.now().strftime("%H%M%S%f")
    #             call_id = payload.get("call_id") or payload.get("voice_id") or self.room_name
    #             human_identity = f"human_{call_id}_{timestamp}"
    #             await lk_api.sip.create_sip_participant(
    #                 api.CreateSIPParticipantRequest(
    #                     sip_trunk_id=trunk_id,
    #                     sip_call_to=target_number,
    #                     room_name=self.room_name,
    #                     participant_identity=human_identity,
    #                     participant_name=f"Human - {department.title()}"
    #                 )
    #             )
    #             await lk_api.aclose()
    #             logger.info(f"Human agent ({target_number}) added to room {self.room_name} for {department} department")
    #         except Exception as e:
    #             logger.error(f"Failed to add human agent via SIP: {e}")
    #     else:
    #         missing = []
    #         if not target_number:
    #             missing.append("target phone number")
    #         if not trunk_id:
    #             missing.append("SIP trunk ID")
    #         logger.warning(f"Cannot transfer: missing {', '.join(missing)}. Backend notification sent anyway.")
    # 
    #     # Notify backend (skip if no URL configured)
    #     if os.getenv("MANTRAASSIST_BACKEND_URL"):
    #         webhook_payload = {
    #             "event": "HANDOFF_REQUESTED",
    #             "data": {
    #                 "room_name": self.room_name,
    #                 "reason": reason,
    #                 "department": department,
    #                 "call_id": payload.get("call_id") or payload.get("voice_id"),
    #                 "lead_id": payload.get("lead_id"),
    #                 "client_name": payload.get("client_name", "User"),
    #             }
    #         }
    #         await send_to_backend(webhook_payload)
    # 
    #     # Override agent instructions to enforce absolute silence
    #     if self.agent:
    #         try:
    #             await self.agent.update_instructions(
    #                 "You are SILENT. The call has been transferred to a human agent. "
    #                 "Say absolutely nothing. Do not speak, do not acknowledge, do not say goodbye. "
    #                 "The human agent handles everything from here. SILENT."
    #             )
    #             logger.info("Agent instructions overridden to enforce silence")
    #         except Exception as e:
    #             logger.error(f"Failed to update agent instructions: {e}")
    # 
    #     # Interrupt any in-progress speech from the agent
    #     try:
    #         if self.agent and self.agent._session:
    #             self.agent._session.interrupt()
    #             logger.info("Agent speech interrupted for handoff")
    #         except Exception as e:
    #             logger.debug(f"Agent interrupt unavailable (non-fatal): {e}")
    # 
    #     return "TRANSFER_COMPLETE. Do not speak."

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
        description="End the call. Call this tool ONLY when the conversation has reached its final conclusion (e.g. after saying final goodbye or when the user explicitly hangs up/declines). NEVER call this during the initial greeting or while the conversation is active."
    )
    async def end_call(self):
        if self.call_state and not self.call_state.get("user_has_spoken", False) and not self.call_state.get("initial_greeting_done", False):
            logger.warning("[DIAG] end_call invoked prematurely during initial greeting / before user spoke. Ignoring tool call.")
            return "Call cannot be ended before the conversation starts. Please greet the user and proceed with the conversation."

        logger.info("Agent decided to end the call via function tool. Disconnecting shortly.")
        self._telemetry("Call ended by agent")
        self._end_call_triggered = True
        async def graceful_disconnect():
            await asyncio.sleep(3.0)
            if self.ctx:
                await _force_disconnect_room(self.ctx)

        self._disconnect_task = create_bg_task(graceful_disconnect())
        return ""

    @llm.function_tool(
        description=(
            "Check doctor and healthcare provider availability, working hours, and open appointment slots on a specific date. "
            "ALWAYS use this tool whenever the caller asks about doctor availability, open consultation times, "
            "scheduling an appointment, or doctor working hours on a given day. "
            "If the caller mentions or asks about a specific medical department or specialty (e.g. 'Cardiology', 'Dermatology', 'Orthopedics', 'Pediatrics', 'Dental'), extract and pass it in department."
        )
    )
    async def check_doctor_availability(
        self,
        date: Annotated[str, "The date to check in YYYY-MM-DD format (e.g. '2026-08-25'). If the caller specifies a relative day like 'tomorrow' or 'next Tuesday', calculate the exact YYYY-MM-DD date."],
        doctor_name: Annotated[Optional[str], "Optional doctor name to filter by (e.g. 'Sharma' or 'Dr. Ananya'). If no doctor name is mentioned, leave None."] = None,
        department: Annotated[Optional[str], "Optional medical department or specialty mentioned in the transcript/call (e.g. 'Cardiology', 'Dermatology', 'Orthopedics', 'Pediatrics', 'General Medicine'). If no department is mentioned, leave None."] = None,
    ) -> str:
        org_id = None
        caller_phone = None

        # 1. Extract from active call state (resolved from registered phone number in org_configs)
        if self.call_state:
            org_id = self.call_state.get("org_id")
            caller_phone = (
                self.call_state.get("caller_phone_number")
                or self.call_state.get("caller_phone")
                or self.call_state.get("phone_number")
                or self.call_state.get("client_phone")
            )

        # 2. Extract dynamically from call metadata payload
        if self.job_metadata:
            try:
                payload = json.loads(self.job_metadata) if isinstance(self.job_metadata, str) else self.job_metadata
                if not org_id:
                    org_id = payload.get("org_id") or (payload.get("metadata", {}).get("org_id") if isinstance(payload.get("metadata"), dict) else None)
                if not caller_phone:
                    caller_phone = (
                        payload.get("phone_number")
                        or payload.get("caller_phone")
                        or payload.get("client_phone")
                        or payload.get("from_phone")
                    )
            except Exception as e:
                logger.warning(f"Could not parse job_metadata in check_doctor_availability: {e}")

        logger.info(f"Agent requesting doctor availability via MCP: org_id={org_id}, date={date}, doctor={doctor_name}, department={department}, phone={caller_phone}")

        from mantra.mcp_client import get_mcp_client

        mcp_client = get_mcp_client()
        result = await mcp_client.call_tool(
            "receive_doctor_availability",
            {
                "org_id": org_id,
                "date": str(date).strip(),
                "query_date": str(date).strip(),
                "name": str(doctor_name).strip() if doctor_name else None,
                "doc_name": str(doctor_name).strip() if doctor_name else "",
                "department": str(department).strip() if department else "",
                "query": str(doctor_name).strip() if doctor_name else (str(department).strip() if department else None),
                "caller_phone": caller_phone,
            },
        )
        return result

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


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    entrypoint_start_time = asyncio.get_event_loop().time()
    logger.info(f"[DIAG] ======== ENTRYPOINT STARTED ======== room={ctx.room.name} job_id={ctx.job.id} pid={os.getpid()}")
    logger.info(f"[DIAG] Job metadata: {ctx.job.metadata[:200] if ctx.job.metadata else 'None'}")

    call_state = {
        "user_joined": False,
        "caller_phone_number": None,
        "agent_joined_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "human_joined_at": None,
        "call_initiated_at": None,
        "timeline": [{"event": "Agent Session Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}]
    }

    tos_task_id = None
    call_id = ctx.job.id
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            call_id = payload.get("call_id") or payload.get("voice_id") or ctx.job.id
            tos_task_id = payload.get("metadata", {}).get("tos_task_id")
            tos_task_id = payload.get("tos_task_id") or payload.get("metadata", {}).get("tos_task_id")
            metadata = payload.get("metadata", {})
            if isinstance(metadata, dict):
                call_state["call_initiated_at"] = metadata.get("call_initiated_at")
        except:
            pass

    call_state["tos_task_id"] = tos_task_id
    call_state["call_id"] = str(call_id)

    async def _telemetry(status: str, detail: str = "", data: dict = None):
        _tos_task_id = call_state.get("tos_task_id")
        _cid = call_state.get("call_id")
        if _tos_task_id:
            msg = f"[Agent Worker] {status}"
            if detail:
                msg += f" — {detail}"
            create_bg_task(report_telemetry(tos_task_id=_tos_task_id, message=msg, call_id=_cid, data=data))

    await _telemetry("agent_started", f"room={ctx.room.name}")
    await ctx.connect()
    await _telemetry("room_connected", f"room={ctx.room.name}")

    # Log entrypoint_started event to audit trail
    create_bg_task(save_call_event(
        call_id=str(call_id),
        event_type="entrypoint_started",
        event_source="agent",
        event_payload={"room": ctx.room.name, "job_id": ctx.job.id},
        event_log=f"room={ctx.room.name} job_id={ctx.job.id}",
    ))

    logger.info(f"--- Starting agent session ---")
    logger.info(f"Room: {ctx.room.name}")
    logger.info(f"Job ID: {ctx.job.id}")
    logger.info(f"Metadata: {ctx.job.metadata}")

    # ── Inbound Call Context Resolution ──────────────────────────────────
    resolved_context = None
    kb_ids_list = []
    kb_tags_list = []

    if ctx.job.metadata:
        try:
            meta_payload = json.loads(ctx.job.metadata)

            if meta_payload.get("direction") == "inbound":
                phone_number = meta_payload.get("phone_number", "")
                logger.info(f"[DIAG] Inbound call detected — phone_number={phone_number}")
                if phone_number:
                    call_state["caller_phone_number"] = phone_number
                    resolved_context = await resolve_inbound_context(phone_number)
                    if resolved_context:
                        meta_payload.update(resolved_context)
                        if resolved_context.get("org_id"):
                            call_state["org_id"] = resolved_context.get("org_id")
                        logger.info(f"[DIAG] Inbound context merged: org_id={resolved_context.get('org_id')}")
                    else:
                        logger.warning(f"[DIAG] Inbound resolution failed for {phone_number} — using dispatch rule defaults, call WILL connect")
                else:
                    logger.warning("[DIAG] Inbound call has no phone_number in metadata")

            if meta_payload.get("org_id"):
                call_state["org_id"] = meta_payload.get("org_id")
                kb_ids_list.append(str(meta_payload["org_id"]))
            if "kb_id" in meta_payload and meta_payload["kb_id"]:
                kb_ids_list.append(str(meta_payload["kb_id"]))
            if "kb_ids" in meta_payload and isinstance(meta_payload["kb_ids"], list):
                kb_ids_list.extend([str(k) for k in meta_payload["kb_ids"]])
            if "kb_tags" in meta_payload and isinstance(meta_payload["kb_tags"], list):
                kb_tags_list.extend([str(t) for t in meta_payload["kb_tags"]])
            kb_ids_list = list(set([str(k) for k in kb_ids_list if k]))
            kb_tags_list = list(set([str(t) for t in kb_tags_list if t]))
        except Exception as e:
            logger.error(f"Failed to parse/resolve metadata: {e}")

    logger.info(f"KB scope: kb_ids={kb_ids_list}, kb_tags={kb_tags_list}")

    fnc_ctx = AssistantFunctions(
        json.dumps(meta_payload) if ctx.job.metadata else "",
        ctx.room.name,
        ctx=ctx,
        kb_ids=kb_ids_list,
        kb_tags=kb_tags_list,
        call_state=call_state,
    )

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # NOTE: Redis logic for concurrency management and call tracking has been removed.

    # Fully in-memory recorder — no disk I/O
    recorder = SessionRecorder()

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info(f"[DIAG] Track subscribed: kind={track.kind} participant={participant.identity} sid={track.sid}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, f"participant_{participant.identity}")
            logger.info(f"[DIAG] Recording started for participant audio track: {participant.identity}")

    @ctx.room.on("local_track_published")
    def on_local_track_published(
        publication: rtc.LocalTrackPublication, track: rtc.Track
    ):
        logger.info(f"[DIAG] Local track published: kind={track.kind} sid={track.sid}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_recording(track, "agent")
            logger.info(f"[DIAG] Recording started for agent audio track")

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        call_state["timeline"].append(
            {
                "event": "Remote Participant Disconnected",
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
        )
        logger.info(
            f"[DIAG] Participant {participant.identity} disconnected. Force-ending call."
        )
        create_bg_task(_force_disconnect_room(ctx))

    initial_instructions = """You are a warm, polite, and empathetic Care Support Assistant on a phone call.

CORE BEHAVIOR:
- This is a PHONE CALL. Speak naturally.
- Keep responses SHORT (1-2 sentences).
- Use natural fillers that match the caller's language (English: "Got it", "Sure"; Hindi: "Theek hai", "Haan").
<!-- LANGUAGE_DIRECTIVE_START -->
<!-- LANGUAGE_DIRECTIVE_END -->
- Sound like a helpful human friend, not a robot.
- DO NOT SPEAK IN OTHER LANGAUGES EXCEPT ENGLISH AND HINDI
- Do NOT use markdown, bullet points, or special characters.
- If the user pauses, wait patiently for them to finish.
- ACTIVELY LISTEN: If the user asks a question (e.g., about directions, a bus stand, or any other detail), address it directly and helpfully BEFORE returning to the main topic. Never ignore the user's questions or blindly repeat your script.
- RETAIN CONTEXT & AVOID REPETITION: Remember the user's previous answers. Do NOT repeatedly ask the same questions. If they say no or want to focus on something else, acknowledge it and move on. DO NOT be pushy.
- KNOWLEDGE BASE USAGE: If the user asks a factual question or inquires about policies, services, or locations, you MUST use the `search_knowledge_base` tool to find the accurate answer.

# HUMAN HANDOFF (DISABLED):
# - Handoff to human is currently disabled.
# - If the user explicitly asks to speak to a human or a doctor/clinical agent, apologize and let them know:
#   "I understand you want to speak to a human or doctor. Unfortunately, we don't have human transfers available right now. However, I can help you book an appointment, or have an agent call you back later."
# - If they insist, politely end the call. Do not promise transfers or human callback.

POLITENESS & EMPATHY:
- Always be polite, courteous, and respectful.
- Show genuine empathy and understanding. Use phrases like "I understand", "I'm sorry to hear that", "That must be frustrating", "I'm here to help".
- Be patient and kind, even if the user seems confused or annoyed.
- Use a warm, caring, and reassuring tone.
- Never be rude, dismissive, or impatient.

ENDING THE CALL:
- You have a tool called `end_call`. Call this tool ONLY when the call is concluding.
- NEVER call `end_call` during the opening greeting, introduction, or while the conversation is in progress.
- Call `end_call` ONLY when:
  * The user explicitly says goodbye, thank you, that's all, not interested, hang up, or end the call.
  * The user explicitly declines or rejects the offer (e.g. "not interested", "no thanks", "I don't need this").
  * The conversation has reached its natural conclusion and all objectives are addressed.
- The sequence for ending a call: 1) Call `end_call` tool, 2) THEN say a brief warm goodbye in your response text.
- Do NOT ask follow-up questions after the user indicates they want to end the call or is not interested.
- Keep your final goodbye SHORT: "Thank you for your time. Have a great day!"

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
    _effective_call_metadata = None
    
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
                
                # For inbound calls, do not inject client_name into additional context so the LLM does not assume the caller's name from DB config
                if is_inbound and key == "client_name":
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

            # Inject live date and time context so LLM always uses current year and date
            now_dt = datetime.datetime.now()
            initial_instructions += "\n\n--- CURRENT DATE & TIME ---\n"
            initial_instructions += f"- Today's Date: {now_dt.strftime('%A, %B %d, %Y')}\n"
            initial_instructions += f"- Current Time: {now_dt.strftime('%I:%M %p')}\n"
            initial_instructions += f"- Current Year: {now_dt.year}\n"
            initial_instructions += f"- Always calculate appointment dates and relative days (e.g. 'today', 'tomorrow', 'next week', 'August 31') using the current year ({now_dt.year}) and pass in YYYY-MM-DD format.\n"

            # Add an overriding rule at the very end so it takes precedence over the backend prompt
            initial_instructions += "\n\n*** CRITICAL OVERRIDING RULES ***\n"
            initial_instructions += "1. NEVER repeat the same question twice. If the user dodges the question or asks a counter-question, answer them and DO NOT repeat your previous question.\n"
            initial_instructions += "2. DO NOT push for an appointment if the user hasn't explicitly agreed or if they are asking about other things. Let the conversation flow naturally.\n"
            initial_instructions += "3. Answer user's questions DIRECTLY without appending a sales pitch or appointment request at the end of every turn.\n"
            initial_instructions += "4. If the user asks to speak to a human or asks to be transferred — apologize and explain that human transfer is currently unavailable. Do not promise transfer, and if they insist, politely end the call.\n"
            initial_instructions += "5. LANGUAGE CONSISTENCY: Always respond in the caller's current conversational language as specified in the CURRENT CONVERSATIONAL LANGUAGE directive.\n"


            if is_inbound:
                initial_instructions += "\n--- INBOUND CALL FLOW & CONTEXT (CRITICAL) ---\n"
                initial_instructions += "- This is an INBOUND call. The caller reached out to you.\n"
                initial_instructions += "- TURN 1 (Initial Greeting): Greet warmly and ask how you can help (e.g. 'Hi, this is Arushi. How can I help you today?').\n"
                initial_instructions += "- TURN 2 (Name Request): When the caller states their reason for calling or intent, briefly acknowledge it, and politely ask for their name BEFORE proceeding to address their request (e.g. 'Sure, I can help with that! May I know your name, please?' or 'Got it. Who am I speaking with?').\n"
                initial_instructions += "- TURN 3+ (Addressing Request): Once the caller gives their name, address their request or answer their questions directly, using their name naturally.\n"
                initial_instructions += "- Do not assume the caller's name unless they state it or your prompt explicitly specifies it.\n"
                initial_instructions += "- Identify yourself strictly as instructed in your prompt\n"
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

        # Priority for Model: ai_payload.ai_model -> payload.model -> default "deepseek"
        model_name = ai_p.get("ai_model") or payload.get("model") or "deepseek"
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
        model_name = "deepseek"
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
            import openai as openai_client
            client = openai_client.AsyncClient(
                api_key=deepseek_key,
                base_url="https://api.deepseek.com",
            )
            llm_engine = openai.LLM(
                model="deepseek-v4-flash",
                client=client,
            )
    else:
        logger.info("Using OpenAI LLM")
        llm_engine = openai.LLM(model="gpt-4o-mini")
    # TTS via LiveKit Inference — Cartesia provider
    # Language Manager & TTS via LiveKit Inference — Cartesia provider
    raw_lang = None
    if "payload" in locals() and isinstance(payload, dict):
        ai_p = payload.get("ai_payload") if isinstance(payload.get("ai_payload"), dict) else {}
        raw_lang = (
            payload.get("language")
            or payload.get("lang")
            or ai_p.get("language")
            or ai_p.get("lang")
        )

    language_mgr = LanguageManager(initial_language=raw_lang)
    language = language_mgr.get_current_language()
    call_state["current_language"] = language

    # Insert dynamic language directive into initial prompt instructions
    directive_block = f"<!-- LANGUAGE_DIRECTIVE_START -->\n{language_mgr.get_prompt_directive()}\n<!-- LANGUAGE_DIRECTIVE_END -->"
    if "<!-- LANGUAGE_DIRECTIVE_START -->" in initial_instructions and "<!-- LANGUAGE_DIRECTIVE_END -->" in initial_instructions:
        pref = initial_instructions.split("<!-- LANGUAGE_DIRECTIVE_START -->")[0]
        suff = initial_instructions.split("<!-- LANGUAGE_DIRECTIVE_END -->")[1]
        initial_instructions = f"{pref}{directive_block}{suff}"
    else:
        initial_instructions += f"\n\n{directive_block}"

    logger.info(f"[LANG] Initialized language state: '{language}' (Voice: {voice_id} | Speed: {voice_speed})")

    tts_engine = inference.TTS(
        model="cartesia/sonic-3",
        voice=voice_id,
        language=language,
        extra_kwargs={
            "speed": voice_speed,
        }
    )

    # Native Deepgram Nova-3 Multilingual STT engine (supports English & Hindi)
    stt_engine = deepgram.STT(
        model="nova-3",
        language="multi",
        endpointing_ms=25,
        no_delay=True,
    )

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={
                "mode": "fixed",
                "min_delay": 0.25,
                "max_delay": 1.50,
            },
            interruption={
                "mode": "adaptive",
                "min_words": 2,
                "min_duration": 0.40,
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.5,
                "backchannel_boundary": (1.0, 1.0),
            },
            preemptive_generation={
                "preemptive_tts": True,
            },
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.10,
            min_silence_duration=0.25,
            prefix_padding_duration=0.20,
        ),
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
    )

    await _telemetry("Agent voice engine ready", f"model={model_name}")

    agent_tools = [
        fnc_ctx.end_call,
        fnc_ctx.search_knowledge_base,
        fnc_ctx.check_doctor_availability,
    ]

    class MantraMultilingualAgent(Agent):
        async def llm_node(
            self,
            chat_ctx: llm.ChatContext,
            tools: list[llm.Tool],
            model_settings: ModelSettings,
        ):
            # Synchronously align language before LLM generates text
            try:
                msgs = list(chat_ctx.messages()) if callable(getattr(chat_ctx, "messages", None)) else (chat_ctx.messages if isinstance(getattr(chat_ctx, "messages", None), list) else [])
                if msgs:
                    user_msgs = [m for m in msgs if hasattr(m, 'role') and str(m.role).lower() in ('user', 'caller')]
                    if user_msgs:
                        last_user_msg = user_msgs[-1]
                        content = " ".join([str(c) for c in last_user_msg.content]) if isinstance(last_user_msg.content, list) else str(last_user_msg.content)
                        if content and not content.startswith("[System:"):
                            new_lang, switched = language_mgr.process_user_utterance(content)
                            if switched:
                                old_lang = call_state.get("current_language", "en")
                                call_state["current_language"] = new_lang
                                logger.info(f"[LANG] Immediate llm_node switch: {old_lang} -> {new_lang}")
                                try:
                                    tts_engine.update_options(language=new_lang, voice=voice_id)
                                    logger.info(f"[LANG] TTS updated to language='{new_lang}' (voice={voice_id})")
                                except Exception as tts_err:
                                    logger.error(f"[LANG] Failed to update TTS options: {tts_err}")
                                try:
                                    stt_engine.update_options(language=new_lang)
                                except Exception as stt_err:
                                    logger.error(f"[LANG] Failed to update STT options: {stt_err}")

                            # Synchronously update the language directive in the system message inside chat_ctx
                            directive = language_mgr.get_prompt_directive()
                            for m in msgs:
                                if hasattr(m, 'role') and str(m.role).lower() in ('system',):
                                    sys_text = " ".join([str(c) for c in m.content]) if isinstance(m.content, list) else str(m.content)
                                    if "<!-- LANGUAGE_DIRECTIVE_START -->" in sys_text and "<!-- LANGUAGE_DIRECTIVE_END -->" in sys_text:
                                        pref = sys_text.split("<!-- LANGUAGE_DIRECTIVE_START -->")[0]
                                        suff = sys_text.split("<!-- LANGUAGE_DIRECTIVE_END -->")[1]
                                        new_sys_content = f"{pref}<!-- LANGUAGE_DIRECTIVE_START -->\n{directive}\n<!-- LANGUAGE_DIRECTIVE_END -->{suff}"
                                        m.content = [new_sys_content] if isinstance(m.content, list) else new_sys_content
            except Exception as align_err:
                logger.error(f"[LANG] Error aligning language in llm_node: {align_err}")

            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                yield chunk

    agent = MantraMultilingualAgent(
        instructions=initial_instructions,
        tools=agent_tools
    )
    fnc_ctx.agent = agent
    fnc_ctx.session = session

    # ── Transcript logging & dynamic language switching task ─────────────
    _last_logged_history_size = 0

    async def transcript_logger():
        nonlocal _last_logged_history_size
        await asyncio.sleep(2.0)  # brief startup delay
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            try:
                if session and hasattr(session, 'history') and session.history:
                    msgs = list(session.history.messages())
                    if len(msgs) > _last_logged_history_size:
                        new_msgs = msgs[_last_logged_history_size:]
                        _last_logged_history_size = len(msgs)
                        for m in new_msgs:
                            role = m.role.name if hasattr(m.role, "name") else str(m.role)
                            content = " ".join([str(c) for c in m.content]) if isinstance(m.content, list) else str(m.content)
                            if content and not content.startswith("[System:"):
                                content_preview = content[:200] + ("..." if len(content) > 200 else "")
                                logger.info(f"[DIAG] TRANSCRIPT | {role}: {content_preview}")

                                # Intercept caller/user utterances for dynamic language switching
                                if str(role).lower() in ["user", "caller"]:
                                    new_lang, switched = language_mgr.process_user_utterance(content)
                                    if switched:
                                        old_lang = call_state.get("current_language", "en")
                                        call_state["current_language"] = new_lang
                                        logger.info(f"[LANG] Language switch triggered: {old_lang} -> {new_lang}")

                                        # 1. Dynamically update STT language options
                                        try:
                                            stt_engine.update_options(language=new_lang)
                                            logger.info(f"[LANG] STT updated to language='{new_lang}'")
                                        except Exception as stt_err:
                                            logger.error(f"[LANG] Failed to update STT language: {stt_err}")

                                        # 2. Dynamically update TTS language options (preserving voice & speed)
                                        try:
                                            tts_engine.update_options(language=new_lang, voice=voice_id)
                                            logger.info(f"[LANG] TTS updated to language='{new_lang}' (voice={voice_id})")
                                        except Exception as tts_err:
                                            logger.error(f"[LANG] Failed to update TTS language: {tts_err}")

                                        # 3. Dynamically update agent system instructions
                                        try:
                                            cur_inst = agent.instructions
                                            if "<!-- LANGUAGE_DIRECTIVE_START -->" in cur_inst and "<!-- LANGUAGE_DIRECTIVE_END -->" in cur_inst:
                                                pref = cur_inst.split("<!-- LANGUAGE_DIRECTIVE_START -->")[0]
                                                suff = cur_inst.split("<!-- LANGUAGE_DIRECTIVE_END -->")[1]
                                                new_directive = language_mgr.get_prompt_directive()
                                                updated_inst = f"{pref}<!-- LANGUAGE_DIRECTIVE_START -->\n{new_directive}\n<!-- LANGUAGE_DIRECTIVE_END -->{suff}"
                                                await agent.update_instructions(updated_inst)
                                                logger.info(f"[LANG] Agent instructions updated to language='{new_lang}'")
                                        except Exception as inst_err:
                                            logger.error(f"[LANG] Failed to update agent prompt instructions: {inst_err}")

            except Exception as e:
                logger.info(f"[DIAG] Transcript logger error: {e}")
            await asyncio.sleep(0.4)

    transcript_task = asyncio.create_task(transcript_logger())

    @session.on("agent_state_changed")
    def on_agent_state(ev):
        call_state["agent_state"] = ev.new_state
        logger.info(f"[DIAG] Agent state change: {getattr(ev, 'old_state', 'None')} -> {ev.new_state}")
        if ev.new_state == "speaking":
            call_state["greeting_started"] = True
        elif getattr(ev, "old_state", None) == "speaking" and ev.new_state != "speaking":
            call_state["last_activity"] = asyncio.get_event_loop().time()
            if call_state.get("greeting_started"):
                call_state["initial_greeting_done"] = True

    @session.on("user_state_changed")
    def on_user_state(ev):
        logger.info(f"[DIAG] User state change: {getattr(ev, 'old_state', 'None')} -> {ev.new_state}")
        if ev.new_state == "speaking":
            call_state["last_activity"] = asyncio.get_event_loop().time()
            call_state["prompted_inactivity"] = False
            call_state["user_has_spoken"] = True

    async def inactivity_monitor():
        logger.info("Inactivity monitor started.")
        while not call_state.get("user_joined"):
            await asyncio.sleep(1.0)

        call_state["last_activity"] = asyncio.get_event_loop().time()
        call_state["prompted_inactivity"] = False

        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.0)
            # Only monitor inactivity AFTER initial greeting has completed speaking
            if not call_state.get("initial_greeting_done"):
                call_state["last_activity"] = asyncio.get_event_loop().time()
                continue

            now = asyncio.get_event_loop().time()
            agent_state = call_state.get("agent_state", "initializing")
            last_activity = call_state.get("last_activity", now)

            time_since_activity = now - last_activity

            if agent_state in ["listening", "idle"]:
                if time_since_activity > 30.0:
                    logger.warning("[DIAG] No user response for 30s. Disconnecting room due to inactivity.")
                    call_state["timeline"].append(
                        {
                            "event": "Inactivity Timeout Disconnect",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        }
                    )
                    create_bg_task(_force_disconnect_room(ctx))
                    break
                elif time_since_activity > 15.0 and not call_state.get(
                    "prompted_inactivity", False
                ):
                    logger.info("No response for 15s. Prompting user...")
                    call_state["prompted_inactivity"] = True
                    try:
                        session.generate_reply(
                            user_input="[System: The user has been silent for a while. Politely ask if they are still there (e.g. 'Are you still there?' or 'Let me know if you need help.'). Keep it extremely short.]"
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
    # Inbound calls use "thank you for calling" as a greeting — exclude it from detection
    INBOUND_FAREWELL_PHRASES = [
        "goodbye",
        "good bye",
        "bye bye",
        "take care",
        "have a great day",
        "have a good day",
        "have a nice day",
        "talk to you later",
        "see you later",
    ]
    OUTBOUND_FAREWELL_PHRASES = INBOUND_FAREWELL_PHRASES + [
        "thanks for calling",
        "thank you for calling",
    ]

    async def farewell_safety_net():
        """Detect if the agent said goodbye without calling end_call, and force disconnect."""
        logger.info("[DIAG] farewell_safety_net: Started")
        await asyncio.sleep(10.0)  # Let the conversation warm up first
        farewell_phrases = INBOUND_FAREWELL_PHRASES if is_inbound else OUTBOUND_FAREWELL_PHRASES
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(3.0)
            if not (session and hasattr(session, "history") and session.history):
                continue
            try:
                messages = list(session.history.messages())
                if not messages:
                    continue
                last_msg = messages[-1]
                role = getattr(last_msg, "role", "")
                content = str(getattr(last_msg, "content", "")).lower()
                if role == "assistant" and any(
                    phrase in content for phrase in farewell_phrases
                ):
                    logger.warning(
                        "[DIAG] farewell_safety_net: Agent said goodbye but end_call was never invoked. Force disconnecting."
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
                logger.info(f"Farewell safety net error: {e}")

    # Call duration limiter logic
    async def call_limiter():
        logger.info("[DIAG] call_limiter: Started — waiting for remote participant to join.")
        try:
            # Wait for remote participant to join before starting the 2m/3m timers
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(1.0)

            logger.info("[DIAG] call_limiter: Remote participant detected in room.")
            elapsed = asyncio.get_event_loop().time() - entrypoint_start_time
            logger.info(
                f"[DIAG] call_limiter: Participant joined at t={elapsed:.2f}s. "
                f"Farewell in {max(0.0, 150.0 - elapsed):.2f}s, Hard kill in {max(0.0, 180.0 - elapsed):.2f}s."
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
                    if hasattr(session, "wait_for_inactive") and callable(getattr(session, "wait_for_inactive")):
                        await asyncio.wait_for(session.wait_for_inactive(), timeout=25.0)
                    else:
                        await asyncio.sleep(25.0)
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
        logger.info(f"[DIAG] Starting agent session...")
        await session.start(agent=agent, room=ctx.room)
        logger.info(f"[DIAG] Session started successfully")
        limiter_task = asyncio.create_task(call_limiter())
        inactivity_task = asyncio.create_task(inactivity_monitor())
        safety_net_task = asyncio.create_task(farewell_safety_net())

        logger.info(f"[DIAG] Checking for already-published tracks...")
        # Check if agent track was already published before we attached the listener
        for publication in ctx.room.local_participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                recorder.start_recording(publication.track, "agent")
                logger.info(f"[DIAG] Found existing agent track: {publication.track.sid}")

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
                    logger.info(f"[DIAG] Found existing participant track: {participant.identity}/{publication.track.sid}")

        logger.info(f"[DIAG] Remote participants in room: {[p.identity for p in ctx.room.remote_participants.values()]}")
        logger.info(f"[DIAG] Room name starts with 'test_': {ctx.room.name.startswith('test_')}")

        # Capture caller's phone number from SIP participant for inbound calls
        if is_inbound:
            for p in ctx.room.remote_participants.values():
                raw = p.identity
                if raw.startswith("sip_"):
                    call_state["caller_phone_number"] = raw.replace("sip_", "", 1)
                    logger.info(f"[DIAG] Inbound caller phone captured: {call_state['caller_phone_number']}")
                    break

        if ctx.room.name.startswith("test_"):
            logger.info(
                "[DIAG] Test room detected. Skipping wait for remote participant to initialize synthesis."
            )
            call_state["user_joined"] = True
        else:
            logger.info("[DIAG] Waiting for remote participant to join...")
            wait_start = asyncio.get_event_loop().time()
            poll_count = 0
            while not list(ctx.room.remote_participants.values()):
                await asyncio.sleep(0.5)
                poll_count += 1
                if poll_count % 10 == 0:
                    elapsed = asyncio.get_event_loop().time() - wait_start
                    logger.info(f"[DIAG] Still waiting for remote participant... elapsed={elapsed:.1f}s room_state={ctx.room.connection_state}")
                if asyncio.get_event_loop().time() - wait_start > 60.0:
                    logger.warning(
                        "[DIAG] Remote participant did not join within 60 seconds (likely no answer). Disconnecting."
                    )
                    await _force_disconnect_room(ctx)
                    return

            logger.info(f"[DIAG] Remote participant joined. Participants: {[p.identity for p in ctx.room.remote_participants.values()]}")
            # Capture caller's phone number from SIP participant identity for inbound calls
            if is_inbound:
                for p in ctx.room.remote_participants.values():
                    raw_identity = p.identity
                    if raw_identity.startswith("sip_"):
                        call_state["caller_phone_number"] = raw_identity.replace("sip_", "", 1)
                        logger.info(f"[DIAG] Inbound caller phone number captured: {call_state['caller_phone_number']}")
                        break
            logger.info("Remote participant joined. Initializing conversation...")
            call_state["user_joined"] = True
            call_state["human_joined_at"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            call_state["timeline"].append({"event": "Remote Participant Joined", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
            await _telemetry("Customer joined the call")
            await asyncio.sleep(0.05)

        # ── Answering Machine Detection (outbound only - async background execution) ──
        if AMD_ENABLED and not is_inbound and not ctx.room.name.startswith("test_"):
            async def _run_amd_background():
                try:
                    detection = await detect_voicemail(session, participant_identity=f"sip_{call_id}", timeout=2.5)
                    if detection.category:
                        call_state["amd_category"] = detection.category
                        call_state["amd_transcript"] = detection.transcript
                        call_state["timeline"].append({
                            "event": f"AMD Result: {detection.category}",
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        })
                    if detection.detected:
                        logger.info("AMD: voicemail detected — interrupting agent and leaving voicemail message")
                        try:
                            session.interrupt()
                            speech_handle = session.generate_reply(
                                instructions=(
                                    "The call went to voicemail. Follow your instructions about "
                                    "voicemail. If you have no voicemail instructions, introduce "
                                    "yourself by name and ask them to call you back. Keep it brief."
                                )
                            )
                            await speech_handle.wait_for_playout()
                            await _telemetry("amd_voicemail_message_played")
                        except Exception as e:
                            logger.error(f"AMD voicemail message failed: {e}")
                        ctx.shutdown("voicemail detected")
                except Exception as e:
                    logger.warning(f"Background AMD check error: {e}")

            create_bg_task(_run_amd_background())

        # Give tiny 0.05s delay for WebRTC track binding before requesting initial greeting
        await asyncio.sleep(0.05)

        logger.info(f"[DIAG] Generating explicit initial greeting for {client_name} (inbound={is_inbound})...")
        try:
            if is_inbound:
                session.generate_reply(
                    instructions="Initiate the conversation according to your system prompt. Introduce yourself and ask how you can help."
                )
            else:
                session.generate_reply(
                    instructions=f"Greet the user named {client_name} and follow the opening script in your instructions."
                )
            logger.info("[DIAG] Greeting generation requested immediately upon connect.")
        except RuntimeError as e:
            logger.warning(f"[DIAG] Could not generate greeting (session may be closed): {e}")

        logger.info(f"[DIAG] Entering main loop — blocking until room disconnects. connection_state={ctx.room.connection_state}")
        # Block until the room connection drops or the session closes
        loop_count = 0
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1.0)
            loop_count += 1
            if loop_count % 30 == 0:
                elapsed = asyncio.get_event_loop().time() - entrypoint_start_time
                hist_count = len(list(session.history.messages())) if (session and hasattr(session, 'history') and session.history) else 0
                agent_state = call_state.get("agent_state", "unknown")
                logger.info(f"[DIAG] Call heartbeat — elapsed={elapsed:.0f}s agent_state={agent_state} history_msgs={hist_count} connection_state={ctx.room.connection_state}")
        logger.info(f"[DIAG] Main loop exited — room connection_state={ctx.room.connection_state} loop_count={loop_count}")

    except asyncio.CancelledError:
        logger.info("[DIAG] Call entrypoint coroutine cancelled.")
        raise
    except Exception as e:
        logger.error(f"[DIAG] Error in entrypoint execution: {e}", exc_info=True)
        context_data = {
            "Room Name": getattr(ctx.room, "name", "N/A"),
            "Job ID": getattr(ctx.job, "id", "N/A"),
            "Process ID (PID)": os.getpid(),
        }
        try:
            if ctx.job.metadata:
                context_data["Job metadata"] = ctx.job.metadata
        except Exception:
            pass
        try:
            await send_crash_email(
                service_name="Livekit Voice Agent worker",
                error=e,
                context_data=context_data,
            )
        except Exception as email_err:
            logger.error(f"[DIAG] Failed to dispatch crash email: {email_err}")
    finally:
        logger.info("[DIAG] ======== ENTERING FINALLY BLOCK ========")
        logger.info(f"[DIAG] connection_state={ctx.room.connection_state} user_joined={call_state.get('user_joined')} agent_state={call_state.get('agent_state','unknown')}")
        # 1. Cancel background tasks
        for task_name in [
            "limiter_task",
            "inactivity_task",
            "goodbye_task",
            "safety_net_task",
            "transcript_task",
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
            if call_state.get("_finalized"):
                logger.info("[DIAG] finalize(): Call already finalized — skipping duplicate execution")
                return
            call_state["_finalized"] = True
            post_call_llm = build_post_call_llm()

            recording_url = None
            transcript_data = None
            summary_text = None
            tos_sent = False
            duration = 0
            call_status = "Failed"
            next_call_on = None
            current_stage_id = None
            new_stage_id = None
            derived_process_id = None
            client_custom_fields = {}
            call_payload = {}
            webhook_payload = {}
            delivered = False

            try:
                logger.info("[DIAG] finalize(): Starting post-call processing...")
                if "timeline" in call_state:
                    call_state["timeline"].append({"event": "Call Finalization Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
                await _telemetry("Post-call processing started")

                # 1. Pre-load call metadata
                logger.info("[DIAG] finalize(): Step 1 — Loading call metadata...")
                try:
                    if _effective_call_metadata:
                        call_payload = dict(_effective_call_metadata)
                        logger.info(f"[DIAG] finalize(): Using _effective_call_metadata with {len(call_payload)} keys")
                    else:
                        call_payload = (
                            json.loads(ctx.job.metadata) if (ctx.job and ctx.job.metadata) else {}
                        )
                        logger.info(f"[DIAG] finalize(): Parsed raw job metadata with {len(call_payload)} keys")
                except Exception as e:
                    logger.error(f"[DIAG] finalize(): Failed to parse call metadata: {e}")

                # For inbound calls, store KB tracked process_id and stage_id hints
                if call_payload.get("direction") == "inbound":
                    try:
                        if fnc_ctx and hasattr(fnc_ctx, "used_kb_process_ids"):
                            used_pids = fnc_ctx.used_kb_process_ids
                            if used_pids:
                                call_payload["kb_tracked_process_id"] = used_pids[0]
                                logger.info(f"KB-tracked process_id hint for inbound: {used_pids[0]}")
                        if fnc_ctx and hasattr(fnc_ctx, "used_kb_stage_ids"):
                            used_sids = fnc_ctx.used_kb_stage_ids
                            if used_sids:
                                call_payload["kb_tracked_stage_id"] = used_sids[0]
                                logger.info(f"KB-tracked stage_id hint for inbound: {used_sids[0]}")
                    except Exception as e:
                        logger.error(f"Failed to extract KB usage metadata: {e}")

                # Determine call status based on whether user joined and spoke
                user_spoke = False
                for msg in history_snapshot:
                    role = msg.role.name if hasattr(msg.role, "name") else str(msg.role)
                    if role.lower() == "user":
                        user_spoke = True
                        break

                call_id = call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")
                logger.info(f"[DIAG] finalize(): user_joined={call_state.get('user_joined')} user_spoke={user_spoke} history_size={len(history_snapshot)}")

                is_inbound = (call_payload.get("direction") == "inbound")
                is_user_joined = bool(call_state.get("user_joined") or is_inbound)

                if not is_user_joined:
                    initiated_str = call_state.get("call_initiated_at") or (call_payload.get("metadata", {}) or {}).get("call_initiated_at")
                    ring_time = 0
                    if initiated_str:
                        try:
                            initiated = datetime.datetime.strptime(initiated_str, "%Y-%m-%dT%H:%M:%S")
                            ring_time = (datetime.datetime.now() - initiated).total_seconds()
                        except (ValueError, TypeError):
                            pass
                    logger.info(f"[DIAG] finalize(): ring_time={ring_time:.0f}s (from call_initiated_at={initiated_str})")
                    if ring_time >= 30:
                        call_status = "No Answer"
                    elif ring_time >= 3:
                        call_status = "Busy"
                    else:
                        call_status = "Failed"
                elif not user_spoke and not is_inbound:
                    call_status = "No Answer"
                else:
                    call_status = "Completed"
                logger.info(f"[DIAG] finalize(): call_status determined as '{call_status}' (is_inbound={is_inbound}, user_spoke={user_spoke})")

                # 2. Flush recording tasks and upload to S3 (bounded by 10s timeout)
                logger.info(f"[DIAG] finalize(): Step 2 — Stopping recording...")
                try:
                    if recorder and hasattr(recorder, "stop_recording"):
                        await recorder.stop_recording()
                        logger.info(f"[DIAG] finalize(): Recording stopped. track_count={len(getattr(recorder, '_tracks', []))}")
                        mp3_bytes = recorder.get_combined_mp3_bytes()
                        if mp3_bytes:
                            logger.info(f"[DIAG] finalize(): Got {len(mp3_bytes)} bytes of MP3 audio, uploading to S3...")
                            call_id_for_key = (
                                call_payload.get("call_id")
                                or call_payload.get("voice_id")
                                or (ctx.job.id if ctx.job else "unknown")
                            )
                            s3_key = f"recordings/{call_id_for_key}.mp3"
                            loop = asyncio.get_running_loop()
                            recording_url = await asyncio.wait_for(
                                loop.run_in_executor(None, upload_to_s3, mp3_bytes, s3_key),
                                timeout=10.0
                            )
                            logger.info(f"[DIAG] finalize(): S3 recording: {'uploaded' if recording_url else 'upload failed'}")
                        else:
                            logger.info("[DIAG] finalize(): No audio data captured for recording")
                except asyncio.TimeoutError:
                    logger.warning("[DIAG] finalize(): S3 recording upload timed out after 10s — proceeding without recording_url")
                except Exception as e:
                    logger.error(f"[DIAG] finalize(): Recording/S3 step failed: {e}", exc_info=True)

                # 3. Build transcript from captured history snapshot
                logger.info(f"[DIAG] finalize(): Step 3 — Building transcript from {len(history_snapshot)} messages...")
                try:
                    transcript_data = SessionRecorder.build_transcript(
                        list(history_snapshot)
                    )
                    logger.info(f"[DIAG] finalize(): Transcript built ({len(history_snapshot)} messages, {len(transcript_data or '')} chars)")
                except Exception as e:
                    logger.error(f"[DIAG] finalize(): Transcript step failed: {e}", exc_info=True)

                # 4. Calculate duration
                if recorder and hasattr(recorder, "recording_duration_seconds"):
                    duration = int(recorder.recording_duration_seconds)

                # 5. Run unified analysis (bounded by 70s timeout)
                direction = call_payload.get("direction")
                if direction == "inbound":
                    try:
                        if fnc_ctx and hasattr(fnc_ctx, "used_kb_process_ids"):
                            used_pids = fnc_ctx.used_kb_process_ids
                            if used_pids and not call_payload.get("kb_tracked_process_id"):
                                call_payload["kb_tracked_process_id"] = used_pids[0]
                                logger.info(f"Using KB-tracked process_id hint for inbound before analysis: {used_pids[0]}")
                        if fnc_ctx and hasattr(fnc_ctx, "used_kb_stage_ids"):
                            used_sids = fnc_ctx.used_kb_stage_ids
                            if used_sids and not call_payload.get("kb_tracked_stage_id"):
                                call_payload["kb_tracked_stage_id"] = used_sids[0]
                                logger.info(f"Using KB-tracked stage_id hint for inbound before analysis: {used_sids[0]}")
                    except Exception as e:
                        logger.error(f"Failed to extract KB usage metadata before analysis: {e}")

                current_stage_id = call_payload.get("stage_id") or call_payload.get("kb_tracked_stage_id")
                stage_details = call_payload.get("stageDetails", [])
                kb_process_stage_data = (
                    fnc_ctx.used_process_stage_data 
                    if (fnc_ctx and hasattr(fnc_ctx, 'used_process_stage_data') and fnc_ctx.used_process_stage_data) 
                    else None
                )
                if not kb_process_stage_data and fnc_ctx and hasattr(fnc_ctx, 'kb_ids') and fnc_ctx.kb_ids:
                    try:
                        kb = get_global_kb()
                        kb_process_stage_data = await kb.get_process_stage_data_for_kb_ids(fnc_ctx.kb_ids)
                        if kb_process_stage_data:
                            logger.info(f"Loaded {len(kb_process_stage_data)} process_stage_data entries from DB for KB ids: {fnc_ctx.kb_ids}")
                    except Exception as e:
                        logger.error(f"Failed to fetch fallback KB process_stage_data from DB: {e}")

                summary_text = None
                new_stage_id = current_stage_id
                llm_analysis_ran = False
                derived_process_id = None
                derived_user_intent = None
                client_custom_fields = call_payload.get("client_custom_fields", {})
                if not isinstance(client_custom_fields, dict):
                    client_custom_fields = {}

                if call_status in ["Busy", "Incomplete", "No Answer"]:
                    logger.info(
                        f"[DIAG] finalize(): Call status is {call_status}. Skipping LLM analysis."
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
                else:
                    try:
                        target_llm = post_call_llm or llm_engine
                        if target_llm and history_snapshot:
                            logger.info(f"[DIAG] finalize(): Step 5 — Running analyze_call with {len(list(history_snapshot))} messages...")
                            client_country_code = call_payload.get("client_country_code") or call_payload.get("country_code", "")
                            
                            analysis = await asyncio.wait_for(
                                SessionRecorder.analyze_call(
                                    llm_engine=target_llm,
                                    history=list(history_snapshot),
                                    current_stage_id=current_stage_id,
                                    stage_details=stage_details,
                                    duration=duration,
                                    client_country_code=client_country_code,
                                    process_stage_data=kb_process_stage_data,
                                ),
                                timeout=70.0
                            )
                            summary_text = analysis["summary"]
                            new_stage_id = analysis["new_stage_id"]
                            llm_analysis_ran = True
                            derived_process_id = analysis.get("process_id")
                            derived_user_intent = analysis.get("user_intent")
                            extracted_client_name = analysis.get("client_name")

                            if extracted_client_name:
                                clean_name = str(extracted_client_name).strip()
                                if clean_name and clean_name.lower() not in ["user", "unknown", "n/a", "none", "null", ""]:
                                    curr_name = str(call_payload.get("client_name") or "").strip()
                                    if not curr_name or curr_name.lower() in ["user", "unknown", "n/a"]:
                                        call_payload["client_name"] = clean_name
                                        logger.info(f"[DIAG] finalize(): Extracted client_name from call analysis: {clean_name}")

                            if derived_process_id:
                                call_payload["process_id"] = derived_process_id
                            elif not call_payload.get("process_id") and call_payload.get("kb_tracked_process_id"):
                                call_payload["process_id"] = call_payload.get("kb_tracked_process_id")

                            next_call_on = normalize_datetime(analysis["next_call_on"])

                            if analysis.get("appointment_date_time"):
                                client_custom_fields["appointment_date_time"] = analysis["appointment_date_time"]
                            if analysis.get("doctor"):
                                client_custom_fields["doctor"] = analysis["doctor"]
                            if analysis.get("hospital_location"):
                                client_custom_fields["hospital_location"] = analysis["hospital_location"]

                            logger.info(
                                f"Analysis completed. Process: {derived_process_id}, New Stage ID: {new_stage_id}, Next Call On: {next_call_on}, User Intent: {derived_user_intent}, Client Name: {call_payload.get('client_name')}"
                            )
                        else:
                            logger.warning(
                                "Skipping analysis: LLM or history unavailable after session close"
                            )
                    except asyncio.TimeoutError:
                        logger.warning("[DIAG] finalize(): analyze_call timed out — using fallback summary")
                        summary_text = "Call completed. Summary timed out during processing."
                    except Exception as e:
                        logger.error(
                            f"Analysis or summary generation failed: {e}", exc_info=True
                        )

                # Final fallback guarantee for summary_text if missing or empty
                if not summary_text or not str(summary_text).strip():
                    if transcript_data and transcript_data.strip():
                        summary_text = f"Call completed ({duration}s). Transcript snippet: {transcript_data[:180]}..."
                    else:
                        summary_text = "Call completed."

            except Exception as e:
                logger.error(f"[DIAG] finalize(): Pipeline error in finalize: {e}", exc_info=True)

            # 6. Build webhook payload — separate structures for inbound vs outbound
            resolved_call_id = call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")

            kb_referred = bool(direction == "inbound" and (call_payload.get("process_id") or call_payload.get("stage_id") or call_payload.get("kb_tracked_process_id") or derived_process_id))
            if direction == "inbound":
                effective_process_id = _as_int(derived_process_id or call_payload.get("process_id") or call_payload.get("kb_tracked_process_id")) if kb_referred else None
            else:
                effective_process_id = _as_int(derived_process_id or call_payload.get("process_id") or call_payload.get("kb_tracked_process_id"))

            initial_stage_id = _as_int(current_stage_id if current_stage_id is not None else call_payload.get("stage_id") or call_payload.get("kb_tracked_stage_id"))
            analysis_stage_id = _as_int(new_stage_id) if new_stage_id is not None else None

            payload_stage_id = initial_stage_id
            if analysis_stage_id is not None:
                payload_new_stage_id = analysis_stage_id
            else:
                payload_new_stage_id = initial_stage_id

            # Reconcile effective_process_id and payload_new_stage_id against kb_process_stage_data
            if kb_process_stage_data:
                effective_process_id, payload_new_stage_id = reconcile_process_and_stage_id(
                    process_id=effective_process_id,
                    stage_id=payload_new_stage_id,
                    process_stage_data=kb_process_stage_data,
                )


            # Enforce stage-based call status rule:
            # If payload_new_stage_id == initial_stage_id (not updated) -> Incomplete
            # If payload_new_stage_id != initial_stage_id (updated) -> Completed
            if call_status not in ["No Answer", "Busy", "Failed"]:
                if initial_stage_id is not None and payload_new_stage_id != initial_stage_id:
                    call_status = "Completed"
                    logger.info(f"[DIAG] finalize(): Stage updated from {initial_stage_id} to {payload_new_stage_id} — call_status='Completed'")
                elif initial_stage_id is None and payload_new_stage_id is not None:
                    call_status = "Completed"
                    logger.info(f"[DIAG] finalize(): New stage assigned ({payload_new_stage_id}) with no initial stage — call_status='Completed'")
                else:
                    call_status = "Incomplete"
                    logger.info(f"[DIAG] finalize(): Stage not updated (new_stage_id={payload_new_stage_id}, initial={initial_stage_id}) — call_status='Incomplete'")

            if direction == "inbound":
                raw_caller_phone = call_state.get("caller_phone_number") or call_payload.get("client_phone_number") or call_payload.get("client_phone") or ""
                cc_code = call_payload.get("client_country_code") or call_payload.get("country_code") 
                formatted_caller_phone = format_e164_phone_number(raw_caller_phone, country_code=cc_code)

                webhook_payload = {
                    "event": "CALL_DATA_INBOUND_UPDATE",
                    "data": {
                        "org_id": _as_int(call_payload.get("org_id")),
                        "call_recording": recording_url or "",
                        "process_id": effective_process_id,
                        "stage_id": payload_stage_id,
                        "new_stage_id": payload_new_stage_id,
                        "call_status": call_status,
                        "client_name": call_payload.get("client_name") or "",
                        "client_email": call_payload.get("client_email") or "",
                        "client_phone_number": formatted_caller_phone,
                        "call_duration": duration,
                        "call_transcript": transcript_data or "",
                        "ai_summary": summary_text or "",
                        "next_call_on": normalize_datetime(next_call_on) or "",
                        "called_on": call_state.get("call_initiated_at") or call_state.get("agent_joined_at") or "",
                        "user_intent": derived_user_intent,
                        "call_intent": derived_user_intent,
                        "meta_data": {
                            "document_id": str(call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")),
                            "provider": (call_payload.get("metadata", {}) or {}).get("provider", ""),
                        },
                    }
                }
            else:
                event_name = "CALL_RETRY" if call_status in ["No Answer", "Busy", "Failed"] else "CALL_DATA_UPDATE"
                if event_name == "CALL_RETRY":
                    webhook_payload = {
                        "event": event_name,
                        "data": {
                            "call_id": resolved_call_id,
                            "called_on": call_state.get("call_initiated_at"),
                            "call_status": call_status,
                            "ai_call_id": ctx.job.id if ctx.job else "",
                        },
                    }
                else:
                    webhook_payload = {
                        "event": event_name,
                        "data": {
                            "client_id": call_payload.get("lead_id"),
                            "call_id": resolved_call_id,
                            "call_status": call_status,
                            "call_transcript": transcript_data,
                            "ai_summary": summary_text,
                            "recording_url": recording_url,
                            "call_duration_seconds": duration,
                            "next_call_on": normalize_datetime(next_call_on) or "",
                            "called_on": call_state.get("call_initiated_at") or call_state.get("agent_joined_at") or None,
                            "ai_call_id": ctx.job.id if ctx.job else "",
                            "process_id": effective_process_id,
                            "stage_id": payload_stage_id,
                            "new_stage_id": payload_new_stage_id,
                            "user_intent": derived_user_intent,
                            "call_intent": derived_user_intent,
                            "metadata": call_payload.get("metadata", {}),
                            "client_custom_fields": client_custom_fields or {},
                            "call_custom_fields": call_payload.get("call_custom_fields", {}),
                        },
                    }

            # 8. Send to MantraAssist backend and save to local DB
            logger.info(f"[DIAG] finalize(): Step 8 — Saving to DB and delivering webhook...")
            try:
                # Save to local Postgres DB
                try:
                    c_id = webhook_payload.get("data", {}).get("call_id", (ctx.job.id if ctx.job else ""))
                    caller_number = call_payload.get("call_from") or call_payload.get("caller_number") or call_state.get("caller_phone_number") or ""
                    called_number = call_payload.get("client_phone") or call_payload.get("client_phone_number") or call_payload.get("called_number") or ""
                    call_trunk_id = call_payload.get("call_from_id") or call_payload.get("trunk_id") or ""
                    await save_call_log_to_db(
                        call_id=str(c_id),
                        call_log=json.dumps(webhook_payload.get("data", {}), indent=2),
                        status=call_status,
                        recording_url=recording_url,
                        caller_number=caller_number,
                        called_number=called_number,
                        trunk_id=call_trunk_id,
                    )
                    logger.info(f"[DIAG] finalize(): Call log saved to DB for call_id={c_id}")
                except Exception as db_err:
                    logger.error(f"[DIAG] finalize(): Error calling save_call_log_to_db: {db_err}")

                logger.info("[DIAG] finalize(): Queueing webhook to UI Server via Redis...")
                try:
                    import redis.asyncio as redis
                    redis_url = os.getenv("REDIS_URL")
                    if redis_url:
                        client = redis.from_url(redis_url, decode_responses=True)
                        await client.rpush("mantra:pending_webhooks", json.dumps(webhook_payload))
                        await client.aclose()
                        delivered = True
                        logger.info(f"[DIAG] finalize(): Webhook queued to UI Server successfully (call_id={c_id})")
                    else:
                        logger.warning("[DIAG] finalize(): REDIS_URL not set. Falling back to synchronous HTTP delivery.")
                        delivered = await send_to_backend(webhook_payload)
                except Exception as e:
                    logger.error(f"[DIAG] finalize(): Redis queueing failed, falling back to HTTP: {e}")
                    delivered = await send_to_backend(webhook_payload)

                tos_sent = True
                await _telemetry(f"data_sent_to_backend — status={call_status}, queued_to_redis={'yes' if delivered else 'no'}")

                # Log backend delivery event to audit trail
                backend_cid = resolved_call_id or (ctx.job.id if ctx.job else "")
                await save_call_event(
                    call_id=str(backend_cid),
                    event_type="backend_sent" if delivered else "backend_failed",
                    event_source="agent",
                    event_payload={k: v for k, v in webhook_payload.items() if k != "prompt"},
                    event_status="success" if delivered else "failed",
                    event_log=f"status={call_status} duration={duration}s {'delivered' if delivered else 'failed'}",
                )
            except Exception as e:
                logger.error(f"[DIAG] finalize(): Webhook delivery failed: {e}", exc_info=True)
                delivered = False
                try:
                    await save_call_event(
                        call_id=str(resolved_call_id or (ctx.job.id if ctx.job else "")),
                        event_type="backend_failed",
                        event_source="agent",
                        event_payload={"event": webhook_payload.get("event", "unknown")},
                        event_status="failed",
                        event_error=str(e)[:500],
                        event_log=f"status={call_status} error={str(e)[:200]}",
                    )
                except Exception:
                    pass

            await _telemetry(f"call_complete — status={call_status}, duration={duration}s")

            # Call has ended — release call lock in Redis so future retry attempts for call_id are allowed
            try:
                redis_url = os.getenv("REDIS_URL")
                if redis_url and c_id:
                    import redis.asyncio as redis
                    r_client = redis.from_url(redis_url, decode_responses=True)
                    await r_client.delete(f"lock:call:{c_id}")
                    await r_client.aclose()
                    logger.info(f"[DIAG] finalize(): Cleared lock:call:{c_id}")
            except Exception as lock_err:
                logger.warning(f"[DIAG] finalize(): Failed to clear call lock: {lock_err}")

            logger.info(
                f"[DIAG] ======== POST-CALL COMPLETE ========\n"
                f"  Call ID: {ctx.job.id if ctx.job else 'N/A'}\n"
                f"  Lead: {webhook_payload.get('data', {}).get('client_id', 'N/A')}\n"
                f"  Status: {webhook_payload.get('data', {}).get('call_status', 'N/A')}\n"
                f"  Duration: {duration}s\n"
                f"  S3: {'✓' if recording_url else '✗'}\n"
                f"  Backend: {'✓' if delivered else '✗'}\n"
                f"  TOS: {'✓' if tos_sent else '✗'}\n"
                f"  Transcript length: {len(transcript_data or '')} chars\n"
                f"  Summary: {summary_text[:200] if summary_text else 'None'}"
            )

        await asyncio.shield(finalize())


async def _force_disconnect_room(ctx: JobContext):
    """Delete the room via LiveKit API. Falls back to local disconnect."""
    lk_api = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )
    try:
        await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        # logger.info(f"{Fore.RED}➖ Room Destroyed via API: {ctx.room.name}{Style.RESET_ALL}")
    except Exception as e:
        logger.error(f"Failed to delete room via API: {e}")
        try:
            await ctx.room.disconnect()
            # logger.info(f"{Fore.RED}➖ Room Disconnected locally: {ctx.room.name}{Style.RESET_ALL}")
        except Exception as e2:
            logger.error(f"Local disconnect also failed: {e2}")
    finally:
        await lk_api.aclose()


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
