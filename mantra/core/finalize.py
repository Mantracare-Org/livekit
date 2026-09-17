"""Post-call finalization: recording upload, LLM analysis, webhook payload
construction and delivery.

Extracted verbatim from the former mantra/agent.py module, bound to a CallContext.
"""
import asyncio
import datetime
import json
import logging
import os

from mantra.core.common import CallContext, _as_int, create_bg_task, get_global_kb
from mantra.core.engines import build_post_call_llm
from mantra.utils import (
    SessionRecorder,
    format_e164_phone_number,
    normalize_datetime,
    reconcile_process_and_stage_id,
    save_call_event,
    save_call_log_to_db,
    send_to_backend,
    upload_to_s3,
)

logger = logging.getLogger("mantra.finalize")


async def finalize(cc: CallContext, history_snapshot: list):
    if cc.call_state.get("_finalized"):
        logger.info("[DIAG] finalize(): Call already finalized — skipping duplicate execution")
        return
    cc.call_state["_finalized"] = True
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

    ctx = cc.ctx

    try:
        logger.info("[DIAG] finalize(): Starting post-call processing...")
        if "timeline" in cc.call_state:
            cc.call_state["timeline"].append({"event": "Call Finalization Started", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"})
        await cc.telemetry("Post-call processing started")

        # 1. Pre-load call metadata
        logger.info("[DIAG] finalize(): Step 1 — Loading call metadata...")
        try:
            if cc.effective_call_metadata:
                call_payload = dict(cc.effective_call_metadata)
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
                if cc.fnc_ctx and hasattr(cc.fnc_ctx, "used_kb_process_ids"):
                    used_pids = cc.fnc_ctx.used_kb_process_ids
                    if used_pids:
                        call_payload["kb_tracked_process_id"] = used_pids[0]
                        logger.info(f"KB-tracked process_id hint for inbound: {used_pids[0]}")
                if cc.fnc_ctx and hasattr(cc.fnc_ctx, "used_kb_stage_ids"):
                    used_sids = cc.fnc_ctx.used_kb_stage_ids
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
        logger.info(f"[DIAG] finalize(): user_joined={cc.call_state.get('user_joined')} user_spoke={user_spoke} history_size={len(history_snapshot)}")

        is_inbound = (call_payload.get("direction") == "inbound")
        is_user_joined = bool(cc.call_state.get("user_joined") or is_inbound)

        if not is_user_joined:
            initiated_str = cc.call_state.get("call_initiated_at") or (call_payload.get("metadata", {}) or {}).get("call_initiated_at")
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
            if cc.recorder and hasattr(cc.recorder, "stop_recording"):
                await cc.recorder.stop_recording()
                logger.info(f"[DIAG] finalize(): Recording stopped. track_count={len(getattr(cc.recorder, '_tracks', []))}")
                mp3_bytes = cc.recorder.get_combined_mp3_bytes()
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
        if cc.recorder and hasattr(cc.recorder, "recording_duration_seconds"):
            duration = int(cc.recorder.recording_duration_seconds)

        # 5. Run unified analysis (bounded by 70s timeout)
        direction = call_payload.get("direction")
        if direction == "inbound":
            try:
                if cc.fnc_ctx and hasattr(cc.fnc_ctx, "used_kb_process_ids"):
                    used_pids = cc.fnc_ctx.used_kb_process_ids
                    if used_pids and not call_payload.get("kb_tracked_process_id"):
                        call_payload["kb_tracked_process_id"] = used_pids[0]
                        logger.info(f"Using KB-tracked process_id hint for inbound before analysis: {used_pids[0]}")
                if cc.fnc_ctx and hasattr(cc.fnc_ctx, "used_kb_stage_ids"):
                    used_sids = cc.fnc_ctx.used_kb_stage_ids
                    if used_sids and not call_payload.get("kb_tracked_stage_id"):
                        call_payload["kb_tracked_stage_id"] = used_sids[0]
                        logger.info(f"Using KB-tracked stage_id hint for inbound before analysis: {used_sids[0]}")
            except Exception as e:
                logger.error(f"Failed to extract KB usage metadata before analysis: {e}")

        current_stage_id = call_payload.get("stage_id") or call_payload.get("kb_tracked_stage_id")
        stage_details = call_payload.get("stageDetails", [])
        kb_process_stage_data = (
            cc.fnc_ctx.used_process_stage_data
            if (cc.fnc_ctx and hasattr(cc.fnc_ctx, 'used_process_stage_data') and cc.fnc_ctx.used_process_stage_data)
            else None
        )
        # For inbound calls, query org processes and stage descriptions via MCP before post-call analysis
        if is_inbound and not kb_process_stage_data:
            inbound_org_id = (
                cc.call_state.get("org_id")
                or call_payload.get("org_id")
                or (cc.fnc_ctx.org_id if cc.fnc_ctx and hasattr(cc.fnc_ctx, 'org_id') else None)
            )
            if inbound_org_id:
                try:
                    from mantra.mcp_client import get_mcp_client
                    logger.info(f"Fetching org processes via MCP for inbound post-call analysis: org_id={inbound_org_id}")
                    mcp_client = get_mcp_client()
                    mcp_res = await mcp_client.call_tool("fetch_org_processes", {"org_id": inbound_org_id})
                    if mcp_res:
                        items = []
                        if isinstance(mcp_res, list):
                            items = mcp_res
                        elif isinstance(mcp_res, dict):
                            items = [mcp_res]
                        elif isinstance(mcp_res, str):
                            mcp_res_str = mcp_res.strip()
                            try:
                                parsed = json.loads(mcp_res_str)
                                if isinstance(parsed, list):
                                    items = parsed
                                elif isinstance(parsed, dict):
                                    items = [parsed]
                            except Exception:
                                pass
                            if not items:
                                for line in mcp_res_str.splitlines():
                                    line = line.strip()
                                    if line:
                                        try:
                                            items.append(json.loads(line))
                                        except Exception:
                                            pass
                        if items:
                            kb_process_stage_data = items
                            logger.info(f"[INBOUND-MCP] Loaded {len(kb_process_stage_data)} processes via MCP for org_id={inbound_org_id}:\n{json.dumps(kb_process_stage_data, indent=2)}")
                except Exception as e:
                    logger.warning(f"Failed to fetch org processes via MCP for inbound call: {e}")

        if not kb_process_stage_data and cc.fnc_ctx and hasattr(cc.fnc_ctx, 'kb_ids') and cc.fnc_ctx.kb_ids:
            try:
                kb = get_global_kb()
                kb_process_stage_data = await kb.get_process_stage_data_for_kb_ids(cc.fnc_ctx.kb_ids)
                if kb_process_stage_data:
                    logger.info(f"Loaded {len(kb_process_stage_data)} process_stage_data entries from DB for KB ids: {cc.fnc_ctx.kb_ids}")
            except Exception as e:
                logger.error(f"Failed to fetch fallback KB process_stage_data from DB: {e}")

        summary_text = None
        new_stage_id = current_stage_id
        llm_analysis_ran = False
        derived_process_id = None
        derived_user_intent = None
        appointment_metadata = None
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
                target_llm = post_call_llm or cc.llm_engine
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
                        timeout=10.0
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

                    appointment_metadata = analysis.get("appointment_metadata") if isinstance(analysis.get("appointment_metadata"), dict) else None
                    if appointment_metadata:
                        appointment_metadata.pop("preferred_end_datetime", None)
                        if appointment_metadata.get("preferred_datetime"):
                            appointment_metadata["preferred_datetime"] = normalize_datetime(appointment_metadata["preferred_datetime"])
                        if appointment_metadata.get("provider_user_id") is not None:
                            appointment_metadata["provider_user_id"] = _as_int(appointment_metadata["provider_user_id"])
                        elif cc.call_state.get("provider_user_id") is not None:
                            appointment_metadata["provider_user_id"] = _as_int(cc.call_state.get("provider_user_id"))
                            logger.info(f"[DIAG] Auto-injected provider_user_id={appointment_metadata['provider_user_id']} into appointment_metadata from call_state")

                    logger.info(
                        f"Analysis completed. Process: {derived_process_id}, New Stage ID: {new_stage_id}, Next Call On: {next_call_on}, User Intent: {derived_user_intent}, Client Name: {call_payload.get('client_name')}"
                    )
                else:
                    logger.warning(
                        "Skipping analysis: LLM or history unavailable after session close"
                    )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("[DIAG] finalize(): analyze_call timed out or task cancelled — using fallback summary and captured state")
                summary_text = "Call completed."
                if cc.call_state.get("provider_user_id") and not appointment_metadata:
                    appointment_metadata = {
                        "provider_user_id": _as_int(cc.call_state.get("provider_user_id")),
                        "provider_name": None,
                        "preferred_datetime": None,
                        "appointment_title": "Scheduled Appointment",
                        "appointment_notes": "Appointment requested during call."
                    }
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

    except (Exception, asyncio.CancelledError) as e:
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

        # Ensure payload_stage_id belongs to the effective_process_id
        proc_stages = []
        for p in kb_process_stage_data:
            if isinstance(p, dict) and _as_int(p.get("process_id") or p.get("id")) == effective_process_id:
                stg_list = p.get("stages") or p.get("stageDetails") or []
                for s in stg_list:
                    if isinstance(s, dict):
                        sid = _as_int(s.get("stage_id") or s.get("id"))
                        if sid is not None:
                            proc_stages.append(sid)

        if proc_stages:
            if payload_stage_id not in proc_stages:
                logger.info(
                    f"[DIAG] finalize(): payload_stage_id {payload_stage_id} does not belong to process {effective_process_id} "
                    f"(available: {proc_stages}) — defaulting to initial stage {proc_stages[0]}"
                )
                payload_stage_id = proc_stages[0]

    # Enforce stage-based call status rule:
    # If payload_new_stage_id == initial_stage_id (not updated) -> Incomplete
    # If payload_new_stage_id != initial_stage_id (updated) -> Completed
    if call_status not in ["No Answer", "Busy", "Failed"]:
        if payload_stage_id is not None and payload_new_stage_id != payload_stage_id:
            call_status = "Completed"
            logger.info(f"[DIAG] finalize(): Stage updated from {payload_stage_id} to {payload_new_stage_id} — call_status='Completed'")
        elif payload_stage_id is None and payload_new_stage_id is not None:
            call_status = "Completed"
            logger.info(f"[DIAG] finalize(): New stage assigned ({payload_new_stage_id}) with no initial stage — call_status='Completed'")
        else:
            call_status = "Incomplete"
            logger.info(f"[DIAG] finalize(): Stage not updated (new_stage_id={payload_new_stage_id}, initial={payload_stage_id}) — call_status='Incomplete'")

    if direction == "inbound":
        raw_caller_phone = cc.call_state.get("caller_phone_number") or call_payload.get("client_phone_number") or call_payload.get("client_phone") or ""
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
                "called_on": cc.call_state.get("call_initiated_at") or cc.call_state.get("agent_joined_at") or "",
                "user_intent": derived_user_intent,
                "call_intent": derived_user_intent,
                "meta_data": {
                    "document_id": str(call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")),
                    "provider": (call_payload.get("metadata", {}) or {}).get("provider", ""),
                },
            }
        }
        if appointment_metadata:
            webhook_payload["data"]["appointment_metadata"] = appointment_metadata
    else:
        event_name = "CALL_RETRY" if call_status in ["No Answer", "Busy", "Failed"] else "CALL_DATA_UPDATE"
        if event_name == "CALL_RETRY":
            webhook_payload = {
                "event": event_name,
                "data": {
                    "call_id": resolved_call_id,
                    "called_on": cc.call_state.get("call_initiated_at"),
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
                    "called_on": cc.call_state.get("call_initiated_at") or cc.call_state.get("agent_joined_at") or None,
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
        if appointment_metadata:
            webhook_payload["data"]["appointment_metadata"] = appointment_metadata

    # Prominently log the complete generated webhook payload for easy developer copying
    payload_json_str = json.dumps(webhook_payload, indent=2)
    logger.info(
        f"\n{'='*70}\n"
        f"📋 [COMPLETE WEBHOOK PAYLOAD - {webhook_payload.get('event')}]\n"
        f"{'='*70}\n"
        f"{payload_json_str}\n"
        f"{'='*70}"
    )
    print(
        f"\n{'='*70}\n"
        f"📋 [COMPLETE WEBHOOK PAYLOAD - {webhook_payload.get('event')}]\n"
        f"{'='*70}\n"
        f"{payload_json_str}\n"
        f"{'='*70}\n",
        flush=True
    )

    # 8. Send to MantraAssist backend and save to local DB
    logger.info(f"[DIAG] finalize(): Step 8 — Saving to DB and delivering webhook...")
    try:
        # Save to local Postgres DB
        try:
            c_id = webhook_payload.get("data", {}).get("call_id", (ctx.job.id if ctx.job else ""))
            caller_number = call_payload.get("call_from") or call_payload.get("caller_number") or cc.call_state.get("caller_phone_number") or ""
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
        await cc.telemetry(f"data_sent_to_backend — status={call_status}, queued_to_redis={'yes' if delivered else 'no'}")

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

    await cc.telemetry(f"call_complete — status={call_status}, duration={duration}s")

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