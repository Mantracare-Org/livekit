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
        current_stage_id = call_payload.get("stage_id")
        stage_details = call_payload.get("stageDetails", [])
        kb_process_stage_data = None

        if direction != "inbound":
            kb_process_stage_data = (
                cc.fnc_ctx.used_process_stage_data
                if (cc.fnc_ctx and hasattr(cc.fnc_ctx, 'used_process_stage_data') and cc.fnc_ctx.used_process_stage_data)
                else None
            )
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
            logger.info("[DIAG] finalize(): Call connected. Generating detailed conversational summary of events and outcome...")
            if post_call_llm and history_snapshot:
                try:
                    summary_text = await asyncio.wait_for(
                        SessionRecorder.generate_summary(post_call_llm, list(history_snapshot)),
                        timeout=8.0
                    )
                    logger.info(f"[DIAG] finalize(): Summary generated successfully: {summary_text[:120]}...")
                except Exception as e:
                    logger.warning(f"[DIAG] finalize(): generate_summary failed or timed out: {e}")
                    summary_text = None

        # Final fallback guarantee for summary_text if missing or empty
        if not summary_text or not str(summary_text).strip():
            summary_text = f"Call completed ({duration}s)."

    except (Exception, asyncio.CancelledError) as e:
        logger.error(f"[DIAG] finalize(): Pipeline error in finalize: {e}", exc_info=True)

    # 6. Build webhook payload — separate structures for inbound vs outbound
    resolved_call_id = call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")

    if direction != "inbound":
        effective_process_id = _as_int(derived_process_id or call_payload.get("process_id"))
        initial_stage_id = _as_int(current_stage_id if current_stage_id is not None else call_payload.get("stage_id"))
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

    if call_status not in ["No Answer", "Busy", "Failed"]:
        call_status = None
        logger.info("[DIAG] finalize(): Connected call status set to None — delegating status evaluation to JEV AI Pro")

    if direction == "inbound":
        raw_caller_phone = cc.call_state.get("caller_phone_number") or call_payload.get("client_phone_number") or call_payload.get("client_phone") or ""
        cc_code = call_payload.get("client_country_code") or call_payload.get("country_code")
        formatted_caller_phone = format_e164_phone_number(raw_caller_phone, country_code=cc_code)

        used_kb_ids_list = (
            cc.fnc_ctx.used_kb_ids
            if (cc.fnc_ctx and hasattr(cc.fnc_ctx, "used_kb_ids") and cc.fnc_ctx.used_kb_ids)
            else (call_payload.get("kb_ids") or ([] if not call_payload.get("kb_id") else [str(call_payload["kb_id"])]))
        )
        primary_kb_id = used_kb_ids_list[0] if used_kb_ids_list else call_payload.get("kb_id")

        webhook_payload = {
            "event": "CALL_DATA_INBOUND_UPDATE",
            "data": {
                "org_id": _as_int(call_payload.get("org_id")),
                "call_recording": recording_url or "",
                "client_name": call_payload.get("client_name") or "",
                "client_phone_number": formatted_caller_phone,
                "call_duration": duration,
                "call_transcript": transcript_data or "",
                "ai_summary": summary_text or "",
                "called_on": cc.call_state.get("call_initiated_at") or cc.call_state.get("agent_joined_at") or "",
                "kb_id": primary_kb_id,
                "meta_data": {
                    "document_id": str(call_payload.get("call_id") or call_payload.get("voice_id") or (ctx.job.id if ctx.job else "")),
                    "provider": (call_payload.get("metadata", {}) or {}).get("provider", ""),
                    "kb_id": primary_kb_id,
                    "kb_ids": used_kb_ids_list,
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
                    "call_transcript": transcript_data,
                    "recording_url": recording_url,
                    "call_duration_seconds": duration,
                    "called_on": cc.call_state.get("call_initiated_at") or cc.call_state.get("agent_joined_at") or None,
                    "ai_call_id": ctx.job.id if ctx.job else "",
                    "process_id": effective_process_id,
                    "stage_id": payload_stage_id,
                    "ai_summary": summary_text,
                },
            }

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