import os
import sys
import logging
import json
import time
import hashlib
import traceback
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import jwt
import aiohttp
import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, HTTPException, File, UploadFile
from mantra.email_alerts import send_crash_email
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

# ── Authentication ───────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be set")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
ADMIN_USERNAME_HASH = os.getenv("ADMIN_USERNAME_HASH", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")


async def get_db_connection():
    """Create a PostgreSQL connection for dashboard queries."""
    return await asyncpg.connect(
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        database=os.getenv("POSTGRES_DB"),
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
    )

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

@app.exception_handler(Exception)
async def global_crash_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error in UI server: {exc}", exc_info=True)
    
    context_data = {
        "Request URL": str(request.url),
        "HTTP Method" : request.method,
        "User-Agent": request.headers.get("User-Agent"),
        "Client IP": request.client.host if request.client else None,
        
    }

    await send_crash_email(
        service_name="Mantra UI Server",
        error=exc,
        context_data=context_data
    )

    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error, An Automated alert has been dispatched. The technical team is working on resolving this issue."}
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

# Mount static files (with HTML files as default)
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")

@app.get("/")
async def index():
    """Serve the login page."""
    return FileResponse(os.path.join(STATIC_DIR, "login.html"))

# ── Authentication ───────────────────────────────────────────────────────

@app.post("/api/v1/auth/login")
async def login(request: Request):
    """Authenticate with username/password, return JWT."""
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    username_hash = hashlib.sha256(username.encode()).hexdigest()
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    if not ADMIN_USERNAME_HASH or not ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="Auth not configured")

    if username_hash != ADMIN_USERNAME_HASH or password_hash != ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    expiry = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    token = jwt.encode(
        {"sub": username, "exp": expiry, "iat": datetime.now(timezone.utc)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return {"token": token, "expires_in": JWT_EXPIRY_HOURS * 3600, "username": username}


def require_auth(request: Request):
    """Dependency to protect routes via JWT Bearer token."""
    auth = request.headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
    else:
        token = request.query_params.get("token")
        
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        request.state.user = payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@app.get("/dashboard")
async def dashboard_page():
    """Serve the dashboard page."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/console")
async def console_page():
    """Serve the test console."""
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
    logger.info(f"Provider detected: {provider}")

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

    # Trigger SIP outbound call in background to prevent webhook timeouts
    async def trigger_sip():
        try:
            sip_number = payload.get("call_from")
            if sip_number and not sip_number.startswith("+"):
                sip_number = f"+{sip_number}"
                
            sip_client = plivo_client if provider == "plivo" and plivo_client else lk_client
            proxy_msg = "proxied Plivo client" if sip_client == plivo_client else "direct LiveKit client"
            logger.info(f"Step 2: Initiating SIP call to {phone_number} via trunk {trunk_id} using {proxy_msg}" + (f" (Caller ID: {sip_number})" if sip_number else ""))

            sip_part = await sip_client.sip.create_sip_participant(
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
            logger.error(f"SIP Call trigger failed for {room_name}: {e}\n{traceback.format_exc()}")
            
            # Store exact SIP failure reason in Redis for the agent to read
            if redis_client:
                err_str = str(e).lower()
                if any(token in err_str for token in ("408", "timeout", "no answer")):
                    status_guess = "No Answer"
                elif any(token in err_str for token in ("486", "busy", "603", "decline", "rejected")):
                    status_guess = "Busy"
                else:
                    status_guess = "Incomplete"
                try:
                    await redis_client.set(f"sip_error_status:{call_id}", status_guess, ex=300)
                except Exception as re:
                    logger.error(f"Failed to save SIP error to Redis: {re}")

            # Delete the room to signal the agent to terminate immediately
            try:
                await lk_client.room.delete_room(api.DeleteRoomRequest(room=room_name))
                logger.info(f"Deleted room {room_name} due to SIP failure")
            except Exception as cleanup_err:
                logger.error(f"Failed to cleanup room after SIP failure: {cleanup_err}")

    # Fire and forget the SIP task
    import asyncio
    asyncio.create_task(trigger_sip())

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

# ── Dashboard API (authenticated) ────────────────────────────────────────

@app.get("/api/v1/dashboard/stream")
async def dashboard_stream(request: Request):
    """SSE endpoint with real-time queue status + active call details."""
    # require_auth(request)

    async def event_generator():
        if not redis_client:
            yield "data: {\"error\": \"Redis not connected\"}\n\n"
            return

        MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", os.getenv("CARTESIA_MAX_CONCURRENCY", "5")))

        while True:
            try:
                pending_count = await redis_client.zcard("queue:pending")
                active_calls_map = await redis_client.hgetall("calls:active")
                active_count = len(active_calls_map)

                active_details = []
                for call_id, room_name in active_calls_map.items():
                    status = await redis_client.get(f"calls:status:{call_id}")
                    active_details.append({
                        "call_id": call_id,
                        "room_name": room_name,
                        "status": status or "unknown",
                    })

                data = json.dumps({
                    "pending_calls": pending_count,
                    "active_calls": active_count,
                    "max_concurrency": MAX_CONCURRENCY,
                    "active_call_details": active_details,
                    "timestamp": time.time(),
                })
                yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"Dashboard SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/v1/dashboard/metrics")
async def dashboard_metrics(request: Request):
    """Today's call metrics from PostgreSQL."""
    # require_auth(request)

    try:
        conn = await get_db_connection()
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*)::int AS total_calls,
                    COUNT(*) FILTER (WHERE status = 'Completed')::int AS completed_calls,
                    COUNT(*) FILTER (WHERE status = 'Busy')::int AS busy_calls,
                    COUNT(*) FILTER (WHERE status = 'No Answer')::int AS no_answer_calls,
                    COUNT(*) FILTER (WHERE status = 'Error')::int AS error_calls,
                    COUNT(*) FILTER (WHERE status = 'Incomplete')::int AS incomplete_calls,
                    ROUND(
                        AVG(
                            CAST(NULLIF(call_log::json ->> 'call_duration_seconds', '') AS integer)
                        ) FILTER (
                            WHERE call_log::json ->> 'call_duration_seconds' ~ '^\d+$'
                        )
                    )::int AS avg_duration_seconds
                FROM call_logs
                WHERE created_at >= CURRENT_DATE
            """)
        finally:
            await conn.close()

        metrics = dict(row) if row else {
            "total_calls": 0, "completed_calls": 0, "busy_calls": 0,
            "no_answer_calls": 0, "error_calls": 0, "incomplete_calls": 0,
            "avg_duration_seconds": 0,
        }

        answer_rate = round(
            metrics["completed_calls"] / metrics["total_calls"] * 100, 1
        ) if metrics["total_calls"] > 0 else 0

        return {
            **metrics,
            "answer_rate": answer_rate,
        }
    except Exception as e:
        logger.error(f"Dashboard metrics error: {e}")
        return {"error": str(e)}


@app.get("/api/v1/dashboard/calls")
async def dashboard_calls(request: Request, limit: int = 20, offset: int = 0):
    """Paginated call history from PostgreSQL."""
    # require_auth(request)

    try:
        conn = await get_db_connection()
        try:
            rows = await conn.fetch(
                """
                SELECT call_id, status, recording_url, created_at,
                       call_log::json AS call_log
                FROM call_logs
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset
            )

            count_row = await conn.fetchrow("SELECT COUNT(*)::int AS total FROM call_logs")
            total = count_row["total"] if count_row else 0
        finally:
            await conn.close()

        calls = []
        for row in rows:
            cl = row["call_log"] if isinstance(row["call_log"], dict) else {}
            calls.append({
                "call_id": row["call_id"],
                "status": row["status"],
                "recording_url": row["recording_url"] or "",
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "client_name": cl.get("client_name") or cl.get("client_id") or "",
                "client_phone": cl.get("client_phone") or "",
                "duration": cl.get("call_duration_seconds"),
                "summary": cl.get("ai_summary") or "",
                "purpose": (cl.get("prompt") or "")[:120],
            })

        return {"calls": calls, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"Dashboard calls error: {e}")
        return {"error": str(e), "calls": [], "total": 0}


@app.get("/api/v1/dashboard/active-calls")
async def dashboard_active_calls(request: Request):
    """Current active calls from Redis."""
    # require_auth(request)

    if not redis_client:
        return {"active_calls": [], "error": "Redis not connected"}

    try:
        active_map = await redis_client.hgetall("calls:active")
        calls = []
        for call_id, room_name in active_map.items():
            status = await redis_client.get(f"calls:status:{call_id}")
            calls.append({
                "call_id": call_id,
                "room_name": room_name,
                "status": status or "unknown",
            })
        return {"active_calls": calls}
    except Exception as e:
        logger.error(f"Active calls error: {e}")
        return {"active_calls": [], "error": str(e)}


# ── Knowledge Base Ingestion Endpoints ────────────────────────────────

@app.post("/api/v1/knowledge/upload")
async def kb_upload(request: Request, kb_id: str, file: UploadFile = File(...)):
    """Upload a file (.pdf, .txt, .md) and index it into the specified KB."""
    # require_auth(request)
    
    if not file.filename:
        return JSONResponse({"error": "No filename provided"}, status_code=400)
    
    ext = file.filename.lower().split('.')[-1]
    if ext not in ('pdf', 'txt', 'md'):
        return JSONResponse({"error": f"Unsupported file type: {ext}"}, status_code=400)
    
    file_bytes = await file.read()
    
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file
        
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_file(kb, kb_id, file_bytes, file.filename)
        await kb.close()
        
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB upload failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/knowledge/text")
async def kb_text(request: Request):
    """Ingest a raw text block into the specified KB."""
    # require_auth(request)
    
    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        content = body.get("content")
        title = body.get("title")
        
        if not kb_id or not content:
            return JSONResponse({"error": "kb_id and content are required"}, status_code=400)
        
        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_text
        
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_text(kb, kb_id, content, title=title, source_type='text')
        await kb.close()
        
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB text ingest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/knowledge/url")
async def kb_url(request: Request):
    """Fetch a URL, extract text, and index it into the specified KB."""
    # require_auth(request)
    
    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        url = body.get("url")
        
        if not kb_id or not url:
            return JSONResponse({"error": "kb_id and url are required"}, status_code=400)
        
        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_url
        
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_url(kb, kb_id, url)
        await kb.close()
        
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB URL ingest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/v1/knowledge/{page_id}")
async def kb_delete_page(request: Request, page_id: str):
    """Delete a single page from the KB."""
    # require_auth(request)
    
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase
        
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        success = await kb.delete_page(page_id)
        await kb.close()
        
        if success:
            return {"status": "success", "deleted": page_id}
        else:
            return JSONResponse({"error": "Page not found"}, status_code=404)
    except Exception as e:
        logger.error(f"KB delete failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/v1/knowledge/by-kb/{kb_id}")
async def kb_delete_by_kb(request: Request, kb_id: str):
    """Delete all pages for a KB."""
    # require_auth(request)
    
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase
        
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        count = await kb.delete_by_kb(kb_id)
        await kb.close()
        
        return {"status": "success", "deleted_count": count, "kb_id": kb_id}
    except Exception as e:
        logger.error(f"KB delete by KB failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


def main():
    import uvicorn
    port = int(os.getenv("PORT", "8081"))
    logger.info(f"UI Server starting on http://0.0.0.0:{port}")
    try:
        uvicorn.run("mantra.ui_server:app", host="0.0.0.0", port=port, access_log=False)
    except Exception as e:
        logger.error(f"Failed to run UI server: {e}", exc_info=True)
        try:
            import asyncio
            asyncio.run(send_crash_email(
                service_name="Mantra UI Server (Core/Startup)", 
                error=e, 
                context_data={"Status": "Crashloop / Process Death", "PID": os.getpid()}
            ))
        except Exception as email_err:
            logger.error(f"Failed to dispatch core crash email: {email_err}")
        raise

if __name__ == "__main__":
    main()
