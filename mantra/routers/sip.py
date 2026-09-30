""" SIP trunk and dispatch-rule routes for the UI server. """

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from livekit import api
from livekit.protocol import sip as proto_sip
from mantra.dependencies.database import get_db_connection
from mantra.services import clients as _svc_clients
from mantra.services.clients import AGENT_NAME
from mantra.services.telephony import _build_plivo_xml, _create_sip_outbound_trunk, _get_sip_domain, _normalize_phone_number, _resolve_plivo_sip_trunk_id, _setup_inbound_sip_process
import json
import os
import time
import traceback

import logging

logger = logging.getLogger("mantra.sip")
router = APIRouter()

@router.post("/v1/test/inbound-call")
async def test_inbound_call(request: Request):
    """
    Simulates an inbound call by triggering an outbound SIP call but dispatching
    the agent with the 'inbound' direction metadata so it acts like an inbound call.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Test inbound call request: {json.dumps(payload, indent=2)}")
    
    agent_name = payload.pop("agent_name", AGENT_NAME)
    call_id = int(time.time())
    room_name = f"test_inbound_{call_id}"
    
    # Force the direction to inbound so the agent handles it correctly
    payload["direction"] = "inbound"
    payload["call_id"] = call_id
    # Ensure phone_number is set (agent looks for this, not 'phone')
    if "phone" in payload and "phone_number" not in payload:
        payload["phone_number"] = payload["phone"]
    
    # 1. Trigger agent dispatch
    try:
        logger.info(f"Dispatching agent '{agent_name}' to room {room_name}")
        dispatch = await _svc_clients.lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name=agent_name,
                metadata=json.dumps(payload)
            )
        )
    except Exception as e:
        logger.error(f"Agent dispatch failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"Agent dispatch failed: {str(e)}"}, status_code=500)

    # 2. Trigger SIP Outbound Call to the tester's phone
    try:
        trunk_id = payload.get("trunk_id")
        client_phone = payload.get("phone")
        country_code = str(payload.get("country_code", "")).strip("+")
        
        if not trunk_id or not client_phone:
            return JSONResponse({"error": "trunk_id and phone are required"}, status_code=400)
            
        if client_phone.startswith("+"):
            phone_number = client_phone
        elif country_code and client_phone:
            phone_number = f"+{country_code}{client_phone}"
        else:
            phone_number = client_phone
            
        logger.info(f"Initiating test SIP call to {phone_number} via trunk {trunk_id}")

        sip_part = await _svc_clients.lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"sip_test_{call_id}",
                participant_name="SIP Tester"
            )
        )
    except Exception as e:
        logger.error(f"SIP Call trigger failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"SIP Call trigger failed: {str(e)}"}, status_code=500)

    return JSONResponse({
        "status": "success",
        "message": "Test inbound call initiated",
        "room": room_name,
        "call_id": call_id
    })




@router.post("/v1/sip/trunks/inbound")
async def create_inbound_trunk(request: Request):
    """
    Create a new SIP Inbound Trunk to receive incoming calls from SIP providers (e.g., Plivo).
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Creating SIP Inbound Trunk with payload: {json.dumps(payload, indent=2)}")
    
    name = payload.get("name")
    numbers = payload.get("numbers")
    auth_username = payload.get("authUsername") or payload.get("auth_username")
    auth_password = payload.get("authPassword") or payload.get("auth_password")
    
    if not all([name, numbers]):
        return JSONResponse({"error": "Missing required fields: name, numbers"}, status_code=400)
        
    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.split(",") if n.strip()]
    elif not isinstance(numbers, list):
        numbers = [str(numbers)]
        
    try:
        trunk_request = api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(
                name=name,
                numbers=numbers,
                auth_username=auth_username or "",
                auth_password=auth_password or "",
            )
        )
        trunk = await _svc_clients.lk_client.sip.create_inbound_trunk(trunk_request)
        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk.sip_trunk_id,
            "name": trunk.name,
            "numbers": list(trunk.numbers)
        })
    except Exception as e:
        logger.error(f"Failed to create inbound trunk: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/v1/sip/trunks/inbound/voicelink")
async def create_voicelink_inbound_trunk(request: Request):
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    logger.info(f"Creating Voicelink inbound trunk: {json.dumps(payload, indent=2)}")

    name = payload.get("name")
    numbers = payload.get("numbers")
    allowed_addresses = "160.30.71.89"

    if not all([name, numbers]):
        return JSONResponse({"error": "Missing required fields: name, numbers"}, status_code=400)

    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.split(",") if n.strip()]
    elif not isinstance(numbers, list):
        numbers = [str(numbers)]

    if isinstance(allowed_addresses, str):
        allowed_addresses = [a.strip() for a in allowed_addresses.split(",") if a.strip()]

    try:
        trunk_request = api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(
                name=name,
                numbers=numbers,
                allowed_addresses=allowed_addresses or [],
            )
        )
        trunk = await _svc_clients.lk_client.sip.create_inbound_trunk(trunk_request)
        trunk_id = trunk.sip_trunk_id
        logger.info(f"Voicelink inbound trunk created: {trunk_id}")

        dispatch_payload = {
            **payload,
            "trunk_id": trunk_id,
            "direction": "inbound",
        }

        rule_name = payload.get("rule_name", f"voicelink_rule_{trunk_id}")
        room_prefix = payload.get("room_prefix", "inbound_")

        req = api.CreateSIPDispatchRuleRequest(
            name=rule_name,
            metadata=json.dumps(dispatch_payload),
            rule=api.SIPDispatchRule(
                dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                    room_prefix=room_prefix
                )
            ),
            room_config=api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME,
                        metadata=json.dumps(dispatch_payload)
                    )
                ]
            ),
            trunk_ids=[trunk_id]
        )
        rule = await _svc_clients.lk_client.sip.create_sip_dispatch_rule(req)
        logger.info(f"Dispatch rule created: {rule.sip_dispatch_rule_id}")

        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk_id,
            "sip_dispatch_rule_id": rule.sip_dispatch_rule_id,
            "name": name,
            "numbers": list(trunk.numbers),
            "allowed_addresses": allowed_addresses,
        })
    except Exception as e:
        logger.error(f"Failed to create Voicelink inbound trunk: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/v1/sip/trunks/inbound")
async def list_sip_inbound_trunks():
    """
    List all SIP Inbound Trunks configured in LiveKit.
    """
    try:
        response = await _svc_clients.lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
        trunk_list = []
        for item in response.items:
            trunk_list.append({
                "sip_trunk_id": item.sip_trunk_id,
                "name": item.name,
                "numbers": list(item.numbers)
            })
        
        return JSONResponse({
            "status": "success",
            "count": len(trunk_list),
            "trunks": trunk_list
        })
    except Exception as e:
        logger.error(f"Failed to list SIP inbound trunks: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/sip/trunks/inbound/{trunk_id}")
async def delete_sip_inbound_trunk(trunk_id: str):
    """
    Delete a SIP Inbound Trunk and all associated dispatch rules by ID.
    """
    if not trunk_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Trunk ID is required"}, status_code=400)

    try:
        conn = await get_db_connection()
        try:
            db_rule_rows = await conn.fetch(
                """
                SELECT DISTINCT dispatch_rule_id
                FROM org_configs
                WHERE sip_trunk_id = $1 AND dispatch_rule_id IS NOT NULL
                """,
                trunk_id,
            )
        finally:
            await conn.close()

        rule_ids = {str(row["dispatch_rule_id"]) for row in db_rule_rows}
        rule_response = await _svc_clients.lk_client.sip.list_dispatch_rule(
            api.ListSIPDispatchRuleRequest(trunk_ids=[trunk_id])
        )
        rule_ids.update(
            item.sip_dispatch_rule_id
            for item in rule_response.items
            if item.sip_dispatch_rule_id
        )

        rule_errors = []
        for rule_id in rule_ids:
            try:
                await _svc_clients.lk_client.sip.delete_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
                )
                logger.info(f"Deleted SIP dispatch rule {rule_id} for trunk {trunk_id}")
            except Exception as e:
                rule_errors.append(f"{rule_id}: {e}")

        if rule_errors:
            return JSONResponse(
                {
                    "status_code": 500,
                    "status": "error",
                    "error": "Failed to delete associated SIP dispatch rules",
                    "dispatch_rule_errors": rule_errors,
                },
                status_code=500,
            )

        await _svc_clients.lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Successfully deleted SIP Inbound Trunk: {trunk_id}")

        conn = await get_db_connection()
        try:
            await conn.execute(
                "DELETE FROM org_configs WHERE sip_trunk_id = $1",
                trunk_id,
            )
        finally:
            await conn.close()

        if _svc_clients.redis_client:
            try:
                await _svc_clients.redis_client.delete(
                    f"trunk:provider:{trunk_id}",
                )
            except Exception as e:
                logger.warning(f"Failed to clear trunk cache for {trunk_id}: {e}")
        
        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": f"SIP inbound trunk {trunk_id} and associated dispatch rules deleted successfully",
            "sip_trunk_id": trunk_id,
            "deleted_dispatch_rule_ids": sorted(rule_ids),
        })
    except Exception as e:
        logger.error(f"Failed to delete SIP inbound trunk {trunk_id}: {e}")
        return JSONResponse({"status_code": 500, "status": "error", "error": str(e)}, status_code=500)



@router.post("/v1/sip/dispatch-rules")
async def create_dispatch_rule(request: Request):
    """
    Create a SIP Dispatch Rule to route incoming calls from a specific trunk to agent-controlled rooms.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
        
    logger.info(f"Creating dispatch rule with payload: {json.dumps(payload, indent=2)}")
    
    trunk_id = payload.get("trunk_id")
    if not trunk_id:
        return JSONResponse({"error": "trunk_id is required"}, status_code=400)
        
    room_prefix = payload.get("room_prefix", "inbound_")
    name = payload.get("name", f"rule_{trunk_id}")
    
    # Enforce inbound direction for agent payload
    payload["direction"] = "inbound"
    # If phone_number not set but phone is, normalize it
    if "phone" in payload and "phone_number" not in payload:
        payload["phone_number"] = payload["phone"]
    
    try:
        req = api.CreateSIPDispatchRuleRequest(
            name=name,
            metadata=json.dumps(payload),
            rule=api.SIPDispatchRule(
                dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                    room_prefix=room_prefix
                )
            ),
            room_config=api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME,
                        metadata=json.dumps(payload)
                    )
                ]
            ),
            trunk_ids=[trunk_id]
        )
        # Using _svc_clients.lk_client directly as rules are managed at LiveKit cloud level
        rule = await _svc_clients.lk_client.sip.create_sip_dispatch_rule(req)
        
        return JSONResponse({
            "status": "success",
            "sip_dispatch_rule_id": rule.sip_dispatch_rule_id,
            "name": name,
            "trunk_ids": [trunk_id],
            "room_prefix": room_prefix
        })
    except Exception as e:
        logger.error(f"Failed to create dispatch rule: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/v1/sip/dispatch-rules")
async def list_dispatch_rules():
    """
    List all SIP Dispatch Rules configured in LiveKit.
    """
    try:
        response = await _svc_clients.lk_client.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
        rule_list = []
        for item in response.items:
            # Safely handle the rule type which could be individual, direct, etc.
            rule_info = {}
            if item.rule:
                if item.rule.dispatch_rule_individual:
                    rule_info = {"type": "individual", "room_prefix": item.rule.dispatch_rule_individual.room_prefix}
                elif item.rule.dispatch_rule_direct:
                    rule_info = {"type": "direct", "room_name": item.rule.dispatch_rule_direct.room_name}
                elif item.rule.dispatch_rule_caller:
                    rule_info = {"type": "caller", "room_prefix": item.rule.dispatch_rule_caller.room_prefix, "workspace_uid": item.rule.dispatch_rule_caller.workspace_uid}
                    
            rule_list.append({
                "sip_dispatch_rule_id": item.sip_dispatch_rule_id,
                "name": item.name,
                "trunk_ids": list(item.trunk_ids),
                "rule": rule_info,
                "metadata": item.metadata
            })
        
        return JSONResponse({
            "status": "success",
            "count": len(rule_list),
            "rules": rule_list
        })
    except Exception as e:
        logger.error(f"Failed to list dispatch rules: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/sip/dispatch-rules/{rule_id}")
async def delete_dispatch_rule(rule_id: str):
    """
    Delete a SIP Dispatch Rule by its ID.
    """
    if not rule_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Rule ID is required"}, status_code=400)
    
    try:
        await _svc_clients.lk_client.sip.delete_dispatch_rule(
            api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
        )
        logger.info(f"Successfully deleted SIP Dispatch Rule: {rule_id}")
        
        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": f"SIP dispatch rule {rule_id} deleted successfully"
        })
    except Exception as e:
        logger.error(f"Failed to delete dispatch rule {rule_id}: {e}")
        return JSONResponse({"status_code": 500, "status": "error", "error": str(e)}, status_code=500)



@router.get("/v1/sip/plivo-xml")
@router.post("/v1/sip/plivo-xml")
async def plivo_xml(request: Request):
    """
    DEPRECATED: Replaced by Plivo Zentrunk SIP trunking.
    Kept for backward compatibility with numbers still using Plivo Application.
    """
    logger.warning("plivo_xml endpoint called (DEPRECATED - migrating to Zentrunk)")
    call_uuid = "unknown"
    to_number = "unknown"
    from_number = "unknown"
    
    if request.method == "POST":
        form_data = await request.form()
        logger.info(f"Received Plivo XML request via POST: {dict(form_data)}")
        call_uuid = form_data.get("CallUUID", "unknown")
        to_number = form_data.get("To", "unknown")
        from_number = form_data.get("From", "unknown")
    elif request.method == "GET":
        logger.info(f"Received Plivo XML request via GET: {dict(request.query_params)}")
        call_uuid = request.query_params.get("CallUUID", "unknown")
        to_number = request.query_params.get("To", "unknown")
        from_number = request.query_params.get("From", "unknown")
        
    logger.info(f"Plivo XML parameters - CallUUID: {call_uuid}, To: {to_number}, From: {from_number}")
    
    sip_trunk_id = await _resolve_plivo_sip_trunk_id(to_number)

    if not sip_trunk_id:
        sip_trunk_id = os.getenv("SIP_TRUNK_ID")
        if sip_trunk_id:
            logger.warning(f"No SIP trunk mapping found for {to_number}, using fallback from env: {sip_trunk_id}")
        else:
            logger.error(f"No SIP trunk mapping found for {to_number} and no SIP_TRUNK_ID in env")
            return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>', media_type="application/xml")
    
    # Use clean number (no + prefix) for the SIP URI username.
    # Plivo's <User> element treats + prefix as a local extension lookup,
    # causing silent skip. LiveKit's trunk has both formats in its numbers array.
    clean_to = _normalize_phone_number(to_number) if to_number != "unknown" else ""
    
    sip_domain = _get_sip_domain()
        
    # Build absolute action URL dynamically using headers for ngrok support
    req_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8081"
    req_scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    action_url = f"{req_scheme}://{req_host}/v1/sip/plivo-dial-status"

    
    xml_content = _build_plivo_xml(
        sip_trunk_id=sip_trunk_id,
        sip_domain=sip_domain,
        action_url=action_url,
        phone_number=clean_to,
    )
    logger.info(f"Returning Plivo XML for {call_uuid}: {xml_content}")
    return Response(content=xml_content, media_type="application/xml")



@router.get("/v1/sip/twilio-webhook")
@router.post("/v1/sip/twilio-webhook")
async def twilio_webhook(request: Request):
    """
    Returns TwiML for Twilio to route to the LiveKit SIP Trunk.
    Looks up the SIP trunk ID from the phone number mapping.
    """
    call_sid = "unknown"
    to_number = "unknown"
    from_number = "unknown"
    
    if request.method == "POST":
        form_data = await request.form()
        logger.info(f"Received Twilio webhook via POST: {dict(form_data)}")
        call_sid = form_data.get("CallSid", "unknown")
        to_number = form_data.get("To", "unknown")
        from_number = form_data.get("From", "unknown")
    elif request.method == "GET":
        logger.info(f"Received Twilio webhook via GET: {dict(request.query_params)}")
        call_sid = request.query_params.get("CallSid", "unknown")
        to_number = request.query_params.get("To", "unknown")
        from_number = request.query_params.get("From", "unknown")
        
    logger.info(f"Twilio webhook parameters - CallSid: {call_sid}, To: {to_number}, From: {from_number}")
    
    # Look up SIP trunk ID for this number from Redis cache
    clean_to = to_number.replace("+", "")
    sip_trunk_id = None
    
    if _svc_clients.redis_client:
        try:
            # Try with + prefix first, then without
            sip_trunk_id = await _svc_clients.redis_client.get(f"twilio:sip_trunk:{to_number}")
            if not sip_trunk_id:
                sip_trunk_id = await _svc_clients.redis_client.get(f"twilio:sip_trunk:{clean_to}")
        except Exception as e:
            logger.warning(f"Redis lookup failed for Twilio SIP trunk: {e}")
    
    # Fallback to DB if not found in Redis
    if not sip_trunk_id:
        try:
            conn = await get_db_connection()
            row = await conn.fetchrow(
                "SELECT sip_trunk_id FROM org_configs WHERE phone_number IN ($1, $2)",
                to_number, clean_to
            )
            await conn.close()
            if row and row['sip_trunk_id']:
                sip_trunk_id = row['sip_trunk_id']
                logger.info(f"Found Twilio SIP trunk mapping in DB for {to_number}: {sip_trunk_id}")
                if _svc_clients.redis_client:
                    await _svc_clients.redis_client.set(f"twilio:sip_trunk:{to_number}", sip_trunk_id, ex=86400*30)
        except Exception as e:
            logger.warning(f"DB lookup failed for Twilio SIP trunk: {e}")

    # Fallback to env var if not found
    if not sip_trunk_id:
        sip_trunk_id = os.getenv("SIP_TRUNK_ID")
        if sip_trunk_id:
            logger.warning(f"No SIP trunk mapping found for {to_number}, using fallback from env: {sip_trunk_id}")
        else:
            logger.error(f"No SIP trunk mapping found for {to_number} and no SIP_TRUNK_ID in env")
            return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>', media_type="application/xml")
    
    sip_domain = _get_sip_domain()
        
    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial>
        <Sip>sip:{sip_trunk_id}@{sip_domain};transport=tcp</Sip>
    </Dial>
</Response>'''
    return Response(content=xml_content, media_type="application/xml")



@router.post("/v1/sip/inbound/setup")
async def setup_inbound_sip(request: Request):
    """
    End-to-end inbound SIP setup:
    1. Creates LiveKit Inbound Trunk
    2. Creates LiveKit Dispatch Rule
    3. Triggers provider API (Zadarma/Twilio/Plivo) to update the forwarding URI
    
    Payload:
    - number (required): Phone number in E.164 format (e.g., +918031321203)
    - org_id (required): Organization ID
    - provider (required): SIP provider - "zadarma", "twilio", "plivo", or "voice_link"
    - name (optional): Trunk name (default: "{provider} {number}")
    - prompt (optional): Agent prompt
    - voice (optional): Agent voice
    - model (optional): Agent model
    - kb_tags (optional): Knowledge base tags
    - transfer_numbers (optional): Transfer numbers config
    - client_name (optional): Client name
    - process_id (optional): Process ID
    """
    try:
        payload = await request.json()
    except Exception:
        payload = None

    # Log received payload
    if payload is not None:
        logger.info(
            f"=== [SIP INBOUND SETUP REQUEST] ===\n"
            f"Payload: {json.dumps(payload, indent=2)}"
        )
    else:
        logger.info(
            f"=== [SIP INBOUND SETUP REQUEST] ===\n"
            f"Invalid/Empty JSON Payload"
        )

    response = await _setup_inbound_sip_process(payload)

    # Log sent payload (response)
    status_code = response.status_code
    try:
        body = json.loads(response.body.decode('utf-8'))
        body_str = json.dumps(body, indent=2)
    except Exception:
        body_str = str(response.body)

    logger.info(
        f"=== [SIP INBOUND SETUP RESPONSE] ===\n"
        f"Status: {status_code}\n"
        f"Payload: {body_str}"
    )
    return response



@router.post("/v1/sip/plivo-dial-status")
async def plivo_dial_status(request: Request):
    """
    DEPRECATED: Replaced by Plivo Zentrunk SIP trunking.
    Kept for backward compatibility with numbers still using Plivo Application.
    """
    logger.warning("plivo_dial_status endpoint called (DEPRECATED - migrating to Zentrunk)")
    form_data = await request.form()
    logger.info(f"Received Plivo Dial Status callback: {dict(form_data)}")
    
    # Return empty response to Plivo to end the call
    xml_content = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=xml_content, media_type="application/xml")






@router.post("/v1/sip/trunks/outbound")
@router.post("/v1/sip/trunks/outbound/zadarma")
async def create_zadarma_sip_trunk(request: Request):
    """
    Create a new Zadarma SIP trunk.
    The root '/outbound' endpoint is maintained for backward compatibility.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    logger.info(
        f"[POST /v1/sip/trunks/outbound] Payload received: {json.dumps(payload, separators=(',', ':'))}"
    )

    try:
        trunk = await _create_sip_outbound_trunk(
            name=payload.get("name"),
            address=payload.get("address"),
            numbers=payload.get("numbers"),
            auth_username=payload.get("authUsername")
            or payload.get("auth_username")
            or payload.get("auth_user"),
            auth_password=payload.get("authPassword")
            or payload.get("auth_password")
            or payload.get("auth_pass"),
        )

        return JSONResponse(
            {
                "status": "success",
                "sip_trunk_id": trunk.sip_trunk_id,
                "name": trunk.name,
                "provider": "zadarma",
                "address": trunk.address,
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)



@router.post("/v1/sip/trunks/outbound/twilio")
async def create_twilio_sip_trunk(request: Request):
    """
    Create a new Twilio SIP trunk using professional nomenclature.
    Aligns with LiveKit CLI parameters: auth_user, auth_pass.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    logger.info(
        f"[POST /v1/sip/trunks/outbound/twilio] Payload received: {json.dumps(payload, separators=(',', ':'))}"
    )

    # Twilio-friendly field mapping (accepting both CLI-style and original keys)
    name = payload.get("name")
    address = payload.get("address") or "live-kit-mc.pstn.twilio.com"
    numbers = payload.get("numbers")
    auth_username = (
        payload.get("authUsername")
        or payload.get("auth_username")
        or payload.get("auth_user")
    )
    auth_password = (
        payload.get("authPassword")
        or payload.get("auth_password")
        or payload.get("auth_pass")
    )

    try:
        trunk = await _create_sip_outbound_trunk(
            name=name,
            address=address,
            numbers=numbers,
            auth_username=auth_username,
            auth_password=auth_password,
        )

        return JSONResponse(
            {
                "status": "success",
                "sip_trunk_id": trunk.sip_trunk_id,
                "name": trunk.name,
                "provider": "twilio",
                "address": trunk.address,
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/v1/sip/trunks/outbound/voice_link")
async def create_voicelink_sip_trunk(request: Request):
    """
    Create a new Voicelink SIP trunk.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"},
        status_code=400)

    if _svc_clients.voicelink_client is None:
        logger.error (" Voicelink_Client is none")
        return JSONResponse({"error": "VoiceLink not available"}, status_code=503)

    try:
        def _src(src):
            return dict(
                name=src.get("name"),
                address=src.get("address"),
                numbers=src.get("numbers"),
                auth_username=src.get("authUsername") or src.get("auth_username") or src.get("auth_user"),
                auth_password=src.get("authPassword") or src.get("auth_password") or src.get("auth_pass"),
            )

        sources = {
            "nested": payload.get("trunk"),
            "flat": payload if "numbers" in payload and (
                "authUsername" in payload or "auth_username" in payload or "auth_user" in payload
            ) else None,
        }
        matched = next((k for k, v in sources.items() if v), None)

        if matched:
            logger.info(f"Provisioning new SIP trunk (VoiceLink) — {matched} payload")
            trunk = await _create_sip_outbound_trunk(
                **_src(sources[matched]),
                client=_svc_clients.voicelink_client,
                destination_country="in",
            )
            trunk_id = trunk.sip_trunk_id
            if _svc_clients.redis_client:
                await _svc_clients.redis_client.set(f"trunk:provider:{trunk_id}", "voice_link", ex=86400 * 30)
        else:
            trunk_id = payload.get("trunk_id") or payload.get("call_from_id")

        if not trunk_id:
            return JSONResponse(
                {"error": "No trunk id provided"},
                status_code = 400
            )

        return {
            "status": "success",
            "sip_trunk_id": trunk_id,
            "provider": "voicelink",
        }
    except Exception as e:
        logger.error(f"Failed to create voicelink trunk: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)




@router.post("/v1/sip/trunks/outbound/plivo")
async def create_and_call_plivo(request: Request):
    """
    Unified Plivo endpoint to provision a SIP trunk (optional) and place an outbound call.
    Supports on-the-fly provisioning if 'trunk' details are provided,
    otherwise uses 'trunk_id' from the payload or environment.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    if _svc_clients.plivo_client is None:
        logger.error("_svc_clients.plivo_client is None — LIVEKIT_URL may be unset")
        return JSONResponse({"error": "Plivo client not available"}, status_code=503)

    try:
        # 1. Handle SIP Trunk (Provision new or use existing)
        trunk_data = payload.get("trunk")
        if trunk_data:
            logger.info("Provisioning new SIP trunk (Plivo) before call...")
            trunk = await _create_sip_outbound_trunk(
                name=trunk_data.get("name"),
                address=trunk_data.get("address"),
                numbers=trunk_data.get("numbers"),
                auth_username=trunk_data.get("authUsername")
                or trunk_data.get("auth_username")
                or trunk_data.get("auth_user"),
                auth_password=trunk_data.get("authPassword")
                or trunk_data.get("auth_password")
                or trunk_data.get("auth_pass"),
                client=_svc_clients.plivo_client,
                destination_country="in",
            )
            trunk_id = trunk.sip_trunk_id
        elif "numbers" in payload and (
            "authUsername" in payload
            or "auth_username" in payload
            or "auth_user" in payload
        ):
            logger.info("Flat trunk payload detected. Provisioning Plivo trunk...")
            trunk = await _create_sip_outbound_trunk(
                name=payload.get("name"),
                address=payload.get("address"),
                numbers=payload.get("numbers"),
                auth_username=payload.get("authUsername")
                or payload.get("auth_username")
                or payload.get("auth_user"),
                auth_password=payload.get("authPassword")
                or payload.get("auth_password")
                or payload.get("auth_pass"),
                client=_svc_clients.plivo_client,
                destination_country="in",
            )
            trunk_id = trunk.sip_trunk_id
        else:
            trunk_id = (
                payload.get("trunk_id")
                or payload.get("call_from_id")
                or os.getenv("SIP_TRUNK_ID")
            )

        if not trunk_id:
            return JSONResponse(
                {"error": "No trunk_id provided or configured"}, status_code=400
            )

        # 2. Extract Target Phone Number (optional if only provisioning/testing trunk)
        client_phone = payload.get("client_phone")
        if client_phone is not None:
            client_phone = str(client_phone).strip()

        if not client_phone:
            logger.info(
                f"No client_phone provided. Trunk {trunk_id} provisioned successfully."
            )
            return JSONResponse(
                {
                    "status": "success",
                    "sip_trunk_id": trunk_id,
                    "message": "Trunk provisioned successfully (no call initiated)",
                }
            )

        country_code = str(payload.get("client_country_code") or "").strip("+")
        if client_phone.startswith("+"):
            phone_number = client_phone
        elif country_code and client_phone:
            phone_number = f"+{country_code}{client_phone}"
        else:
            phone_number = client_phone

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
                        logger.info(f"[KB] Enriched Plivo outbound payload with kb_ids={kb_ids} for org_id={org_id}")
                    tag_row = await conn.fetchrow("SELECT kb_tags FROM org_configs WHERE org_id = $1 AND is_active = true", org_id)
                    if tag_row and tag_row["kb_tags"] and not payload.get("kb_tags"):
                        kb_tags = tag_row["kb_tags"] if isinstance(tag_row["kb_tags"], list) else []
                        if kb_tags:
                            payload["kb_tags"] = kb_tags
                finally:
                    await conn.close()
            except Exception as e:
                logger.warning(f"[KB] Plivo outbound enrichment skipped (non-fatal): {e}")

        # 3. Trigger Agent Dispatch — use direct client (no proxy needed for LiveKit Cloud)
        call_id = payload.get("call_id") or payload.get("voice_id") or int(time.time())
        room_name = f"call_{trunk_id}_{call_id}"

        # Smart Deduplication Lock: Prevent sub-second duplicate calls for the same call_id
        if _svc_clients.redis_client:
            lock_acquired = await _svc_clients.redis_client.set(f"lock:call:{call_id}", "1", nx=True, ex=30)
            if not lock_acquired:
                logger.warning(f"Duplicate Plivo call request ignored for call_id: {call_id} (call in-progress)")
                return JSONResponse({
                    "status": "ignored",
                    "message": f"Duplicate request for call_id {call_id} already in progress",
                    "room": room_name
                }, status_code=200)
        


        logger.info(f"Dispatching agent to room {room_name}")
        await _svc_clients.lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name, agent_name=AGENT_NAME, metadata=json.dumps(payload)
            )
        )

        # 4. Initiate SIP Call — use proxied client to route through Plivo's Indian infrastructure
        sip_number = payload.get("call_from")  # Caller ID
        if sip_number and not sip_number.startswith("+"):
            sip_number = f"+{sip_number}"

        logger.info(
            f"Placing SIP call to {phone_number} via trunk {trunk_id} (Caller ID: {sip_number})"
        )

        sip_part = await _svc_clients.plivo_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                sip_number=sip_number,
                room_name=room_name,
                participant_identity=f"sip_{call_id}",
                participant_name="Mantra Voice",
                play_ringtone=False,
                wait_until_answered=True,
            )
        )

        return JSONResponse(
            {
                "status": "success",
                "sip_trunk_id": trunk_id,
                "room": room_name,
                "participant": sip_part.participant_identity,
                "call_id": call_id,
            }
        )

    except Exception as e:
        logger.error(f"Plivo unified call failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/v1/sip/trunks/outbound")
async def list_sip_outbound_trunks():
    """
    List all SIP outbound trunks.
    Returns a collection of configured SIP trunks with their metadata.
    """
    try:
        response = await _svc_clients.lk_client.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest()
        )
        trunk_list = []
        for item in response.items:
            trunk_list.append(
                {
                    "sip_trunk_id": item.sip_trunk_id,
                    "name": item.name,
                    "address": item.address,
                    "transport": item.transport,
                    "numbers": list(item.numbers),
                    "auth_username": item.auth_username,
                    "encryption": item.media_encryption,
                }
            )

        return JSONResponse(
            {"status": "success", "count": len(trunk_list), "trunks": trunk_list}
        )
    except Exception as e:
        logger.error(f"Failed to list SIP outbound trunks: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/sip/trunks/outbound/{trunk_id}")
async def delete_sip_outbound_trunk(trunk_id: str):
    """
    Delete a SIP outbound trunk by its trunk ID.
    Permanently removes the trunk configuration from LiveKit.
    """
    if not trunk_id:
        return JSONResponse({"error": "Trunk ID is required"}, status_code=400)

    try:
        await _svc_clients.lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Successfully deleted SIP outbound trunk: {trunk_id}")

        return JSONResponse(
            {
                "status": "success",
                "message": f"SIP trunk {trunk_id} deleted successfully",
                "sip_trunk_id": trunk_id,
            }
        )
    except Exception as e:
        logger.error(f"Failed to delete SIP outbound trunk {trunk_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/v1/sip/trunks/inbound/{trunk_id}")
async def update_inbound_sip_trunk(trunk_id: str, request: Request):
    """Update fields on an existing inbound SIP trunk without recreating it.

    Supports partial updates for allowed addresses, allowed numbers,
    auth credentials, and metadata.  Only the fields provided in the
    request body are changed.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    kwargs = {}

    if "name" in payload:
        kwargs["name"] = payload["name"]

    if "metadata" in payload:
        kwargs["metadata"] = json.dumps(payload["metadata"]) if isinstance(payload["metadata"], dict) else payload["metadata"]

    if "auth_username" in payload:
        kwargs["auth_username"] = payload["auth_username"]

    if "auth_password" in payload:
        kwargs["auth_password"] = payload["auth_password"]

    if "numbers" in payload:
        nums = payload["numbers"]
        if isinstance(nums, str):
            nums = [n.strip() for n in nums.split(",") if n.strip()]
        kwargs["numbers"] = nums

    if "allowed_addresses" in payload:
        addrs = payload["allowed_addresses"]
        if isinstance(addrs, str):
            addrs = [a.strip() for a in addrs.split(",") if a.strip()]
        kwargs["allowed_addresses"] = addrs

    if "allowed_numbers" in payload:
        nums = payload["allowed_numbers"]
        if isinstance(nums, str):
            nums = [n.strip() for n in nums.split(",") if n.strip()]
        kwargs["allowed_numbers"] = nums

    if not kwargs:
        return JSONResponse({"error": "No updatable fields provided"}, status_code=400)

    try:
        await _svc_clients.lk_client.sip.update_inbound_trunk_fields(trunk_id, **kwargs)
        logger.info(f"Inbound trunk updated: {trunk_id}")
        return JSONResponse({
            "status": "success",
            "message": f"Inbound trunk {trunk_id} updated",
            "sip_trunk_id": trunk_id,
        })
    except Exception as e:
        logger.error(f"Failed to update inbound trunk {trunk_id}: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ──────────────────────────────────────────────
# SIP DISPATCH RULE UPDATE
# ──────────────────────────────────────────────


@router.patch("/v1/sip/dispatch-rules/{rule_id}")
async def update_sip_dispatch_rule(rule_id: str, request: Request):
    """Update fields on an existing SIP dispatch rule without recreating it."""
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    kwargs = {}

    if "name" in payload:
        kwargs["name"] = payload["name"]

    if "metadata" in payload:
        kwargs["metadata"] = json.dumps(payload["metadata"]) if isinstance(payload["metadata"], dict) else payload["metadata"]

    if "attributes" in payload and isinstance(payload["attributes"], dict):
        kwargs["attributes"] = payload["attributes"]

    if "trunk_ids" in payload:
        tids = payload["trunk_ids"]
        if isinstance(tids, str):
            tids = [t.strip() for t in tids.split(",") if t.strip()]
        kwargs["trunk_ids"] = tids

    if "rule" in payload:
        rule_config = payload["rule"]
        room_prefix = rule_config.get("room_prefix", "inbound_")
        pin = rule_config.get("pin", "")
        no_randomness = rule_config.get("no_randomness", False)
        kwargs["rule"] = proto_sip.SIPDispatchRule(
            dispatch_rule_individual=proto_sip.SIPDispatchRuleIndividual(
                room_prefix=room_prefix,
                pin=pin,
                no_randomness=no_randomness,
            )
        )

    if not kwargs:
        return JSONResponse({"error": "No updatable fields provided"}, status_code=400)

    try:
        await _svc_clients.lk_client.sip.update_dispatch_rule_fields(rule_id, **kwargs)
        logger.info(f"Dispatch rule updated: {rule_id}")
        return JSONResponse({
            "status": "success",
            "message": f"Dispatch rule {rule_id} updated",
            "sip_dispatch_rule_id": rule_id,
        })
    except Exception as e:
        logger.error(f"Failed to update dispatch rule {rule_id}: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/config")
async def get_config():
    """Return the LiveKit URL for the frontend."""
    return JSONResponse({"url": os.getenv("LIVEKIT_URL")})


# ── Dashboard API (authenticated) ────────────────────────────────────────



