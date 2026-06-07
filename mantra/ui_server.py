import os

# Force-disable global proxy so the default LiveKit client (Twilio/Zadarma) bypasses it.
# The plivo_client will still explicitly use PLIVO_PROXY via its session.
_PROXY_VARS = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
_removed_proxies = []
for proxy_var in _PROXY_VARS:
    if proxy_var in os.environ:
        _removed_proxies.append(f"{proxy_var}={os.environ[proxy_var]}")
        del os.environ[proxy_var]

import logging
import json
import time
import traceback
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from livekit.protocol import sip as proto_sip
from livekit.protocol import room as proto_room
from livekit.protocol import agent_dispatch as proto_agent_dispatch
from dotenv import load_dotenv

# Load environment variables from .env.local
load_dotenv(".env.local")

# Second proxy cleanup: dotenv may have re-introduced proxy vars from .env files
for proxy_var in _PROXY_VARS:
    if proxy_var in os.environ:
        _removed_proxies.append(f"{proxy_var}={os.environ[proxy_var]} (via dotenv)")
        del os.environ[proxy_var]

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
        if _removed_proxies:
            logger.info(f"Proxy cleanup: removed {_removed_proxies} from environment")
        else:
            logger.info("Proxy cleanup: no proxy env vars found (clean environment)")

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
logger = logging.getLogger("mantra.ui_server")
logger.setLevel(logging.INFO)

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
    
    logger.info(f"Manual dispatch request with payload: {json.dumps(payload, indent=2)}")
    
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
    Test inbound call behavior by having the system call YOUR phone.
    The agent will think it's an inbound call and greet you accordingly.
    Requires an outbound SIP trunk to place the call to your phone.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    phone = payload.get("phone") or payload.get("client_phone")
    country_code = str(payload.get("country_code") or payload.get("client_country_code") or "91").strip("+")
    trunk_id = payload.get("trunk_id") or os.getenv("SIP_TRUNK_ID")
    prompt = payload.get("prompt", "You are a healthcare assistant. Greet the caller warmly and ask how you can help them today.")
    voice = payload.get("voice", "arushi")
    model = payload.get("model", "openai")

    if not phone:
        return JSONResponse({"error": "phone is required"}, status_code=400)
    if not trunk_id:
        return JSONResponse({"error": "No SIP trunk ID configured. Set trunk_id in payload or SIP_TRUNK_ID in env."}, status_code=500)

    phone_number = f"+{country_code}{phone}" if not phone.startswith("+") else phone
    call_id = payload.get("call_id") or int(time.time())
    room_name = f"test_inbound_{call_id}"

    agent_metadata = {
        "direction": "inbound",
        "call_id": str(call_id),
        "client_phone": phone_number,
        "prompt": prompt,
        "voice": voice,
        "model": model,
    }

    try:
        logger.info(f"[Inbound Test] Step 1: Dispatching agent to room {room_name}")
        await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(agent_metadata)
            )
        )

        logger.info(f"[Inbound Test] Step 2: Calling {phone_number} via trunk {trunk_id}")
        sip_part = await lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"sip_{call_id}",
                participant_name="Inbound Test Caller"
            )
        )

        logger.info(f"[Inbound Test] Call initiated: {sip_part.participant_identity}")
        return JSONResponse({
            "status": "success",
            "message": "Agent dispatched as inbound. You should receive a call shortly.",
            "room": room_name,
            "phone": phone_number,
            "participant": sip_part.participant_identity,
        })
    except Exception as e:
        logger.error(f"[Inbound Test] Failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


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
    logger.info(f"Webhook received call request for event {event_name}: {json.dumps(payload, indent=2)}")
    
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


async def _get_provider_from_trunk(trunk_id: str) -> str:
    """Fetch the specific trunk by ID and infer the provider from its address.

    Returns 'generic' for unrecognised providers so the caller can fall back
    to a direct LiveKit client without provider-specific routing.
    """
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
            elif "zadarma" in address:
                return "zadarma"
            return "generic"
        logger.warning(f"Trunk {trunk_id} not found — returning 'none'")
        return "none"
    except Exception as e:
        logger.error(f"Failed to fetch trunk {trunk_id} for provider detection: {e}")
        return "none"


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
    
    logger.info(f"[POST /api/v1/sip/trunks/outbound] Payload received: {json.dumps(payload, indent=2)}")
    
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
    
    logger.info(f"[POST /api/v1/sip/trunks/outbound/twilio] Payload received: {json.dumps(payload, indent=2)}")
    
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

        # 4. Initiate SIP Call — use direct client (trunk's destination_country handles region)
        sip_number = payload.get("call_from")  # Caller ID
        logger.info(f"Placing SIP call to {phone_number} via trunk {trunk_id} (Caller ID: {sip_number})")

        sip_part = await lk_client.sip.create_sip_participant(
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


# ──────────────────────────────────────────────
# INBOUND SIP TRUNK CRUD
# ──────────────────────────────────────────────

@app.post("/api/v1/sip/trunks/inbound")
async def create_inbound_sip_trunk(request: Request):
    """Create a SIP inbound trunk for receiving incoming calls from any provider/IVR.

    Supports SIP header → room attribute mapping so external IVRs can pass
    call context (e.g. X-Account-Number, X-Call-Reason, X-Department) via
    custom SIP headers.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    name = payload.get("name")
    numbers = payload.get("numbers")
    auth_username = payload.get("authUsername") or payload.get("auth_username") or payload.get("auth_user")
    auth_password = payload.get("authPassword") or payload.get("auth_password") or payload.get("auth_pass")

    if not all([name, numbers, auth_username, auth_password]):
        missing = [f for f, v in [
            ("name", name), ("numbers", numbers),
            ("auth_username", auth_username), ("auth_password", auth_password)
        ] if not v]
        return JSONResponse({"error": f"Missing required fields: {', '.join(missing)}"}, status_code=400)

    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.split(",") if n.strip()]

    # SIP header → room attribute mapping for IVR context passthrough
    headers_to_attributes = payload.get("headers_to_attributes", {})
    if isinstance(headers_to_attributes, str):
        try:
            headers_to_attributes = json.loads(headers_to_attributes)
        except Exception:
            headers_to_attributes = {}

    # Which SIP headers to forward: "none", "x_headers" (default), or "all"
    include_headers_raw = payload.get("include_headers", "x_headers")
    include_header_enum = {
        "none": proto_sip.SIP_NO_HEADERS,
        "x_headers": proto_sip.SIP_X_HEADERS,
        "all": proto_sip.SIP_ALL_HEADERS,
    }.get(include_headers_raw.lower(), proto_sip.SIP_X_HEADERS)

    allowed_addresses = payload.get("allowed_addresses", [])
    if isinstance(allowed_addresses, str):
        allowed_addresses = [a.strip() for a in allowed_addresses.split(",") if a.strip()]

    allowed_numbers = payload.get("allowed_numbers", [])
    if isinstance(allowed_numbers, str):
        allowed_numbers = [n.strip() for n in allowed_numbers.split(",") if n.strip()]

    logger.info(f"Creating inbound SIP trunk: {name} — numbers: {numbers}")
    if headers_to_attributes:
        logger.info(f"SIP header mapping: {headers_to_attributes}")

    try:
        trunk = proto_sip.SIPInboundTrunkInfo(
            name=name,
            numbers=numbers,
            auth_username=auth_username,
            auth_password=auth_password,
            metadata=json.dumps(payload.get("metadata", {})),
            headers_to_attributes=headers_to_attributes,
            include_headers=include_header_enum,
            allowed_addresses=allowed_addresses,
            allowed_numbers=allowed_numbers,
        )
        req = proto_sip.CreateSIPInboundTrunkRequest(trunk=trunk)
        result = await lk_client.sip.create_inbound_trunk(req)

        logger.info(f"Inbound trunk created: {result.sip_trunk_id} ({name})")
        return JSONResponse({
            "status": "success",
            "sip_trunk_id": result.sip_trunk_id,
            "name": result.name,
            "numbers": list(result.numbers),
        })
    except Exception as e:
        logger.error(f"Failed to create inbound trunk: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/v1/sip/trunks/inbound")
async def list_inbound_sip_trunks():
    """List all SIP inbound trunks."""
    try:
        req = proto_sip.ListSIPInboundTrunkRequest()
        response = await lk_client.sip.list_inbound_trunk(req)

        trunk_list = []
        for item in response.items:
            trunk_list.append({
                "sip_trunk_id": item.sip_trunk_id,
                "name": item.name,
                "numbers": list(item.numbers),
                "allowed_addresses": list(item.allowed_addresses),
                "allowed_numbers": list(item.allowed_numbers),
                "media_encryption": item.media_encryption,
                "created_at": str(item.created_at),
            })

        return JSONResponse({
            "status": "success",
            "count": len(trunk_list),
            "trunks": trunk_list,
        })
    except Exception as e:
        logger.error(f"Failed to list inbound trunks: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/v1/sip/trunks/inbound/{trunk_id}")
async def delete_inbound_sip_trunk(trunk_id: str):
    """Delete a SIP inbound trunk by its trunk ID."""
    if not trunk_id:
        return JSONResponse({"error": "Trunk ID is required"}, status_code=400)

    try:
        await lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Deleted inbound trunk: {trunk_id}")
        return JSONResponse({
            "status": "success",
            "message": f"Inbound trunk {trunk_id} deleted",
            "sip_trunk_id": trunk_id,
        })
    except Exception as e:
        logger.error(f"Failed to delete inbound trunk {trunk_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ──────────────────────────────────────────────
# SIP DISPATCH RULE CRUD (for inbound call routing)
# ──────────────────────────────────────────────

@app.post("/api/v1/sip/dispatch-rules")
async def create_sip_dispatch_rule(request: Request):
    """
    Create a SIP dispatch rule that routes inbound calls to rooms
    and auto-dispatches the mantra-agent.

    Supports DNIS-based routing (inbound_numbers), SIP header attribute
    passthrough, PIN-gated entry, and deterministic room naming for
    integration with external IVR systems.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    trunk_id = payload.get("trunk_id") or payload.get("trunk_ids")
    if not trunk_id:
        return JSONResponse({"error": "trunk_id is required"}, status_code=400)

    trunk_ids = [trunk_id] if isinstance(trunk_id, str) else trunk_id

    name = payload.get("name", "inbound-agent-rule")
    room_prefix = payload.get("room_prefix", "inbound_")
    metadata = payload.get("metadata", {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    # ── DNIS-based routing: only match calls to these specific DID numbers ──
    inbound_numbers = payload.get("inbound_numbers") or payload.get("numbers")
    if isinstance(inbound_numbers, str):
        inbound_numbers = [n.strip() for n in inbound_numbers.split(",") if n.strip()]

    # ── No-randomness mode: room name = {room_prefix}{inbound_number} ──
    no_randomness = payload.get("no_randomness", False)
    pin = payload.get("pin", "")

    agent_prompt = payload.get("prompt", "You are receiving an inbound healthcare call. Greet the caller warmly and assist them.")
    voice = payload.get("voice", "arushi")
    model = payload.get("model", "openai")

    # ── Room attributes (also accessible by the agent via room metadata) ──
    attributes = {"direction": "inbound"}
    custom_attributes = payload.get("attributes", {})
    if isinstance(custom_attributes, dict):
        attributes.update(custom_attributes)

    # ── Agent metadata — merge SIP-header-extracted context ──
    agent_metadata = {
        "direction": "inbound",
        "trunk_id": trunk_ids[0],
        "prompt": agent_prompt,
        "voice": voice,
        "model": model,
    }
    # Forward known IVR context keys from attributes if present
    for ctx_key in ["account_number", "call_reason", "department", "language", "user_id", "caller_choice"]:
        if ctx_key in attributes:
            agent_metadata[ctx_key] = attributes[ctx_key]

    agent_metadata.update(metadata)

    try:
        rule_info = proto_sip.SIPDispatchRuleInfo(
            name=name,
            trunk_ids=trunk_ids,
            rule=proto_sip.SIPDispatchRule(
                dispatch_rule_individual=proto_sip.SIPDispatchRuleIndividual(
                    room_prefix=room_prefix,
                    pin=pin,
                    no_randomness=no_randomness,
                )
            ),
            attributes=attributes,
            inbound_numbers=inbound_numbers or None,
            metadata=json.dumps({"direction": "inbound"}),
            room_config=proto_room.RoomConfiguration(
                agents=[
                    proto_agent_dispatch.RoomAgentDispatch(
                        agent_name="mantra-agent",
                        metadata=json.dumps(agent_metadata),
                        restart_policy=proto_agent_dispatch.JobRestartPolicy.JRP_ON_FAILURE,
                    )
                ]
            ),
        )

        result = await lk_client.sip.create_dispatch_rule(
            proto_sip.CreateSIPDispatchRuleRequest(dispatch_rule=rule_info)
        )

        logger.info(f"Dispatch rule created: {result.sip_dispatch_rule_id} ({name})")
        return JSONResponse({
            "status": "success",
            "sip_dispatch_rule_id": result.sip_dispatch_rule_id,
            "name": result.name,
            "trunk_ids": list(result.trunk_ids),
            "room_prefix": room_prefix,
            "inbound_numbers": inbound_numbers,
        })
    except Exception as e:
        logger.error(f"Failed to create dispatch rule: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/v1/sip/dispatch-rules")
async def list_sip_dispatch_rules():
    """List all SIP dispatch rules."""
    try:
        req = proto_sip.ListSIPDispatchRuleRequest()
        response = await lk_client.sip.list_dispatch_rule(req)

        rule_list = []
        for item in response.items:
            rule_list.append({
                "sip_dispatch_rule_id": item.sip_dispatch_rule_id,
                "name": item.name,
                "trunk_ids": list(item.trunk_ids),
                "inbound_numbers": list(item.inbound_numbers),
                "numbers": list(item.numbers),
                "hide_phone_number": item.hide_phone_number,
                "metadata": item.metadata,
                "attributes": dict(item.attributes),
                "room_preset": item.room_preset,
            })

        return JSONResponse({
            "status": "success",
            "count": len(rule_list),
            "rules": rule_list,
        })
    except Exception as e:
        logger.error(f"Failed to list dispatch rules: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/v1/sip/dispatch-rules/{rule_id}")
async def delete_sip_dispatch_rule(rule_id: str):
    """Delete a SIP dispatch rule by its rule ID."""
    if not rule_id:
        return JSONResponse({"error": "Rule ID is required"}, status_code=400)

    try:
        await lk_client.sip.delete_dispatch_rule(
            proto_sip.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
        )
        logger.info(f"Deleted dispatch rule: {rule_id}")
        return JSONResponse({
            "status": "success",
            "message": f"Dispatch rule {rule_id} deleted",
            "sip_dispatch_rule_id": rule_id,
        })
    except Exception as e:
        logger.error(f"Failed to delete dispatch rule {rule_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ──────────────────────────────────────────────
# SIP INBOUND TRUNK UPDATE
# ──────────────────────────────────────────────

@app.patch("/api/v1/sip/trunks/inbound/{trunk_id}")
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
        await lk_client.sip.update_inbound_trunk_fields(trunk_id, **kwargs)
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

@app.patch("/api/v1/sip/dispatch-rules/{rule_id}")
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
        await lk_client.sip.update_dispatch_rule_fields(rule_id, **kwargs)
        logger.info(f"Dispatch rule updated: {rule_id}")
        return JSONResponse({
            "status": "success",
            "message": f"Dispatch rule {rule_id} updated",
            "sip_dispatch_rule_id": rule_id,
        })
    except Exception as e:
        logger.error(f"Failed to update dispatch rule {rule_id}: {e}\n{traceback.format_exc()}")
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
    uvicorn.run("mantra.ui_server:app", host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
