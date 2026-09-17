"""The agent's function tools (end_call, KB search, medical routing, availability).

Extracted verbatim from the former mantra/agent.py module.
"""
import asyncio
import json
import logging
import os
from typing import Annotated, Any, Optional

from livekit import api
from livekit.agents import Agent, JobContext, llm

from mantra.core.common import create_bg_task, get_global_kb
from mantra.core.inbound import format_upfront_kb_context, format_upfront_process_context
from mantra.core.room_control import _force_disconnect_room
from mantra.knowledge_base import PostgresKnowledgeBase
from mantra.retriever import KnowledgeRetriever
from mantra.utils import report_telemetry

logger = logging.getLogger("mantra.assistant_functions")


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

    async def warmup(self):
        try:
            kb = await self._get_kb()
            await kb.warmup(self.kb_ids)
            retriever = await self._get_retriever()
            pages = await retriever.prefetch(self.kb_ids)
            process_context = await kb.get_process_stage_data_for_kb_ids(self.kb_ids)
            if self.agent:
                upfront_text = format_upfront_kb_context(pages)
                upfront_text += format_upfront_process_context(process_context)
                if upfront_text:
                    cur_inst = self.agent.instructions
                    if isinstance(cur_inst, str) and "<!-- UPFRONT_KB_START -->" not in cur_inst:
                        await self.agent.update_instructions(cur_inst + upfront_text)
                        logger.info(
                            f"[KB] Injected {len(pages)} KB pages and {len(process_context)} "
                            "process contexts into agent instructions"
                        )
        except Exception as e:
            logger.warning(f"[KB] AssistantFunctions warmup error: {e}")

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
    #     except Exception as e:
    #         logger.debug(f"Agent interrupt unavailable (non-fatal): {e}")
    #
    #     return "TRANSFER_COMPLETE. Do not speak."

    @llm.function_tool(
        description=(
            "Search the knowledge base for general factual information, doctor profiles, pricing, services, "
            "policies, and any entity or topic asked by the caller. Call this tool silently without saying search fillers "
            "(e.g., do NOT say 'Let me check' or 'Let me look that up'). Speak the retrieved answer directly. "
            "NEVER use this tool for doctor availability, open appointment slots, booking, rescheduling, cancellation, "
            "or doctor working hours; always use the dedicated MCP availability tool for those requests."
        )
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

    async def _load_department_options(self, org_id: int | str) -> list[str]:
        """Load and remember the departments available for this organization."""
        from mantra.mcp_client import get_mcp_client

        try:
            raw_departments = await get_mcp_client().call_tool(
                "get_org_departments",
                {"org_id": org_id},
            )
            departments = json.loads(raw_departments) if isinstance(raw_departments, str) else raw_departments
            if isinstance(departments, dict):
                departments = departments.get("departments") or departments.get("data") or []
            if not isinstance(departments, list):
                departments = []
            normalized = []
            for item in departments:
                name = str(item).strip()
                if name and name.casefold() not in {value.casefold() for value in normalized}:
                    normalized.append(name)
        except Exception as exc:
            logger.warning(f"Failed to fetch departments for org_id={org_id}: {exc}")
            normalized = []

        if self.call_state is not None:
            self.call_state["department_options"] = normalized
        return normalized

    @llm.function_tool(
        description=(
            "Use when the caller gives a broad medical symptom without a clear department or specialty, such as 'I have an eye problem'. "
            "Fetch the organization's department list silently, but do not guess a department from one vague symptom. "
            "Ask up to two concise clinical-routing questions before selecting a department. Ask about the symptom's onset, progression, severity, and any associated symptoms that distinguish the available specialties. "
            "Use the caller's answers and the full conversation to select the best exact value from the returned list. "
            "Do not ask the caller to choose a department or mention department names aloud. "
            "Do not use fixed symptom-to-department mappings or assume that a symptom always belongs to a particular specialty. "
            "Only call check_doctor_availability after the caller answers the necessary routing question(s)."
        )
    )
    async def clarify_medical_department(
        self,
        symptom: Annotated[str, "The caller's broad symptom or reason for the appointment."],
    ) -> str:
        org_id = self.call_state.get("org_id") if self.call_state else None
        if not org_id and self.job_metadata:
            try:
                payload = json.loads(self.job_metadata) if isinstance(self.job_metadata, str) else self.job_metadata
                org_id = payload.get("org_id") or (
                    payload.get("metadata", {}).get("org_id")
                    if isinstance(payload.get("metadata"), dict)
                    else None
                )
            except Exception as exc:
                logger.warning(f"Could not parse job_metadata in clarify_medical_department: {exc}")

        if not org_id:
            return "Ask the caller which specific eye or medical specialty they need, then continue without guessing a department."

        departments = await self._load_department_options(org_id)

        if self.call_state is not None:
            self.call_state["department_clarification_symptom"] = symptom.strip()

        if not departments:
            return (
                "Department discovery is unavailable for this organization. Do not ask the caller to choose a department "
                "and do not fall back to the knowledge base. Proceed directly by calling check_doctor_availability with "
                "the best department inferred from the conversation, or leave department empty if none is known."
            )

        return (
            f"INTERNAL ROUTING CONTEXT ONLY. Caller symptom: {symptom.strip()}. "
            f"Allowed departments: {json.dumps(departments)}. "
            "Do not select a department yet if the symptom could reasonably match more than one option. Ask up to two concise questions about onset, progression, severity, and associated symptoms, choosing the questions that best distinguish the returned options. "
            "After the caller answers, select the single best exact department using the full conversation context and the clinical evidence provided by the caller. "
            "Do not use a fixed symptom-to-department mapping, infer a department solely from one keyword, say the department list, ask the caller to choose a department, or explain the internal routing."
        )

    @llm.function_tool(
        description=(
            "Check doctor and healthcare provider availability, working hours, and open appointment slots on a specific date. "
            "This is the authoritative real-time MCP tool for appointment availability. Never use the knowledge base for this request. "
            "If the organization department list is available, the department must match one of its values. "
            "Never invent a generic department such as Ophthalmology when a department list is available. If the department is unknown, "
            "call clarify_medical_department first, then continue even if department discovery is unavailable. Never ask the caller to choose a department by name. "
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

        department_options = self.call_state.get("department_options", []) if self.call_state else []
        if not department_options and org_id:
            department_options = await self._load_department_options(org_id)

        requested_department = str(department).strip() if department else ""
        matched_department = next(
            (
                option
                for option in department_options
                if option.casefold() == requested_department.casefold()
            ),
            None,
        )
        if not matched_department and department_options:
            if self.call_state is not None:
                self.call_state["department_clarification_symptom"] = requested_department
            return (
                "Department selection is invalid. Call clarify_medical_department, choose one exact value from its "
                "returned allowed departments, and retry availability. Do not ask the caller to choose a department."
            )

        if self.call_state is not None:
            self.call_state["selected_department"] = matched_department or requested_department or None

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
        if self.call_state is not None and isinstance(result, str):
            import re
            m = re.search(r'User ID:\s*(\d+)', result, re.IGNORECASE) or re.search(r'Doctor ID:\s*(\d+)', result, re.IGNORECASE) or re.search(r'user_id[":\s]+(\d+)', result, re.IGNORECASE)
            if m:
                try:
                    self.call_state["provider_user_id"] = int(m.group(1))
                    logger.info(f"Captured provider_user_id={self.call_state['provider_user_id']} from MCP availability result")
                except Exception:
                    pass
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