""" Telephony webhook routes for the UI server. """

from datetime import datetime
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from livekit import api
from mantra.dependencies.database import get_db_connection
from mantra.services import clients as _svc_clients
from mantra.services.clients import AGENT_NAME
from mantra.services.telephony import _get_provider_from_trunk
from mantra.utils import report_telemetry, save_call_event, send_to_backend
import asyncio
import json
import os
import time
import traceback

import logging

logger = logging.getLogger("mantra.telephony")
router = APIRouter()

@router.post("/dispatch-test")
async def dispatch_test(request: Request):
    """
    Manually trigger an agent dispatch with a custom payload.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    logger.info(
        f"Manual dispatch request with payload: {json.dumps(payload, separators=(',', ':'))}"
    )

    agent_name = payload.pop("agent_name", AGENT_NAME)
    # Generate a unique room name for this test session using the call_id if provided
    call_id = payload.get("call_id") or int(time.time())
    room_name = f"test_{call_id}"

    try:
        # Create dispatch with payload as metadata
        dispatch = await _svc_clients.lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name, agent_name=agent_name, metadata=json.dumps(payload)
            )
        )
        logger.info(
            f"Successfully dispatched agent to room {room_name}, dispatch_id: {dispatch.id}"
        )
    except Exception as e:
        logger.error(f"Dispatch failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Generate token for the user to join the same room
    token = (
        api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))
        .with_identity("Tester")
        .with_name("Manual Tester")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )

    return JSONResponse(
        {
            "status": "success",
            "room": room_name,
            "token": token.to_jwt(),
            "url": os.getenv("LIVEKIT_URL"),
        }
    )



@router.post("/v1/webhooks/telephony")
async def handle_outbound_call_webhook(request: Request):
    """
    Webhook handler to process telephony events and trigger outbound agent dispatch.
    Expects a JSON payload containing the call context.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    event_name = payload.get("event_name", "telephony_dispatch")
    logger.info(f"Webhook received call request for event {event_name}: {json.dumps(payload, separators=(',',':'))}")

    tos_task_id = payload.get("tos_task_id") or payload.get("metadata", {}).get("tos_task_id")

    call_id = payload.get("call_id") or payload.get("voice_id") or payload.get("event_id") or int(time.time())
    room_name = f"call_{call_id}"  # fallback until trunk_id resolved below

    def _telemetry(message_suffix: str):
        if tos_task_id:
            loop = asyncio.get_running_loop()
            loop.create_task(
                report_telemetry(
                    tos_task_id=tos_task_id,
                    message=f"[UI Server] {message_suffix}",
                    call_id=str(call_id),
                )
            )

    _telemetry("webhook_received")

    if _svc_clients.redis_client:
        # Clear stale backend delivery lock for new/retry attempts
        try:
            await _svc_clients.redis_client.delete(f"backend_sent:{call_id}")
        except Exception:
            pass

        # Smart Deduplication Lock: Catch sub-second duplicate requests if call is currently in-progress
        is_retry = bool(
            payload.get("is_retry")
            or payload.get("retry")
            or (payload.get("event") in ("CALL_RETRY", "call_retry"))
            or request.query_params.get("retry")
        )
        if is_retry:
            try:
                await _svc_clients.redis_client.delete(f"lock:call:{call_id}")
            except Exception:
                pass

        lock_acquired = await _svc_clients.redis_client.set(f"lock:call:{call_id}", "1", nx=True, ex=30)
        if not lock_acquired:
            logger.warning(f"Duplicate telephony webhook hit ignored for call_id: {call_id} (call in-progress)")
            return JSONResponse({
                "status": "ignored",
                "message": f"Duplicate request for call_id {call_id} already in progress",
                "room": f"call_{call_id}"
            }, status_code=200)


    # Construct phone number in E.164 format
    country_code = payload.get("client_country_code", "").strip("+")
    client_phone = payload.get("client_phone", "").strip()

    if client_phone.startswith("+"):
        phone_number = client_phone
    elif country_code and client_phone:
        phone_number = f"+{country_code}{client_phone}"
    else:
        phone_number = client_phone  # Fallback

    if not phone_number:
        return JSONResponse(
            {"error": "No client_phone provided in payload"}, status_code=400
        )

    # Resolve trunk ID and detect provider for logging
    trunk_id = (
        payload.get("trunk_id")
        or payload.get("call_from_id")
        or os.getenv("SIP_TRUNK_ID")
    )
    if not trunk_id:
        return JSONResponse({"error": "No SIP trunk ID configured"}, status_code=500)

    provider = await _get_provider_from_trunk(trunk_id)
    logger.info(f"[DIAG] Webhook: call_id={call_id} phone={phone_number} trunk={trunk_id} provider={provider} AGENT_NAME={AGENT_NAME}")

    # Embed trunk_id in room name for capacity tracking (zero Redis)
    room_name = f"call_{trunk_id}_{call_id}"

    # Stamp call_initiated_at before dispatching so the agent gets it
    payload_meta = payload.get("metadata")
    if not isinstance(payload_meta, dict):
        payload_meta = {}
    payload_meta["call_initiated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    payload_meta.setdefault("provider", provider)
    payload["metadata"] = payload_meta

    if payload.get("org_id") and not payload.get("kb_ids"):
        try:
            org_id = str(payload["org_id"])
            conn = await get_db_connection()
            try:
                kb_rows = await conn.fetch("SELECT id FROM kb_collections WHERE org_id = $1", org_id)
                kb_ids = [str(r["id"]) for r in kb_rows]
                if org_id not in kb_ids:
                    kb_ids.append(org_id)
                if kb_ids:
                    payload["kb_ids"] = kb_ids
                    logger.info(f"[KB] Enriched outbound payload with kb_ids={kb_ids} for org_id={org_id}")
                tag_row = await conn.fetchrow("SELECT kb_tags FROM org_configs WHERE org_id = $1 AND is_active = true", org_id)
                if tag_row and tag_row["kb_tags"] and not payload.get("kb_tags"):
                    kb_tags = tag_row["kb_tags"] if isinstance(tag_row["kb_tags"], list) else []
                    if kb_tags:
                        payload["kb_tags"] = kb_tags
                        logger.info(f"[KB] Enriched outbound payload with kb_tags={kb_tags}")
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"[KB] Outbound enrichment skipped (non-fatal): {e}")

    # Log the webhook event (payload received from MantraAssist, sent to agent)
    asyncio.create_task(save_call_event(
        call_id=str(call_id),
        event_type="webhook_received",
        event_source="ui_server",
        event_payload={k: v for k, v in payload.items() if k != "prompt"},
    ))

    # Trigger agent dispatch + SIP call as background task — return 200 immediately
    async def _deliver_call_failure(sip_status: str, error: BaseException, *, reason: str):
        """Cleanup room/lock and notify n8n for a failed outbound call."""
        logger.error(
            f"[DIAG] Webhook: call failed room={room_name} status={sip_status} "
            f"reason={reason}: {error}\n{traceback.format_exc()}"
        )
        _telemetry(f"sip_call_failed — {sip_status}: {str(error)[:80]}")

        if _svc_clients.redis_client:
            try:
                await _svc_clients.redis_client.set(f"sip_error_status:{call_id}", sip_status, ex=300)
            except Exception:
                pass

        try:
            await _svc_clients.lk_client.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info(f"Deleted room {room_name} due to call failure ({sip_status})")
        except Exception:
            pass

        if _svc_clients.redis_client:
            try:
                await _svc_clients.redis_client.delete(f"lock:call:{call_id}")
            except Exception:
                pass

        # Match agent retryable statuses so Busy/No Answer are CALL_RETRY (once via send_to_backend dedupe)
        event_name = (
            "CALL_RETRY" if sip_status in ("No Answer", "Busy") else "CALL_DATA_UPDATE"
        )
        if event_name == "CALL_RETRY":
            n8n_payload = {
                "event": event_name,
                "data": {
                    "call_id": call_id,
                    "called_on": payload.get("metadata", {}).get("call_initiated_at"),
                    "call_status": sip_status,
                    "ai_call_id": None,
                },
            }
        else:
            n8n_payload = {
                "event": event_name,
                "data": {
                    "client_id": payload.get("lead_id"),
                    "call_id": call_id,
                    "call_status": sip_status,
                    "call_transcript": None,
                    "ai_summary": f"{reason}: {sip_status}",
                    "recording_url": None,
                    "call_duration_seconds": 0,
                    "next_call_on": "",
                    "called_on": payload.get("metadata", {}).get("call_initiated_at"),
                    "ai_call_id": None,
                    "process_id": payload.get("process_id"),
                    "new_stage_id": payload.get("stage_id"),
                    "metadata": payload.get("metadata", {}),
                    "client_custom_fields": payload.get("client_custom_fields", {}),
                    "call_custom_fields": payload.get("call_custom_fields", {}),
                },
            }
        delivered = await send_to_backend(n8n_payload)
        logger.info(
            f"Call failure delivered to n8n backend: {sip_status} reason={reason} (success={delivered})"
        )
        asyncio.create_task(save_call_event(
            call_id=str(call_id),
            event_type="sip_failed",
            event_source="ui_server",
            event_payload={"sip_status": sip_status, "reason": reason, "error": str(error)[:200]},
            event_status="failed",
            event_error=str(error)[:500],
        ))

    async def _process_call():
        """Background: dispatch agent, place SIP call, handle each failure type separately."""
        # ── Step 1: Agent dispatch ─────────────────────────────────────
        try:
            logger.info(
                f"[DIAG] Webhook: Step 1 — Creating agent dispatch for room={room_name} agent_name={AGENT_NAME}"
            )
            await _svc_clients.lk_client.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name, agent_name=AGENT_NAME, metadata=json.dumps(payload)
                )
            )
            logger.info(f"[DIAG] Webhook: Dispatch created for room={room_name}")
            _telemetry(f"agent_dispatched — room={room_name}")
            asyncio.create_task(save_call_event(
                call_id=str(call_id),
                event_type="dispatch_created",
                event_source="ui_server",
                event_payload={"room": room_name, "agent_name": AGENT_NAME},
            ))
        except api.ServerError as e:
            await _deliver_call_failure(
                "Incomplete",
                e,
                reason=f"Agent dispatch failed (status={e.status} code={e.code}): {e.message}",
            )
            return
        except Exception as e:
            await _deliver_call_failure(
                "Incomplete", e, reason="Agent dispatch failed"
            )
            return

        # ── Step 2: SIP dial ───────────────────────────────────────────
        sip_number = payload.get("call_from")
        if sip_number and not sip_number.startswith("+"):
            sip_number = f"+{sip_number}"

        sip_client = _svc_clients.lk_client
        if provider == "plivo" and _svc_clients.plivo_client:
            sip_client = _svc_clients.plivo_client
        elif provider == "voice_link" and _svc_clients.voicelink_client:
            sip_client = _svc_clients.voicelink_client
        proxy_msg = (
            "proxied Plivo client"
            if sip_client == _svc_clients.plivo_client
            else "proxied VoiceLink client"
            if sip_client == _svc_clients.voicelink_client
            else "direct LiveKit client"
        )
        logger.info(
            f"[DIAG] Webhook: Step 2 — Initiating SIP call to {phone_number} via trunk {trunk_id} using {proxy_msg}"
            + (f" (Caller ID: {sip_number})" if sip_number else "")
        )
        _telemetry(f"sip_call_initiating — phone={phone_number}")

        asyncio.create_task(save_call_event(
            call_id=str(call_id),
            event_type="sip_initiated",
            event_source="ui_server",
            event_payload={
                "phone": phone_number,
                "trunk": trunk_id,
                "caller_id": sip_number,
                "room": room_name,
            },
        ))

        async def _sip_already_connected() -> bool:
            try:
                participants = await _svc_clients.lk_client.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                return any(p.identity == f"sip_{call_id}" for p in participants.participants)
            except Exception:
                return False

        try:
            sip_part = await sip_client.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    sip_number=sip_number,
                    room_name=room_name,
                    participant_identity=f"sip_{call_id}",
                    participant_name="SIP Caller",
                    play_ringtone=False,
                    wait_until_answered=True,
                )
            )
            # Answered — agent path owns Completed / No Answer (joined, no speech)
            logger.info(
                f"[DIAG] Webhook: SIP Participant created: {sip_part.participant_identity}"
            )
            _telemetry("sip_call_connected")
            asyncio.create_task(save_call_event(
                call_id=str(call_id),
                event_type="sip_connected",
                event_source="ui_server",
                event_payload={
                    "participant": sip_part.participant_identity,
                    "room": room_name,
                },
            ))
            return

        except api.SipCallError as e:
            # Structured SIP failure: metadata.sip_status_code / sip_status
            if await _sip_already_connected():
                logger.warning(
                    f"[DIAG] Webhook: SipCallError after connect for {room_name}; "
                    f"agent will finalize: {e}"
                )
                return

            sip_code = e.sip_status_code
            sip_reason = (e.sip_status or "").strip()
            detail = f"SIP {sip_code} {sip_reason}".strip() if sip_code is not None else (sip_reason or str(e))

            if sip_code == 408:
                await _deliver_call_failure("No Answer", e, reason=detail)
            elif sip_code in (486, 600):
                await _deliver_call_failure("Busy", e, reason=detail)
            elif sip_code == 603:
                await _deliver_call_failure("Busy", e, reason=detail)
            elif sip_code == 503:
                await _deliver_call_failure("Incomplete", e, reason=detail)
            else:
                await _deliver_call_failure("Incomplete", e, reason=detail)
            return

        except api.ServerError as e:
            # Twirp/API error without sip_status_code (e.g. dial timeout wrapper)
            if await _sip_already_connected():
                logger.warning(
                    f"[DIAG] Webhook: ServerError after connect for {room_name}; "
                    f"agent will finalize: {e}"
                )
                return

            msg = (e.message or "").lower()
            # HTTP/Twirp 408 or timeout message → No Answer
            if e.status == 408 or "timed out" in msg or "timeout" in msg or "no answer" in msg:
                await _deliver_call_failure(
                    "No Answer",
                    e,
                    reason=f"ServerError status={e.status} code={e.code}: {e.message}",
                )
            else:
                await _deliver_call_failure(
                    "Incomplete",
                    e,
                    reason=f"ServerError status={e.status} code={e.code}: {e.message}",
                )
            return

        except Exception as e:
            if await _sip_already_connected():
                logger.warning(
                    f"[DIAG] Webhook: error after connect for {room_name}; "
                    f"agent will finalize: {e}"
                )
                return
            await _deliver_call_failure(
                "Incomplete", e, reason="SIP call failed (unclassified)"
            )

    asyncio.create_task(_process_call())

    return Response(status_code=200)



