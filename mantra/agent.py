"""Kettle Voice Agent worker — LiveKit agent server and per-call orchestration.

Heavy lifting lives in ``mantra.core.*`` and ``mantra.prompts``; this module
owns the AgentServer, environment startup, and the entrypoint that wires a
CallContext and starts the monitors.
"""
import logging
import json
import asyncio
import os
import datetime
import sys

from livekit import rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
)
from livekit.plugins import silero

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

# Also suppress noisy OTEL SDK logs once the SDK initialises
logging.getLogger("opentelemetry").setLevel(logging.ERROR)

from dotenv import load_dotenv

from mantra.email_alerts import send_crash_email
from mantra.utils import (
    SessionRecorder,
    save_call_event,
    report_telemetry,
)

from mantra.amd import detect_voicemail

from mantra.call_duration import (
    current_limits,
    extend_call,
)
from mantra.positive_intent import should_extend_from_history

from mantra.core.common import CallContext, create_bg_task, get_global_kb
from mantra.core.engines import (
    build_language_manager,
    build_live_llm_engine,
    build_stt_engine,
    build_tts_engine,
    select_model_and_voice,
)
from mantra.core.inbound import (
    _extract_livekit_caller_phone,
    recognize_inbound_client,
    resolve_inbound_context,
    resolve_outbound_context,
)
from mantra.core.assistant_functions import AssistantFunctions
from mantra.core.live_agent import make_multilingual_agent
from mantra.core.call_monitors import (
    call_limiter,
    farewell_safety_net,
    inactivity_monitor,
    positive_intent_monitor,
    register_room_handlers,
    register_session_handlers,
    transcript_logger,
)
from mantra.core.finalize import finalize
from mantra.core.room_control import _force_disconnect_room
from mantra.prompts import (
    BASE_INSTRUCTIONS,
    apply_language_directive,
    build_initial_instructions,
)

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
        "timeline": [{"event": "Agent Session Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}],
        "entrypoint_start_time": entrypoint_start_time,
        "duration_extended": False,
        "extension_event": asyncio.Event(),
        "farewell_triggered": False,
        "original_instructions": None,
        "is_inbound": False,
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
        except Exception:
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
    payload = None

    if ctx.job.metadata:
        try:
            meta_payload = json.loads(ctx.job.metadata)

            if meta_payload.get("direction") == "inbound":
                # Dispatch metadata contains the org-bound DID. The real caller
                # number is read from the LiveKit SIP participant after joining.
                routing_phone = meta_payload.get("phone_number", "")
                logger.info(f"[DIAG] Inbound call detected — routing phone={routing_phone or '<none>'}")
                if routing_phone:
                    resolved_context = await resolve_inbound_context(routing_phone)
                    if resolved_context:
                        meta_payload.update(resolved_context)
                        if resolved_context.get("org_id"):
                            call_state["org_id"] = resolved_context.get("org_id")
                        logger.info(f"[DIAG] Inbound context merged: org_id={resolved_context.get('org_id')}")
                    else:
                        logger.warning(f"[DIAG] Inbound resolution failed for {routing_phone} — using dispatch rule defaults, call WILL connect")
                else:
                    logger.warning("[DIAG] Inbound call has no routing phone in dispatch metadata")
            elif meta_payload.get("direction") == "outbound":
                org_id = meta_payload.get("org_id")
                logger.info(f"[DIAG] Outbound call detected — org_id={org_id}, resolving KB...")
                if org_id:
                    call_state["org_id"] = str(org_id)
                    if not meta_payload.get("kb_ids"):
                        outbound_ctx = await resolve_outbound_context(str(org_id))
                        if outbound_ctx and outbound_ctx.get("kb_ids"):
                            existing_ids = set([str(k) for k in meta_payload.get("kb_ids", []) if k])
                            merged_ids = list(set([str(k) for k in outbound_ctx["kb_ids"] if k] + list(existing_ids)))
                            if not meta_payload.get("kb_ids"):
                                meta_payload["kb_ids"] = merged_ids
                            else:
                                for kid in outbound_ctx["kb_ids"]:
                                    if str(kid) not in existing_ids:
                                        meta_payload["kb_ids"].append(str(kid))
                            if outbound_ctx.get("kb_tags") and not meta_payload.get("kb_tags"):
                                meta_payload["kb_tags"] = outbound_ctx["kb_tags"]
                            if outbound_ctx.get("org_id"):
                                call_state["org_id"] = outbound_ctx["org_id"]
                            logger.info(f"[DIAG] Outbound KB context merged: kb_ids={meta_payload.get('kb_ids')}, kb_tags={meta_payload.get('kb_tags')}")
                        else:
                            logger.warning(f"[DIAG] Outbound KB resolution returned no kb_ids for org_id={org_id}")
                else:
                    logger.warning("[DIAG] Outbound call has no org_id — KB will be empty unless kb_ids provided directly")

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

    if call_state.get("org_id"):
        try:
            org_id = str(call_state["org_id"])
            org_kb_ids = await get_global_kb().get_kb_ids_for_org(org_id)
            kb_ids_list = list(set(kb_ids_list + [str(kb_id) for kb_id in org_kb_ids if kb_id]))
            logger.info(f"[DIAG] Expanded org {org_id} to KB collections: {org_kb_ids}")
        except Exception as e:
            logger.warning(f"Failed to expand KB collections for org {call_state['org_id']}: {e}")

    logger.info(f"KB scope: kb_ids={kb_ids_list}, kb_tags={kb_tags_list}")

    fnc_ctx = AssistantFunctions(
        json.dumps(meta_payload) if ctx.job.metadata else "",
        ctx.room.name,
        ctx=ctx,
        kb_ids=kb_ids_list,
        kb_tags=kb_tags_list,
        call_state=call_state,
    )
    if call_state.get("org_id"):
        create_bg_task(fnc_ctx._load_department_options(call_state["org_id"]))
    create_bg_task(fnc_ctx.warmup())

    # Session ID for S3 key naming
    session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # NOTE: Redis logic for concurrency management and call tracking has been removed.

    # Fully in-memory recorder — no disk I/O
    recorder = SessionRecorder()

    # CallContext created early so room handlers register before any long
    # engine-building work; fields are filled in as each component is built.
    cc = CallContext(
        ctx=ctx,
        session=None,
        agent=None,
        fnc_ctx=fnc_ctx,
        call_state=call_state,
        recorder=recorder,
        language_mgr=None,
        tts_engine=None,
        stt_engine=None,
        keyterm_memory=None,
        llm_engine=None,
        entrypoint_start_time=entrypoint_start_time,
        is_inbound=False,
        client_name="User",
        voice_id="",
        effective_call_metadata=None,
        telemetry=_telemetry,
        agent_name=AGENT_NAME,
    )
    register_room_handlers(cc)

    initial_instructions = BASE_INSTRUCTIONS  # literal base; metadata block builds on it
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
            initial_instructions, client_name = build_initial_instructions(payload, is_inbound=is_inbound)

            logger.info(f"Loaded full context for {client_name} (inbound: {is_inbound})")
            call_state["is_inbound"] = is_inbound

        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")

    call_state["is_inbound"] = is_inbound
    cc.is_inbound = is_inbound
    cc.client_name = client_name
    cc.effective_call_metadata = _effective_call_metadata

    # 3. Select LLM and Voice based on payload
    model_name, voice_input, voice_id, voice_speed = select_model_and_voice(payload)

    # Explicit logs for call configuration
    logger.info("--- CALL CONFIGURATION ---")
    logger.info(f"Model: {model_name}")
    logger.info(f"Voice: {voice_input} (ID: {voice_id})")
    logger.info(f"Speed: {voice_speed}")
    logger.info("--------------------------")

    cc.voice_id = voice_id

    llm_engine, client = build_live_llm_engine(model_name)

    # Language Manager & TTS via LiveKit Inference — Cartesia provider
    language_mgr, raw_lang, requested_lang, response_mode = build_language_manager(payload)
    language = language_mgr.get_current_language()
    call_state["current_language"] = language

    cc.language_mgr = language_mgr

    # Insert dynamic language directive into initial prompt instructions
    directive_block = f"<!-- LANGUAGE_DIRECTIVE_START -->\n{language_mgr.get_prompt_directive()}\n<!-- LANGUAGE_DIRECTIVE_END -->"
    initial_instructions = apply_language_directive(initial_instructions, directive_block)

    logger.info(f"[LANG] Initialized language state: '{language}' (Voice: {voice_id} | Speed: {voice_speed})")

    # Fire DeepSeek pre-warm NOW — initial_instructions is fully built.
    # Passing the real system prompt warms DeepSeek's KV prefix cache so the
    # first actual turn (e.g. user says "Yes") reuses the cached prefix
    # instead of recomputing the entire context → eliminates 2-3s TTFT on turn 2.
    if model_name == "deepseek" and client is not None:
        async def _prewarm_deepseek_with_ctx():
            try:
                logger.info("[DEEPSEEK] Pre-warming KV cache with system prompt...")
                pw_start = asyncio.get_event_loop().time()
                await client.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[
                        {"role": "system", "content": initial_instructions},
                        {"role": "user", "content": "hi"},
                    ],
                    max_tokens=1,
                )
                pw_dur = (asyncio.get_event_loop().time() - pw_start) * 1000
                logger.info(f"[DEEPSEEK] KV cache pre-warmed with system prompt in {pw_dur:.1f}ms")
            except Exception as pw_err:
                logger.warning(f"[DEEPSEEK] Pre-warm (with ctx) non-fatal error: {pw_err}")
        create_bg_task(_prewarm_deepseek_with_ctx())

    tts_engine = build_tts_engine(voice_id, language, voice_speed)
    cc.tts_engine = tts_engine

    stt_engine, stt_lang, dynamic_keyterms, keyterm_memory = build_stt_engine(
        payload, call_state, is_inbound, language, requested_lang
    )

    cc.stt_engine = stt_engine
    cc.keyterm_memory = keyterm_memory
    cc.llm_engine = llm_engine

    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={
                "mode": "dynamic",
                "min_delay": 0.10,
                "max_delay": 0.80,
            },
            interruption={
                "mode": "vad",
                "enabled": True,
                "discard_audio_if_uninterruptible": True,
                "min_words": 1,
                "min_duration": 0.15,
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.0,
                "backchannel_boundary": None,
            },
            preemptive_generation={
                "preemptive_tts": True,
            },
        ),
        vad=silero.VAD.load(
            min_speech_duration=0.08,
            min_silence_duration=0.25,
            prefix_padding_duration=0.10,
        ),
        stt=stt_engine,
        llm=llm_engine,
        tts=tts_engine,
    )

    await _telemetry("Agent voice engine ready", f"model={model_name}")

#AGENT TOOLS TO BE MENTIONED HERE!!!!

    agent_tools = [
        fnc_ctx.end_call,
        fnc_ctx.search_knowledge_base,
        fnc_ctx.clarify_medical_department,
        fnc_ctx.check_doctor_availability,
    ]

    agent = make_multilingual_agent(
        instructions=initial_instructions,
        tools=agent_tools,
        language_mgr=language_mgr,
        call_state=call_state,
        tts_engine=tts_engine,
        voice_id=voice_id,
    )
    fnc_ctx.agent = agent
    fnc_ctx.session = session
    call_state["_agent_ref"] = agent
    call_state["original_instructions"] = initial_instructions

    cc.session = session
    cc.agent = agent

    # ── Transcript logging & dynamic language switching task ─────────────
    transcript_task = asyncio.create_task(transcript_logger(cc))

    register_session_handlers(cc)

    try:
        logger.info(f"[DIAG] Starting agent session...")
        await session.start(agent=agent, room=ctx.room)
        logger.info(f"[DIAG] Session started successfully")
        
        workflow_json = payload.get("workflow") if payload else None
        if workflow_json:
            from mantra.core.workflow import LivekitWorkflowEngine, run_workflow
            workflow_engine = LivekitWorkflowEngine(workflow_json, cc, payload)
            workflow_task = asyncio.create_task(run_workflow(workflow_engine))
            logger.info(f"[DIAG] LiveKit Workflow Engine initialized and started.")
            
        limiter_task = asyncio.create_task(call_limiter(cc))
        inactivity_task = asyncio.create_task(inactivity_monitor(cc))
        safety_net_task = asyncio.create_task(farewell_safety_net(cc))
        intent_task = asyncio.create_task(positive_intent_monitor(cc))

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

        if is_inbound and call_state.get("org_id"):
            livekit_caller_phone = _extract_livekit_caller_phone(ctx.room.remote_participants.values())
            if livekit_caller_phone:
                call_state["caller_phone_number"] = livekit_caller_phone
                logger.info(
                    "[DIAG] LiveKit caller metadata resolved — caller=%s, org_id=%s; recognizing client",
                    livekit_caller_phone,
                    call_state["org_id"],
                )
                recognized_client = await recognize_inbound_client(
                    phone_number=livekit_caller_phone,
                    org_id=call_state["org_id"],
                )
                if recognized_client:
                    recognized_name = recognized_client["client_name"]
                    client_metadata = recognized_client["client_metadata"]
                    client_name = recognized_name
                    if isinstance(payload, dict):
                        payload["client_name"] = recognized_name
                        payload["client_metadata"] = client_metadata
                    metadata_lines = []
                    for summary in client_metadata["ai_summaries"]:
                        if isinstance(summary, dict):
                            date = summary.get("date", "")
                            text = summary.get("summary", "")
                            metadata_lines.append(f"- {date}: {text}" if date else f"- {text}")
                    custom_fields = client_metadata["custom_fields"]
                    if custom_fields:
                        metadata_lines.append("Custom fields:")
                        for field in custom_fields:
                            if isinstance(field, dict):
                                field_name = field.get("custom_field_name", "field")
                                field_value = field.get("custom_field_value", "")
                                metadata_lines.append(f"- {field_name}: {field_value}")
                    metadata_context = "\n".join(metadata_lines) or "No additional client metadata provided."
                    try:
                        await agent.update_instructions(
                            agent.instructions
                            + "\n\n--- VERIFIED CALLER IDENTITY ---\n"
                            + f"The caller is a recognized client named {recognized_name}.\n"
                            + "The following private client metadata is available as call context. Use it only when relevant and never mention the lookup or metadata source.\n"
                            + metadata_context
                            + "\n"
                            + f"Address the caller as {recognized_name} naturally when appropriate.\n"
                            + "Do not ask for the caller's name. The inbound caller identity is already verified.\n"
                            + "Do not describe or reveal the recognition lookup to the caller.\n"
                        )
                        logger.info(
                            "[DIAG] Live agent instructions updated with recognized client=%s",
                            recognized_name,
                        )
                    except Exception as instruction_error:
                        logger.warning(
                            "[DIAG] Could not update instructions with recognized client=%s: %s",
                            recognized_name,
                            instruction_error,
                        )
                    logger.info(
                        "[DIAG] Client recognition succeeded — org_id=%s, client=%s",
                        call_state["org_id"],
                        recognized_name,
                    )
            else:
                logger.warning("[DIAG] LiveKit SIP participant had no caller phone metadata; client recognition skipped")

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
                                    "yourself by name and ask them to call back. Keep it brief."
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
            "intent_task",
            "workflow_task",
        ]:
            task = locals().get(task_name)
            if task and not task.done():
                task.cancel()

        # 2. Capture history snapshot immediately before session cleans up
        history_snapshot = (
            list(session.history.messages()) if (session and session.history) else []
        )

        # 3. Shielded finalization
        await asyncio.shield(finalize(cc, history_snapshot))


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