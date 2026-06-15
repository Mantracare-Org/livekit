import os
import sys
import logging
import json
import time
import traceback
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from livekit import api
from dotenv import load_dotenv


# Load environment variables from .env.local
load_dotenv(".env.local")

logger = logging.getLogger("mantra.ui_server")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s INFO %(name)s: %(message)s"))
logger.addHandler(_handler)
logger.propagate = False

# Persistent LiveKit API clients
lk_client: api.LiveKitAPI = None           # Direct — used for Twilio, Zadarma, and general operations
plivo_client: api.LiveKitAPI = None        # Proxied — used for Plivo (India routing)
plivo_session: aiohttp.ClientSession = None  # Owned session for plivo_client; closed manually on shutdown

@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_client, plivo_client, plivo_session
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")

    if lk_url:
        if lk_url.startswith("wss://"):
            api_url = lk_url.replace("wss://", "https://")
        elif lk_url.startswith("ws://"):
            api_url = lk_url.replace("ws://", "http://")
        else:
            api_url = lk_url

        logger.info(f"Connecting to LiveKit API at {api_url}")

        lk_client = api.LiveKitAPI(url=api_url, api_key=api_key, api_secret=api_secret)

        plivo_proxy = os.getenv("PLIVO_PROXY")
        if plivo_proxy:
            logger.info(f"Creating Plivo LiveKit client with proxy: {plivo_proxy}")
        else:
            logger.info("Creating Plivo LiveKit client without proxy (PLIVO_PROXY not set)")
        plivo_session = aiohttp.ClientSession(proxy=plivo_proxy)
        plivo_client = api.LiveKitAPI(
            url=api_url, api_key=api_key, api_secret=api_secret, session=plivo_session
        )

    yield

    for client in [lk_client, plivo_client]:
        if client:
            await client.aclose()
    if plivo_session:
        await plivo_session.close()

app = FastAPI(lifespan=lifespan)

# ── Request logging middleware ────────────────────────────────────────
# Suppress uvicorn's default access log (we handle it ourselves with timing + filtering)

SCANNER_PATHS = (
    "/.well-known/", "/favicon", "/wp-",
    "/blog/", "/web/", "/wordpress/", "/website/",
    "/wp/", "/news/", "/2018/", "/2019/", "/shop/",
    "/wp1/", "/test/", "/media/", "/wp2/", "/site/",
    "/cms/", "/sito/",
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    client_host = request.client.host if request.client else "unknown"
    path = request.url.path
    try:
        response = await call_next(request)
        duration = time.time() - start

        # Suppress scanner junk at INFO level
        if path.startswith(SCANNER_PATHS):
            logger.debug(f"Scanner: {client_host} {request.method} {path} {response.status_code}")
        else:
            logger.info(f"{client_host} {request.method} {path} {response.status_code} in {duration*1000:.0f}ms")

        return response
    except Exception as e:
        duration = time.time() - start
        logger.error(f"{client_host} {request.method} {path} ERROR in {duration*1000:.0f}ms: {e}")
        raise  # let FastAPI handle the error response

# Setup file logging to capture all logs including uvicorn
_file_handler = logging.FileHandler("/home/fardeen/lkt/app.log")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logging.getLogger().addHandler(_file_handler)
# Also add directly to mantra.ui_server logger to be absolutely sure
logger.addHandler(_file_handler)


# Get the directory of the current file
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def index():
    """Serve the main UI."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/health")
async def health():
    """Simple health check."""
    return {"status": "ok", "service": "ui_server"}

@app.post("/dispatch-test")
async def dispatch_test(request: Request):
    """
    Manually trigger an agent dispatch with a custom payload.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Manual dispatch request with payload: {json.dumps(payload, separators=(',',':'))}")
    
    # Generate a unique room name for this test session using the call_id if provided
    call_id = payload.get("call_id") or int(time.time())
    room_name = f"test_{call_id}"
    
    try:
        # Create dispatch with payload as metadata
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(payload)
            )
        )
        logger.info(f"Successfully dispatched agent to room {room_name}, dispatch_id: {dispatch.id}")
    except Exception as e:
        logger.error(f"Dispatch failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Generate token for the user to join the same room
    token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
        .with_identity("Tester") \
        .with_name("Manual Tester") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))

    return JSONResponse({
        "status": "success",
        "room": room_name,
        "token": token.to_jwt(),
        "url": os.getenv("LIVEKIT_URL"),
    })


@app.post("/api/v1/test/inbound-call")
async def test_inbound_call(request: Request):
    """
    Simulates an inbound call by triggering an outbound SIP call but dispatching
    the agent with the 'inbound' direction metadata so it acts like an inbound call.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Test inbound call request: {json.dumps(payload, indent=2)}")
    
    call_id = int(time.time())
    room_name = f"test_inbound_{call_id}"
    
    # Force the direction to inbound so the agent handles it correctly
    payload["direction"] = "inbound"
    payload["call_id"] = call_id
    
    # 1. Trigger agent dispatch
    try:
        logger.info(f"Dispatching agent to room {room_name}")
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
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

        sip_part = await lk_client.sip.create_sip_participant(
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


@app.post("/api/v1/sip/trunks/inbound")
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
        trunk = await lk_client.sip.create_inbound_trunk(trunk_request)
        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk.sip_trunk_id,
            "name": trunk.name,
            "numbers": list(trunk.numbers)
        })
    except Exception as e:
        logger.error(f"Failed to create inbound trunk: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/v1/sip/trunks/inbound")
async def list_sip_inbound_trunks():
    """
    List all SIP Inbound Trunks configured in LiveKit.
    """
    try:
        response = await lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
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


@app.delete("/api/v1/sip/trunks/inbound/{trunk_id}")
async def delete_sip_inbound_trunk(trunk_id: str):
    """
    Delete a SIP Inbound Trunk by its ID.
    """
    if not trunk_id:
        return JSONResponse({"error": "Trunk ID is required"}, status_code=400)
    
    try:
        await lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Successfully deleted SIP Inbound Trunk: {trunk_id}")
        
        return JSONResponse({
            "status": "success",
            "message": f"SIP inbound trunk {trunk_id} deleted successfully"
        })
    except Exception as e:
        logger.error(f"Failed to delete SIP inbound trunk {trunk_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/sip/dispatch-rules")
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
                        agent_name="mantra-agent",
                        metadata=json.dumps(payload)
                    )
                ]
            ),
            trunk_ids=[trunk_id]
        )
        # Using lk_client directly as rules are managed at LiveKit cloud level
        rule = await lk_client.sip.create_sip_dispatch_rule(req)
        
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


@app.get("/api/v1/sip/dispatch-rules")
async def list_dispatch_rules():
    """
    List all SIP Dispatch Rules configured in LiveKit.
    """
    try:
        response = await lk_client.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
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


@app.delete("/api/v1/sip/dispatch-rules/{rule_id}")
async def delete_dispatch_rule(rule_id: str):
    """
    Delete a SIP Dispatch Rule by its ID.
    """
    if not rule_id:
        return JSONResponse({"error": "Rule ID is required"}, status_code=400)
    
    try:
        await lk_client.sip.delete_dispatch_rule(
            api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
        )
        logger.info(f"Successfully deleted SIP Dispatch Rule: {rule_id}")
        
        return JSONResponse({
            "status": "success",
            "message": f"SIP dispatch rule {rule_id} deleted successfully"
        })
    except Exception as e:
        logger.error(f"Failed to delete dispatch rule {rule_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@app.get("/api/v1/sip/plivo-xml")
@app.post("/api/v1/sip/plivo-xml")
async def plivo_xml(request: Request):
    """
    Returns XML for Plivo Application to route to the LiveKit SIP Trunk.
    Passes a dynamic X-Room-Name SIP header to guarantee a unique, non-empty room name.
    """
    call_uuid = "unknown"
    to_number = "918031321203"
    from_number = "unknown"
    
    if request.method == "POST":
        form_data = await request.form()
        logger.info(f"Received Plivo XML request via POST: {dict(form_data)}")
        call_uuid = form_data.get("CallUUID", "unknown")
        to_number = form_data.get("To", "918031321203")
        from_number = form_data.get("From", "unknown")
    elif request.method == "GET":
        logger.info(f"Received Plivo XML request via GET: {dict(request.query_params)}")
        call_uuid = request.query_params.get("CallUUID", "unknown")
        to_number = request.query_params.get("To", "918031321203")
        from_number = request.query_params.get("From", "unknown")
        
    logger.info(f"Plivo XML parameters - CallUUID: {call_uuid}, To: {to_number}, From: {from_number}")
        
    lk_url = os.getenv("LIVEKIT_URL", "")
    host_lk = lk_url.replace("wss://", "").replace("ws://", "")
    if "mantraassist-0ek43ife" in host_lk:
        sip_domain = "4mp2ouvchg3.india.sip.livekit.cloud"
    elif "livekit.cloud" in host_lk:
        subdomain = host_lk.split(".")[0]
        # Fallback to default behavior if it's a different project
        sip_domain = f"{subdomain}.sip.livekit.cloud"
    else:
        sip_domain = "4mp2ouvchg3.india.sip.livekit.cloud"
        
    # Build absolute action URL dynamically using headers for ngrok support
    req_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8081"
    req_scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    action_url = f"{req_scheme}://{req_host}/api/v1/sip/plivo-dial-status"

    
    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial action="{action_url}" method="POST">
        <User>sip:ST_9C476YUSTSfm@{sip_domain};transport=tcp</User>
    </Dial>
</Response>'''
    return Response(content=xml_content, media_type="application/xml")



@app.post("/api/v1/sip/plivo-dial-status")
async def plivo_dial_status(request: Request):
    """
    Callback from Plivo when the Dial attempt completes.
    """
    form_data = await request.form()
    logger.info(f"Received Plivo Dial Status callback: {dict(form_data)}")
    
    # Return empty response to Plivo to end the call
    xml_content = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=xml_content, media_type="application/xml")



@app.post("/api/v1/webhooks/telephony")
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
    
    # Use call_id or voice_id from payload if available, otherwise use timestamp
    call_id = payload.get("call_id") or payload.get("voice_id") or payload.get("event_id") or int(time.time())
    room_name = f"call_{call_id}"
    
    # Construct phone number in E.164 format
    country_code = payload.get("client_country_code", "").strip("+")
    client_phone = payload.get("client_phone", "").strip()
    
    if client_phone.startswith("+"):
        phone_number = client_phone
    elif country_code and client_phone:
        phone_number = f"+{country_code}{client_phone}"
    else:
        phone_number = client_phone # Fallback
    
    if not phone_number:
        return JSONResponse({"error": "No client_phone provided in payload"}, status_code=400)

    # Resolve trunk ID and detect provider for logging
    trunk_id = payload.get("trunk_id") or payload.get("call_from_id") or os.getenv("SIP_TRUNK_ID")
    if not trunk_id:
        return JSONResponse({"error": "No SIP trunk ID configured"}, status_code=500)

    provider = await _get_provider_from_trunk(trunk_id)
    logger.info(f"Provider detected: {provider} — using direct LiveKit client for API calls")

    # Trigger agent dispatch — always use lk_client (direct, no proxy)
    # LiveKit Cloud API calls don't need the Indian proxy; region pinning is on the trunk itself
    try:
        logger.info(f"Step 1: Creating agent dispatch for room {room_name}")
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(payload)
            )
        )
        logger.info(f"Dispatch created: {dispatch.id}")
    except Exception as e:
        logger.error(f"Agent dispatch failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"Agent dispatch failed: {str(e)}"}, status_code=500)

    # Trigger SIP outbound call — always use lk_client (direct, no proxy)
    # The trunk's destination_country="in" handles Indian region routing at the SIP layer
    try:
        sip_number = payload.get("call_from")
        if sip_number and not sip_number.startswith("+"):
            sip_number = f"+{sip_number}"
            
        logger.info(f"Step 2: Initiating SIP call to {phone_number} via trunk {trunk_id}" + (f" (Caller ID: {sip_number})" if sip_number else ""))

        sip_part = await lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                sip_number=sip_number,
                room_name=room_name,
                participant_identity=f"sip_{call_id}",
                participant_name="SIP Caller"
            )
        )
        logger.info(f"SIP Participant created: {sip_part.participant_identity}")
    except Exception as e:
        logger.error(f"SIP Call trigger failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"SIP Call trigger failed: {str(e)}"}, status_code=500)

    # Generate token for anyone needing to join/monitor the call
    token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
        .with_identity(f"monitor_{call_id}") \
        .with_name("Call Monitor") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=False,
            can_subscribe=True,
        ))

    return JSONResponse({
        "status": "success",
        "message": f"Agent dispatched for {event_name}",
        "client_name": payload.get("client_name", "Unknown"),
        "purpose": payload.get("prompt", "Voice interaction")[:100] + ("..." if len(payload.get("prompt", "")) > 100 else ""),
        "room": room_name,
        "token": token.to_jwt(),
        "url": os.getenv("LIVEKIT_URL"),
    })


async def _create_sip_outbound_trunk(
    name: str, address: str, numbers: list, auth_username: str, auth_password: str,
    client: api.LiveKitAPI = None, destination_country: str = None,
):
    if not all([name, address, numbers, auth_username, auth_password]):
        missing = [f for f, v in [("name", name), ("address", address), ("numbers", numbers),
                                   ("auth_username", auth_username), ("auth_password", auth_password)] if not v]
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.split(",") if n.strip()]
    elif not isinstance(numbers, list):
        numbers = [str(numbers)]

    svc = (client or lk_client).sip
    try:
        logger.info(f"Creating SIP outbound trunk: {name} at {address}")
        trunk_request = api.CreateSIPOutboundTrunkRequest(
            trunk=api.SIPOutboundTrunkInfo(
                name=name, address=address, numbers=numbers,
                auth_username=auth_username, auth_password=auth_password,
                destination_country=destination_country
            )
        )
        trunk = await svc.create_outbound_trunk(trunk_request)
        logger.info(f"Successfully created SIP outbound trunk: {trunk.sip_trunk_id} ({name})")
        return trunk
    except Exception as e:
        logger.error(f"LiveKit API error creating SIP trunk: {e}")
        raise


DEFAULT_PROVIDER = "zadarma"

async def _get_provider_from_trunk(trunk_id: str) -> str:
    """Fetch the specific trunk by ID and infer the provider from its address."""
    try:
        response = await lk_client.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest(trunk_ids=[trunk_id])
        )
        if response.items:
            trunk = response.items[0]
            address = (trunk.address or "").lower()
            if "twilio" in address:
                return "twilio"
            elif "plivo" in address:
                return "plivo"
            return DEFAULT_PROVIDER
        logger.warning(f"Trunk {trunk_id} not found — defaulting to {DEFAULT_PROVIDER}")
        return DEFAULT_PROVIDER
    except Exception as e:
        logger.error(f"Failed to fetch trunk {trunk_id} for provider detection: {e}")
        return DEFAULT_PROVIDER


@app.post("/api/v1/sip/trunks/outbound")
@app.post("/api/v1/sip/trunks/outbound/zadarma")
async def create_zadarma_sip_trunk(request: Request):
    """
    Create a new Zadarma SIP trunk. 
    The root '/outbound' endpoint is maintained for backward compatibility.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"[POST /api/v1/sip/trunks/outbound] Payload received: {json.dumps(payload, separators=(',',':'))}")
    
    try:
        trunk = await _create_sip_outbound_trunk(
            name=payload.get("name"),
            address=payload.get("address"),
            numbers=payload.get("numbers"),
            auth_username=payload.get("authUsername") or payload.get("auth_username") or payload.get("auth_user"),
            auth_password=payload.get("authPassword") or payload.get("auth_password") or payload.get("auth_pass")
        )
        
        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk.sip_trunk_id,
            "name": trunk.name,
            "provider": "zadarma",
            "address": trunk.address
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/sip/trunks/outbound/twilio")
async def create_twilio_sip_trunk(request: Request):
    """
    Create a new Twilio SIP trunk using professional nomenclature.
    Aligns with LiveKit CLI parameters: auth_user, auth_pass.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"[POST /api/v1/sip/trunks/outbound/twilio] Payload received: {json.dumps(payload, separators=(',',':'))}")
    
    # Twilio-friendly field mapping (accepting both CLI-style and original keys)
    name = payload.get("name")
    address = payload.get("address") or "live-kit-mc.pstn.twilio.com"
    numbers = payload.get("numbers")
    auth_username = payload.get("authUsername") or payload.get("auth_username") or payload.get("auth_user")
    auth_password = payload.get("authPassword") or payload.get("auth_password") or payload.get("auth_pass")
    
    try:
        trunk = await _create_sip_outbound_trunk(
            name=name,
            address=address,
            numbers=numbers,
            auth_username=auth_username,
            auth_password=auth_password
        )
        
        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk.sip_trunk_id,
            "name": trunk.name,
            "provider": "twilio",
            "address": trunk.address
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/sip/trunks/outbound/plivo")
async def create_and_call_plivo(request: Request):
    """
    Unified Plivo endpoint to provision a SIP trunk (optional) and place an outbound call.
    Supports on-the-fly provisioning if 'trunk' details are provided, 
    otherwise uses 'trunk_id' from the payload or environment.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    if plivo_client is None:
        logger.error("plivo_client is None — LIVEKIT_URL may be unset")
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
                auth_username=trunk_data.get("authUsername") or trunk_data.get("auth_username") or trunk_data.get("auth_user"),
                auth_password=trunk_data.get("authPassword") or trunk_data.get("auth_password") or trunk_data.get("auth_pass"),
                client=plivo_client,
                destination_country="in"
            )
            trunk_id = trunk.sip_trunk_id
        elif "numbers" in payload and ("authUsername" in payload or "auth_username" in payload or "auth_user" in payload):
            logger.info("Flat trunk payload detected. Provisioning Plivo trunk...")
            trunk = await _create_sip_outbound_trunk(
                name=payload.get("name"),
                address=payload.get("address"),
                numbers=payload.get("numbers"),
                auth_username=payload.get("authUsername") or payload.get("auth_username") or payload.get("auth_user"),
                auth_password=payload.get("authPassword") or payload.get("auth_password") or payload.get("auth_pass"),
                client=plivo_client,
                destination_country="in"
            )
            trunk_id = trunk.sip_trunk_id
        else:
            trunk_id = payload.get("trunk_id") or payload.get("call_from_id") or os.getenv("SIP_TRUNK_ID")

        if not trunk_id:
            return JSONResponse({"error": "No trunk_id provided or configured"}, status_code=400)

        # 2. Extract Target Phone Number (optional if only provisioning/testing trunk)
        client_phone = payload.get("client_phone")
        if client_phone is not None:
            client_phone = str(client_phone).strip()

        if not client_phone:
            logger.info(f"No client_phone provided. Trunk {trunk_id} provisioned successfully.")
            return JSONResponse({
                "status": "success",
                "sip_trunk_id": trunk_id,
                "message": "Trunk provisioned successfully (no call initiated)"
            })

        country_code = str(payload.get("client_country_code") or "").strip("+")
        if client_phone.startswith("+"):
            phone_number = client_phone
        elif country_code and client_phone:
            phone_number = f"+{country_code}{client_phone}"
        else:
            phone_number = client_phone

        # 3. Trigger Agent Dispatch — use direct client (no proxy needed for LiveKit Cloud)
        call_id = payload.get("call_id") or payload.get("voice_id") or int(time.time())
        room_name = f"call_{call_id}"

        logger.info(f"Dispatching agent to room {room_name}")
        await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(payload)
            )
        )

        # 4. Initiate SIP Call — use proxied client to route through Plivo's Indian infrastructure
        sip_number = payload.get("call_from")  # Caller ID
        if sip_number and not sip_number.startswith("+"):
            sip_number = f"+{sip_number}"
            
        logger.info(f"Placing SIP call to {phone_number} via trunk {trunk_id} (Caller ID: {sip_number})")

        sip_part = await plivo_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                sip_number=sip_number,
                room_name=room_name,
                participant_identity=f"sip_{call_id}",
                participant_name="Mantra Voice"
            )
        )

        return JSONResponse({
            "status": "success",
            "sip_trunk_id": trunk_id,
            "room": room_name,
            "participant": sip_part.participant_identity,
            "call_id": call_id
        })

    except Exception as e:
        logger.error(f"Plivo unified call failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/v1/sip/trunks/outbound")
async def list_sip_outbound_trunks():
    """
    List all SIP outbound trunks.
    Returns a collection of configured SIP trunks with their metadata.
    """
    try:
        response = await lk_client.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest()
        )
        trunk_list = []
        for item in response.items:
            trunk_list.append({
                "sip_trunk_id": item.sip_trunk_id,
                "name": item.name,
                "address": item.address,
                "transport": item.transport,
                "numbers": list(item.numbers),
                "auth_username": item.auth_username,
                "encryption": item.media_encryption,
            })
        
        return JSONResponse({
            "status": "success",
            "count": len(trunk_list),
            "trunks": trunk_list
        })
    except Exception as e:
        logger.error(f"Failed to list SIP outbound trunks: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/v1/sip/trunks/outbound/{trunk_id}")
async def delete_sip_outbound_trunk(trunk_id: str):
    """
    Delete a SIP outbound trunk by its trunk ID.
    Permanently removes the trunk configuration from LiveKit.
    """
    if not trunk_id:
        return JSONResponse({"error": "Trunk ID is required"}, status_code=400)
    
    try:
        await lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Successfully deleted SIP outbound trunk: {trunk_id}")
        
        return JSONResponse({
            "status": "success",
            "message": f"SIP trunk {trunk_id} deleted successfully",
            "sip_trunk_id": trunk_id
        })
    except Exception as e:
        logger.error(f"Failed to delete SIP outbound trunk {trunk_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/config")
async def get_config():
    """Return the LiveKit URL for the frontend."""
    return JSONResponse({
        "url": os.getenv("LIVEKIT_URL")
    })

def main():
    import uvicorn
    port = int(os.getenv("PORT", "8081"))
    logger.info(f"UI Server starting on http://0.0.0.0:{port}")
    uvicorn.run("mantra.ui_server:app", host="0.0.0.0", port=port, reload=True, access_log=False)

if __name__ == "__main__":
    main()
