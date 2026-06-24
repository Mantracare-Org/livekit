import os
import sys
import logging
import json
import time
import traceback
from contextlib import asynccontextmanager

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
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
redis_client: redis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_client, plivo_client, plivo_session, redis_client
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

    # Setup Redis
    redis_url = os.getenv("REDIS_URL")
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")

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
                participant_name="SIP Caller",
                play_ringtone=False,
                wait_until_answered=True
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
                participant_name="Mantra Voice",
                play_ringtone=False,
                wait_until_answered=True
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

# TODO: This should be removed in production
@app.get("/api/v1/queue/status/stream")
async def stream_queue_status():
    """Server-Sent Events endpoint for live Redis queue updates."""
    async def event_generator():
        if not redis_client:
            yield "data: {\"error\": \"Redis not connected\"}\n\n"
            return
            
        while True:
            try:
                pending_count = await redis_client.zcard("queue:pending")
                active_count = await redis_client.hlen("calls:active")
                
                data = json.dumps({
                    "pending_calls": pending_count,
                    "active_calls": active_count,
                    "timestamp": time.time()
                })
                yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"SSE stream error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
            await asyncio.sleep(1) # Send update every second

    return StreamingResponse(event_generator(), media_type="text/event-stream")

def main():
    import uvicorn
    port = int(os.getenv("PORT", "8081"))
    logger.info(f"UI Server starting on http://0.0.0.0:{port}")
    uvicorn.run("mantra.ui_server:app", host="0.0.0.0", port=port, access_log=False)

if __name__ == "__main__":
    main()
