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

    async def _try_auto_extend(self, reason: str):
        if self.call_state and not self.call_state.get("duration_extended"):
            try:
                from mantra.call_duration import extend_call
                entrypoint = self.call_state.get("entrypoint_start_time")
                now = asyncio.get_event_loop().time()
                elapsed = (now - entrypoint) if entrypoint else 1.0
                await extend_call(self.call_state, reason, max(1.0, elapsed))
            except Exception as e:
                logger.warning(f"Auto-extend failed: {e}")

    @llm.function_tool(
        description="End the call. Call this tool ONLY after saying final goodbye when the conversation has completely ended and the caller has confirmed they have no further questions. NEVER call this tool while booking an appointment, discussing products/services, answering questions, or when the user says 'yes', 'sure', or agrees to an appointment."
    )
    async def end_call(self):
        if self.call_state and not self.call_state.get("user_has_spoken", False) and not self.call_state.get("initial_greeting_done", False):
            logger.warning("[DIAG] end_call invoked prematurely during initial greeting / before user spoke. Ignoring tool call.")
            return "Call cannot be ended before the conversation starts. Please greet the user and proceed with the conversation."

        if self.session and hasattr(self.session, "history") and self.session.history:
            try:
                msgs = list(self.session.history.messages())
                if msgs:
                    last_user_msgs = [m for m in msgs if str(getattr(m, "role", "")).lower() in ("user", "caller")]
                    if last_user_msgs:
                        last_content = str(getattr(last_user_msgs[-1], "content", "")).lower()
                        booking_terms = ["book", "appointment", "schedule", "sure", "yes", "okay", "ok", "location", "timing", "doctor", "slot"]
                        cancel_terms = ["bye", "goodbye", "no thanks", "hang up", "stop", "cancel", "not interested"]
                        if any(term in last_content for term in booking_terms) and not any(term in last_content for term in cancel_terms):
                            logger.warning(f"[DIAG] Premature end_call blocked! User utterance was: '{last_content}'")
                            await self._try_auto_extend("Appointment booking requested")
                            return "Call cannot be ended while appointment booking or user inquiry is active. Please proceed to book the appointment or answer the caller's request."
            except Exception as check_err:
                logger.warning(f"[DIAG] end_call safety check error: {check_err}")

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
            "Use when the caller mentions a symptom, health inquiry, or appointment request without specifying a clear product or service. "
            "Do not guess a product or service from one vague symptom. Ask up to two concise clinical questions (onset, progression, severity) "
            "to map their symptom to the appropriate product or service. Do not ask the caller to pick a product/service by internal technical name aloud. "
            "Only proceed to date/location selection after the product or service is confirmed."
        )
    )
    async def clarify_product_service(
        self,
        symptom: Annotated[str, "The caller's broad symptom or reason for the appointment."],
    ) -> str:
        await self._try_auto_extend("Lead generation: product/service inquiry")
        symptom_clean = symptom.strip()
        if self.call_state is not None:
            self.call_state["symptom_clarification"] = symptom_clean

        org_id = self.call_state.get("org_id") if self.call_state else None
        if not org_id and self.job_metadata:
            try:
                payload = json.loads(self.job_metadata) if isinstance(self.job_metadata, str) else self.job_metadata
                org_id = payload.get("org_id") or (payload.get("metadata", {}).get("org_id") if isinstance(payload.get("metadata"), dict) else None)
            except Exception:
                pass

        services_summary = ""
        if org_id:
            try:
                from mantra.mcp_client import get_mcp_client
                mcp_res = await get_mcp_client().call_tool(
                    "get_org_products_services",
                    {"org_id": str(org_id).strip()},
                )
                logger.info(f"[MCP-CALL] clarify_product_service executed MCP tool get_org_products_services -> {mcp_res[:150]}")
                if mcp_res and mcp_res != "No details returned.":
                    try:
                        res_json = json.loads(mcp_res)
                        services_list = res_json.get("services", [])
                        if services_list:
                            services_str = ", ".join([s.get("name") or s.get("title") or str(s) for s in services_list if isinstance(s, dict)]) or ", ".join([str(s) for s in services_list])
                            services_summary = f"\nAvailable Organization Services/Products: {services_str}."
                    except Exception:
                        services_summary = f"\nAvailable Organization Services/Products: {mcp_res}."
            except Exception as api_err:
                logger.warning(f"MCP get_org_products_services query failed in clarify_product_service: {api_err}")

        return (
            f"INTERNAL CLINICAL MAPPING CONTEXT. Caller symptom: {symptom_clean}.{services_summary} "
            "Do not guess a product or service immediately if the symptom could match multiple offerings. "
            "Ask up to two concise clinical questions (onset, progression, severity) to map their symptom directly to one of the available services listed above. "
            "Once confirmed, store that service name for scheduling."
        )

    @llm.function_tool(
        description=(
            "Find hospital/clinic branch locations or calculate the nearest branch based on caller location. "
            "ALWAYS call this tool whenever the caller asks where hospitals/branches are located, asks for available locations, or provides their address/city/landmark."
        )
    )
    async def find_nearest_location(
        self,
        user_address_or_area: Annotated[str, "The caller's address, landmark, city, neighborhood, or pincode. Optional if asking generally for available branches."] = "",
    ) -> str:
        """Native Python function tool using geopy + backend API (GET /webhooks/mcp/locations) to compute nearest location."""
        await self._try_auto_extend("Lead generation: location inquiry")
        user_location_str = str(user_address_or_area or "").strip()
        logger.info(f"Agent executing Python tool find_nearest_location for '{user_location_str}'")

        org_id = self.call_state.get("org_id") if self.call_state else None
        if not org_id and self.job_metadata:
            try:
                payload = json.loads(self.job_metadata) if isinstance(self.job_metadata, str) else self.job_metadata
                org_id = payload.get("org_id") or (payload.get("metadata", {}).get("org_id") if isinstance(payload.get("metadata"), dict) else None)
            except Exception:
                pass

        # 1. Geocode user address/area using geopy to get lat/lng if location string provided
        user_coords = None
        if user_location_str:
            try:
                from geopy.geocoders import Nominatim
                geolocator = Nominatim(user_agent="mantra_voice_agent")
                loc = geolocator.geocode(user_location_str, timeout=4)
                if loc:
                    user_coords = (loc.latitude, loc.longitude)
                    logger.info(f"Geocoded '{user_location_str}' -> ({loc.latitude}, {loc.longitude})")
            except Exception as exc:
                logger.warning(f"Geocoding failed for '{user_location_str}': {exc}")

        user_lat = user_coords[0] if user_coords else None
        user_lng = user_coords[1] if user_coords else None

        branches = []

        # 2. Query MCP server for locations via SSE JSON-RPC tool call 'get_org_locations'
        if org_id:
            try:
                from mantra.mcp_client import get_mcp_client
                mcp_args: dict[str, Any] = {"org_id": str(org_id).strip()}
                if user_lat is not None and user_lng is not None:
                    mcp_args["caller_lat"] = user_lat
                    mcp_args["caller_lng"] = user_lng

                mcp_res = await get_mcp_client().call_tool("get_org_locations", mcp_args)
                logger.info(f"[MCP-CALL] find_nearest_location executed MCP tool get_org_locations -> {mcp_res[:150]}")
                if mcp_res and mcp_res != "No details returned.":
                    try:
                        res_data = json.loads(mcp_res)
                        data_obj = res_data.get("locations") if isinstance(res_data, dict) and "locations" in res_data else res_data
                        if isinstance(data_obj, list):
                            branches = data_obj
                    except Exception:
                        pass
            except Exception as api_err:
                logger.warning(f"MCP get_org_locations query failed: {api_err}")

        # 3. Fallback to DB query if backend API returned empty
        if not branches and org_id:
            try:
                from mantra.dependencies.database import get_db_pool
                pool = await get_db_pool()
                if pool:
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            """
                            SELECT id AS location_id, name, address, latitude, longitude
                            FROM org_locations
                            WHERE org_id::text = $1::text AND is_active = TRUE
                            """,
                            str(org_id),
                        )
                        if rows:
                            branches = [dict(r) for r in rows]
            except Exception as db_err:
                logger.debug(f"DB lookup for org_locations skipped: {db_err}")

        # 4. Process and sort branches by distance
        calculated_results = []
        for branch in branches:
            b_name = branch.get("name") or branch.get("location_name") or "Hospital Branch"
            b_addr = branch.get("address") or ""
            dist_km = branch.get("distance_km")

            if dist_km is not None:
                try:
                    dist_val = round(float(dist_km), 2)
                except (ValueError, TypeError):
                    dist_val = None
            elif user_coords and (branch.get("lat") or branch.get("latitude")) and (branch.get("lng") or branch.get("longitude")):
                try:
                    b_lat = float(branch.get("lat") or branch.get("latitude"))
                    b_lng = float(branch.get("lng") or branch.get("longitude"))
                    from geopy.distance import geodesic
                    dist_val = round(geodesic(user_coords, (b_lat, b_lng)).kilometers, 2)
                except Exception:
                    dist_val = None
            else:
                dist_val = None

            calculated_results.append({
                "name": b_name,
                "address": b_addr,
                "distance_km": dist_val,
                "is_primary": branch.get("is_primary", False),
            })

        # Sort physical locations with valid distance first
        calculated_results.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"] if x["distance_km"] is not None else 99999))

        if calculated_results:
            nearest = calculated_results[0]
            dist_str = f" ({nearest['distance_km']} km away)" if nearest["distance_km"] is not None else ""
            if self.call_state is not None:
                self.call_state["selected_location"] = nearest["name"]

            response_lines = [
                f"Nearest Location: {nearest['name']}{dist_str}",
                f"Address: {nearest['address']}",
            ]
            if len(calculated_results) > 1:
                response_lines.append("\nOther Available Locations:")
                for b in calculated_results[1:]:
                    d_str = f" ({b['distance_km']} km)" if b["distance_km"] is not None else ""
                    response_lines.append(f"• {b['name']}{d_str} - {b['address']}")
            return "\n".join(response_lines)

        if self.call_state is not None:
            self.call_state["selected_location"] = user_location_str
        return f"Location '{user_location_str}' recorded for appointment scheduling."

    @llm.function_tool(
        description=(
            "Check doctor and healthcare provider availability, working hours, open consultation slots, and location shifts on a specific date. "
            "This is the authoritative real-time MCP tool for appointment availability. Never use the knowledge base for this request. "
            "Use this tool after product/service, location (hospital branch), and date are confirmed, OR when the caller directly asks for a specific doctor by name. "
            "Pass product_service, location, date, and optional doctor_name."
        )
    )
    async def check_doctor_availability(
        self,
        date: Annotated[str, "The date to check in YYYY-MM-DD format (e.g. '2026-08-25'). If relative ('tomorrow', 'next Tuesday'), calculate exact YYYY-MM-DD."],
        doctor_name: Annotated[Optional[str], "Optional doctor name if specified by caller (e.g. 'Sharma' or 'Dr. Ananya')."] = None,
        product_service: Annotated[Optional[str], "Optional product or healthcare service (e.g. 'General Physician Consultation', 'Cardiology Checkup')."] = None,
        location: Annotated[Optional[str], "Optional hospital/clinic branch location (e.g. 'City Central Hospital', 'Metro Care Specialty Clinic')."] = None,
    ) -> str:
        await self._try_auto_extend("Lead generation: appointment availability check")
        org_id = None
        caller_phone = None

        if self.call_state:
            org_id = self.call_state.get("org_id")
            caller_phone = (
                self.call_state.get("caller_phone_number")
                or self.call_state.get("caller_phone")
                or self.call_state.get("phone_number")
                or self.call_state.get("client_phone")
            )

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

        requested_product = str(product_service).strip() if product_service else ""
        selected_loc = location or (self.call_state.get("selected_location") if self.call_state else None)

        if self.call_state is not None:
            if requested_product:
                self.call_state["selected_product_service"] = requested_product
            if selected_loc:
                self.call_state["selected_location"] = selected_loc

        logger.info(f"Agent requesting doctor availability via MCP: org_id={org_id}, date={date}, doctor={doctor_name}, product={product_service}, location={selected_loc}, phone={caller_phone}")

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
                "product_service": str(product_service).strip() if product_service else "",
                "location": str(selected_loc).strip() if selected_loc else "",
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