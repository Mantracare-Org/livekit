import os
import sys
import logging
import json
import time
import uuid
import hashlib
import traceback
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import jwt
import httpx
import aiohttp
import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
import hmac
import base64
from urllib.parse import urlencode, quote_plus
from xml.sax.saxutils import escape
from fastapi import HTTPException, File, UploadFile, Form
from prometheus_fastapi_instrumentator import Instrumentator
from mantra.email_alerts import send_crash_email
from mantra.utils import save_call_log_to_db, save_call_event, report_telemetry, send_to_backend
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from livekit import api
from colorama import Fore, Style, init as colorama_init
from livekit.protocol import sip as proto_sip
from dotenv import load_dotenv

colorama_init(autoreset=True)


# Load environment variables from .env.local
load_dotenv(".env.local")
AGENT_NAME = os.getenv("AGENT_NAME", "mantra-agent")

logger = logging.getLogger("mantra.ui_server")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s INFO %(name)s: %(message)s"))
if not logger.handlers:
    logger.addHandler(_handler)
logger.propagate = True

# Persistent LiveKit API clients
lk_client: api.LiveKitAPI = (
    None  # Direct — used for Twilio, Zadarma, and general operations
)
plivo_client: api.LiveKitAPI = None  # Proxied — used for Plivo (India routing)
plivo_session: aiohttp.ClientSession = (
    None  # Owned session for plivo_client; closed manually on shutdown
)
voicelink_client: api.LiveKitAPI = None  # Proxied — used for VoiceLink
voicelink_session: aiohttp.ClientSession = (
    None  # Owned session for voicelink_client; closed manually on shutdown
)
redis_client: redis.Redis = None
http_client: httpx.AsyncClient = None      # Persistent client for health checks

# ── Per-trunk concurrency limits (derived from provider defaults) ──────
PROVIDER_DEFAULT_CONCURRENCY = {
    "plivo": int(os.getenv("PLIVO_MAX_CONCURRENCY", "2")),
    "zadarma": int(os.getenv("ZADARMA_MAX_CONCURRENCY", "3")),
    "voice_link": int(os.getenv("VOICELINK_MAX_CONCURRENCY", "5")),
    "twilio": int(os.getenv("TWILIO_MAX_CONCURRENCY", "3")),
}
MAX_CALL_CONCURRENCY = int(
    os.getenv("MAX_CONCURRENCY", os.getenv("CARTESIA_MAX_CONCURRENCY", "5"))
)

# ── Per-trunk capacity helpers ─────────────────────────────────────────

_TRUNK_TO_PROVIDER: dict[str, str] = {}

async def _resolve_trunk_limit(trunk_id: str) -> tuple[str | None, int]:
    provider = _TRUNK_TO_PROVIDER.get(trunk_id)
    if provider is None:
        provider = await _get_provider_from_trunk(trunk_id)
        if provider:
            _TRUNK_TO_PROVIDER[trunk_id] = provider
    if provider and provider in PROVIDER_DEFAULT_CONCURRENCY:
        return provider, PROVIDER_DEFAULT_CONCURRENCY[provider]
    return provider, 1


async def _active_call_rooms() -> list[str]:
    if not lk_client:
        raise RuntimeError("LiveKit client not initialised")
    resp = await lk_client.room.list_rooms(api.ListRoomsRequest())
    return [r.name for r in resp.rooms if (r.name or "").startswith("call_")]


async def _extract_trunk_ids(rooms: list[str]) -> list[str]:
    trunk_ids: list[str] = []
    for name in rooms:
        if not name or not name.startswith("call_"):
            continue
        parts = name[5:].rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            trunk_ids.append(parts[0])
    return trunk_ids


async def _active_per_trunk(rooms: list[str], trunk_id: str) -> int:
    prefix = f"call_{trunk_id}_"
    return sum(1 for r in rooms if r and r.startswith(prefix))


async def _trunk_at_capacity(trunk_id: str) -> tuple[bool, int]:
    provider, limit = await _resolve_trunk_limit(trunk_id)
    if provider is None:
        return False, 0
    rooms = await _active_call_rooms()
    active = await _active_per_trunk(rooms, trunk_id)
    return active >= limit, active


async def _log_blocked_call(
    call_id: str, provider: str | None, active_count: int,
    trunk_id: str = "", phone: str = "", caller_number: str = "",
):
    limit = PROVIDER_DEFAULT_CONCURRENCY.get(provider or "", "?")
    call_log = json.dumps({
        "call_id": call_id,
        "provider": provider,
        "blocked": True,
        "reason": "trunk_at_concurrency_limit",
        "active_calls": active_count,
        "max_concurrency": limit,
        "trunk_id": trunk_id,
        "phone": phone,
        "requested_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    await save_call_log_to_db(
        call_id, call_log, "Busy", "",
        caller_number=caller_number,
        called_number=phone,
        trunk_id=trunk_id,
    )


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
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return await asyncpg.connect(dsn=database_url, timeout=5.0)
    else:
        return await asyncpg.connect(
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            database=os.getenv("POSTGRES_DB"),
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
            timeout=5.0,
        )


async def process_pending_webhooks():
    """Background worker to reliably deliver webhooks offloaded by the agent."""
    import redis.asyncio as redis
    from redis.exceptions import (
        TimeoutError as RedisTimeoutError,
        ConnectionError as RedisConnectionError,
        ReadOnlyError,
        ResponseError,
    )

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("No REDIS_URL configured; webhook worker will not start.")
        return

    logger.info("Starting background webhook worker...")
    client = None

    while True:
        try:
            if client is None:
                client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_timeout=15,
                    socket_connect_timeout=5,
                    health_check_interval=15,
                    retry_on_timeout=True,
                )

            # blpop blocks for up to 5 seconds waiting for a payload
            result = await client.blpop("mantra:pending_webhooks", timeout=5)
            if result:
                _, payload_bytes = result
                try:
                    payload = json.loads(payload_bytes)
                    call_id = payload.get("data", {}).get("call_id", "unknown")
                    logger.info(f"Dequeued webhook for call {call_id}. Delivering to backend...")
                    delivered = await send_to_backend(payload)
                    if delivered:
                        logger.info(f"Successfully delivered offloaded webhook for call {call_id}.")
                    else:
                        logger.warning(f"Webhook delivery for call {call_id} failed, but claim was processed.")
                except json.JSONDecodeError:
                    logger.error("Failed to decode webhook payload from Redis queue.")
                except Exception as ex:
                    logger.error(f"Error processing queued webhook: {ex}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Webhook worker cancelled. Shutting down.")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
            break
        except (RedisTimeoutError, TimeoutError):
            # Normal timeout when there are no new messages and blpop returns empty
            continue
        except (ReadOnlyError, RedisConnectionError) as e:
            logger.warning(f"Redis connection/replica state error in webhook worker: {e}. Reconnecting in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)
        except ResponseError as e:
            if "read only" in str(e).lower() or "unblocked" in str(e).lower():
                logger.warning(f"Redis failover detected in webhook worker ({e}). Reconnecting in 5s...")
            else:
                logger.error(f"Redis response error in webhook worker: {e}. Retrying in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Redis error in webhook worker: {e}. Retrying in 5s...")
            if client:
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = None
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_client, plivo_client, plivo_session, voicelink_client, voicelink_session, redis_client, http_client
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
            logger.info(
                "Creating Plivo LiveKit client without proxy (PLIVO_PROXY not set)"
            )
        plivo_session = aiohttp.ClientSession(proxy=plivo_proxy)
        plivo_client = api.LiveKitAPI(
            url=api_url, api_key=api_key, api_secret=api_secret, session=plivo_session
        )

        voicelink_proxy = os.getenv("VOICELINK_PROXY") or plivo_proxy
        if voicelink_proxy:
            logger.info(f"Creating VoiceLink LiveKit client with proxy: {voicelink_proxy}")
        else:
            logger.info("Creating VoiceLink LiveKit client without proxy")
        voicelink_session = aiohttp.ClientSession(proxy=voicelink_proxy)
        voicelink_client = api.LiveKitAPI(
            url=api_url, api_key=api_key, api_secret=api_secret, session=voicelink_session
        )

    # Setup Redis
    redis_url = os.getenv("REDIS_URL")
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")

    # Create a single, persistent httpx client for all health checks
    http_client = httpx.AsyncClient(timeout=1.5)

    # ── Startup healthcheck ─────────────────────────────────────────
    logger.info("Running startup healthcheck on all dependencies...")
    if await _run_health_checks():
        logger.info("Startup healthcheck: ALL SERVICES HEALTHY")
    else:
        logger.warning("Startup healthcheck: one or more services down — refusing dispatch")
    # ────────────────────────────────────────────────────────────────

    # ── Startup zombie-room cleanup ─────────────────────────────────
    if lk_client:
        try:
            response = await lk_client.room.list_rooms(api.ListRoomsRequest())
            zombie_count = 0
            for room in response.rooms:
                if not room.name or not room.name.startswith("call_"):
                    continue
                if room.num_participants == 0:
                    logger.warning(
                        f"Startup zombie room detected: {room.name} (0 participants). Deleting."
                    )
                    try:
                        await lk_client.room.delete_room(
                            api.DeleteRoomRequest(room=room.name)
                        )
                        zombie_count += 1
                    except Exception as e:
                        logger.error(f"Failed to delete zombie room {room.name}: {e}")
            if zombie_count > 0:
                logger.info(f"Startup zombie cleanup: deleted {zombie_count} empty rooms")
        except Exception as e:
            logger.error(f"Startup zombie room cleanup failed: {e}")
    # ────────────────────────────────────────────────────────────────

    # Start background webhook worker
    webhook_task = asyncio.create_task(process_pending_webhooks())

    yield
    
    webhook_task.cancel()
    try:
        await webhook_task
    except asyncio.CancelledError:
        pass

    for client in [lk_client, plivo_client, voicelink_client]:
        if client:
            await client.aclose()
    if plivo_session:
        await plivo_session.close()
    if voicelink_session:
        await voicelink_session.close()
    if http_client:
        await http_client.aclose()


app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app, include_in_schema=False, should_gzip=True)

SCANNER_PATHS = (
    "/.well-known/",
    "/favicon",
    "/wp-",
    "/blog/",
    "/web/",
    "/wordpress/",
    "/website/",
    "/wp/",
    "/news/",
    "/2018/",
    "/2019/",
    "/shop/",
    "/wp1/",
    "/test/",
    "/media/",
    "/wp2/",
    "/site/",
    "/cms/",
    "/sito/",
)


@app.exception_handler(Exception)
async def global_crash_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error in UI server: {exc}", exc_info=True)

    context_data = {
        "Request URL": str(request.url),
        "HTTP Method": request.method,
        "User-Agent": request.headers.get("User-Agent"),
        "Client IP": request.client.host if request.client else None,
    }

    await send_crash_email(
        service_name="Mantra UI Server", error=exc, context_data=context_data
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error, An Automated alert has been dispatched. The technical team is working on resolving this issue."
        },
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
            logger.debug(
                f"Scanner: {client_host} {request.method} {path} {response.status_code}"
            )
        else:
            logger.info(
                f"{client_host} {request.method} {path} {response.status_code} in {duration * 1000:.0f}ms"
            )

        return response
    except Exception as e:
        duration = time.time() - start
        logger.error(
            f"{client_host} {request.method} {path} ERROR in {duration * 1000:.0f}ms: {e}"
        )
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


@app.post("/v1/auth/login")
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

# ── Paths that require a clean bill of health before processing ─────────
_DISPATCH_PATHS = frozenset({
    "/dispatch-test",
    "/v1/webhooks/telephony",
    "/v1/sip/trunks/outbound",
    "/v1/sip/trunks/outbound/zadarma",
    "/v1/sip/trunks/outbound/twilio",
    "/v1/sip/trunks/outbound/plivo",
})


async def _run_dependency_checks() -> tuple[bool, dict[str, bool | str]]:
    """Run infrastructure dependency checks only (no capacity). Used by the coarse gate."""
    if os.getenv("BYPASS_HEALTH_CHECKS") == "1":
        logger.warning("BYPASS_HEALTH_CHECKS is active. Skipping all service health checks.")
        return True, {}

    checks: dict[str, bool | str] = {}

    async def _check(domain: str, coro, timeout: float = 1.0):
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            checks[domain] = True
        except Exception as e:
            checks[domain] = repr(e)

    async def _check_stt():
        key = os.getenv("DEEPGRAM_API_KEY")
        if not key:
            checks["stt_deepgram"] = "DEEPGRAM_API_KEY not set"
            return
        try:
            r = await http_client.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {key}"},
            )
            checks["stt_deepgram"] = r.is_success
        except Exception as e:
            checks["stt_deepgram"] = str(e)

    async def _check_mantraassist_backend():
        url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
        if not url:
            checks["mantraassist_backend"] = "MANTRAASSIST_BACKEND_URL not set"
            return
        try:
            r = await http_client.get(f"{url}/v1/health")
            if r.is_success:
                data = r.json()
                if data.get("success") is True:
                    checks["mantraassist_backend"] = True
                else:
                    checks["mantraassist_backend"] = f"Unexpected response body: {data}"
            else:
                checks["mantraassist_backend"] = f"HTTP status {r.status_code}"
        except Exception as e:
            checks["mantraassist_backend"] = str(e)

    async def _check_s3():
        bucket = os.getenv("AWS_S3_BUCKET_NAME")
        if not bucket:
            checks["s3"] = "AWS_S3_BUCKET_NAME not set"
            return
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _check_s3_bucket, bucket),
                timeout=1.0,
            )
            checks["s3"] = True
        except Exception as e:
            checks["s3"] = str(e)

    async def _check_postgres():
        conn = None
        try:
            conn = await asyncio.wait_for(get_db_connection(), timeout=1.0)
            checks["postgres"] = True
        except Exception as e:
            checks["postgres"] = repr(e)
        finally:
            if conn:
                try:
                    await conn.close()
                except:
                    pass

    async def _check_redis():
        if not redis_client:
            checks["redis"] = "Redis client not initialised"
            return
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
            checks["redis"] = True
        except Exception as e:
            checks["redis"] = str(e)

    async def _check_livekit_primary():
        lk_url = os.getenv("LIVEKIT_URL", "")
        if not lk_url:
            checks["livekit_primary"] = "LIVEKIT_URL not set"
            return
        http_url = lk_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
        try:
            r = await http_client.get(http_url, timeout=2.0)
            if r.status_code < 500:
                checks["livekit_primary"] = True
            else:
                checks["livekit_primary"] = f"Primary LiveKit endpoint HTTP {r.status_code}"
        except Exception as e:
            checks["livekit_primary"] = f"Primary LiveKit endpoint unreachable: {e}"

    await asyncio.gather(
        _check_livekit_primary(),
        _check("livekit", lk_client.room.list_rooms(api.ListRoomsRequest()), timeout=5.0),
        _check_redis(),
        _check_postgres(),
        _check_stt(),
        _check_mantraassist_backend(),
        _check_s3(),
        return_exceptions=True
    )

    all_ok = True
    for service, status in checks.items():
        if status is True:
            logger.info(f"  - Healthcheck OK: {service}")
        else:
            all_ok = False
            logger.warning(f"  - Healthcheck FAILED: {service} -> {status}")

    return all_ok, checks


async def _run_health_checks() -> bool:
    """Run all dependency + capacity checks. Returns False if any check fails."""
    if os.getenv("BYPASS_HEALTH_CHECKS") == "1":
        logger.warning("BYPASS_HEALTH_CHECKS is active. Skipping all service health checks.")
        return True

    dep_ok, checks = await _run_dependency_checks()
    if not dep_ok:
        return False

    rooms = []
    try:
        rooms = await _active_call_rooms()
    except Exception as e:
        checks["capacity_max_concurrency"] = f"error: {e}"
        rooms = []

    total = len(rooms)
    if total >= MAX_CALL_CONCURRENCY:
        checks["capacity_max_concurrency"] = f"BUSY {total}/{MAX_CALL_CONCURRENCY}"
    else:
        checks["capacity_max_concurrency"] = True

    try:
        trunk_ids = await _extract_trunk_ids(rooms)
    except Exception:
        trunk_ids = []
    seen: set[str] = set()
    for trunk_id in trunk_ids:
        if trunk_id in seen:
            continue
        seen.add(trunk_id)
        provider, limit = await _resolve_trunk_limit(trunk_id)
        active = await _active_per_trunk(rooms, trunk_id)
        key = f"trunk_capacity_{trunk_id}"
        if active >= limit:
            checks[key] = f"BUSY {active}/{limit}"
        else:
            checks[key] = True

    all_ok = True
    for service, status in checks.items():
        if status is True:
            logger.info(f"  - Healthcheck OK: {service}")
        else:
            all_ok = False
            logger.warning(f"  - Healthcheck FAILED: {service} -> {status}")

    return all_ok


@app.get("/network")
async def network_page():
    """Serve the network monitoring page."""
    return FileResponse(os.path.join(STATIC_DIR, "network.html"))


@app.get("/redis")
async def redis_page():
    """Serve the Redis Monitoring & Inspector page."""
    return FileResponse(os.path.join(STATIC_DIR, "redis.html"))


@app.get("/kb-chat")
async def kb_chat_page():
    """Serve the Knowledge Base text chat tester."""
    return FileResponse(os.path.join(STATIC_DIR, "kb_chat.html"))

@app.get("/health")
async def health():
    healthy = await _run_health_checks()
    return JSONResponse(
        content={"healthy": healthy}
    )


# @app.get("/health/accept")
# async def health_accept(request: Request):
#     trunk_id = request.query_params.get("sip_trunk_id")
#     if not trunk_id:
#         logger.warning("Health accept denied: missing sip_trunk_id")
#         return JSONResponse(content={"healthy": False})

#     provider = await _get_provider_from_trunk(trunk_id)
#     if not provider or provider not in PROVIDER_MAX_CONCURRENCY:
#         logger.warning(
#             f"Health accept: unknown provider for trunk {trunk_id}, falling back to generic check"
#         )
#         healthy = await _run_health_checks()
#         return JSONResponse(content={"healthy": healthy})

#     busy, active = await _provider_at_capacity(provider)
#     limit = PROVIDER_MAX_CONCURRENCY[provider]
#     if busy:
#         logger.warning(
#             f"Health denied: {provider} at capacity ({active}/{limit}) "
#             f"for trunk {trunk_id}"
#         )
#         return JSONResponse(content={"healthy": False})

#     # Global capacity gate — also applies to health checks
#     try:
#         global_rooms = await _active_call_rooms()
#         if len(global_rooms) >= MAX_CALL_CONCURRENCY:
#             logger.warning(
#                 f"Health denied: global cap full ({len(global_rooms)}/{MAX_CALL_CONCURRENCY}) "
#                 f"for trunk {trunk_id}"
#             )
#             return JSONResponse(content={"healthy": False})
#     except Exception as e:
#         logger.warning(f"Global capacity check unavailable, continuing: {e}")

#     import uuid
#     reservation_id = uuid.uuid4().hex[:12]
#     if redis_client:
#         await redis_client.setex(f"reserve:{provider}:{reservation_id}", 30, trunk_id)
#     active_after = active + (1 if redis_client else 0)
#     logger.info(
#         f"Health accept: {provider} reserved slot ({active_after}/{limit}) "
#         f"trunk={trunk_id} reservation={reservation_id}"
#     )
#     return JSONResponse(content={
#         "healthy": True,
#         "provider": provider,
#         "reservation_id": reservation_id,
#         "active": active_after,
#         "max": limit,
#     })


@app.middleware("http")
async def health_gate_middleware(request: Request, call_next):
    """Per-provider capacity gate + coarse dependency gate. 503 on blocked dispatch."""
    path = request.url.path
    if request.method == "POST" and path in _DISPATCH_PATHS:
        if path == "/v1/webhooks/telephony":
            try:
                body = await request.body()
                payload = json.loads(body) if body else {}
                call_id = str(
                    payload.get("call_id")
                    or payload.get("voice_id")
                    or payload.get("event_id")
                    or int(time.time())
                )
                trunk_id = (
                    payload.get("trunk_id")
                    or payload.get("call_from_id")
                    or os.getenv("SIP_TRUNK_ID")
                )
                if trunk_id:
                    provider, limit = await _resolve_trunk_limit(trunk_id)
                    if provider is not None:
                        busy, active = await _trunk_at_capacity(trunk_id)
                        if busy:
                            logger.warning(
                                f"Trunk gate blocked {trunk_id} ({provider}): {active}/{limit}"
                            )
                            asyncio.create_task(
                                _log_blocked_call(
                                    call_id, provider, active,
                                    trunk_id=trunk_id,
                                    phone=str(payload.get("client_phone", "")),
                                    caller_number=str(payload.get("call_from", "")),
                                )
                            )
                            return Response(status_code=503)
            except Exception as e:
                logger.error(f"trunk capacity gate error, blocking: {e}")
                return Response(status_code=503)

        # ── Global capacity gate (agent pool) ───────────────────
        try:
            rooms = await _active_call_rooms()
            if len(rooms) >= MAX_CALL_CONCURRENCY:
                logger.warning(f"Global capacity gate blocked: {len(rooms)}/{MAX_CALL_CONCURRENCY}")
                return Response(status_code=503)
        except Exception as e:
            logger.warning(f"Global capacity gate unavailable, continuing: {e}")

        ok, _ = await _run_dependency_checks()
        if not ok:
            logger.warning(f"Health gate blocked {request.method} {path}")
            return Response(status_code=503)

    return await call_next(request)


def _check_s3_bucket(bucket: str):
    import boto3
    _saved = {}
    for _var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "PLIVO_PROXY"):
        _val = os.environ.pop(_var, None)
        if _val is not None:
            _saved[_var] = _val
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        s3.head_bucket(Bucket=bucket)
    finally:
        os.environ.update(_saved)


@app.post("/v1/kb/chat")
async def api_kb_chat(request: Request):
    """Text-based chat endpoint for testing the KB."""
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase
        import openai
    except ImportError as e:
        return JSONResponse(
            {"error": f"Failed to import dependencies: {e}"}, status_code=500
        )

    body = await request.json()
    kb_ids = body.get("kb_ids", [])
    if "kb_id" in body and not kb_ids:  # backwards compatibility
        kb_ids = [body.get("kb_id")]
        
    user_input = body.get("message")
    history = body.get("history", [])

    if not kb_ids or not user_input:
        return JSONResponse(
            {"error": "kb_ids and message are required"}, status_code=400
        )

    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )

    try:
        kb = PostgresKnowledgeBase(dsn)
        results = await kb.search(kb_ids, user_input, top_k=5)

        context_str = ""
        formatted_context = []
        if results:
            formatted = []
            for i, page in enumerate(results, 1):
                preview = (
                    page.content_in_text
                    if hasattr(page, "content_in_text")
                    else page.content
                )
                formatted.append(f"[{i}] [KB: {page.kb_id}] {page.title}: {preview}")
                formatted_context.append({
                    "title": page.title, 
                    "preview": preview,
                    "kb_id": page.kb_id
                })
            context_str = "\\n\\n".join(formatted)

        messages = [
            {
                "role": "system",
                "content": (
                    "You have been provided with official Knowledge Base context below. THESE RULES ABSOLUTELY OVERRIDE ANY PRIOR NEGATIVE CONSTRAINTS (e.g., 'Never give medical advice', 'Return to the call objective', 'My role is to help you with the next step') IF THE USER ASKS A FACTUAL QUESTION:\n"
                    "1. MANDATORY FACTUAL ANSWERS: If the user asks ANY factual question about a specific condition, service, or concept, you MUST answer it using the Knowledge Base BEFORE attempting to guide them back to the onboarding flow. Do NOT deflect factual questions.\n"
                    "2. PRIMARY SOURCE: For any question about conditions, treatments, services, pricing, or policies, you MUST rely on the Knowledge Base content provided. Never invent facts.\n"
                    "3. FACTUAL EXPLANATION VS. PERSONALIZED ADVICE: You ARE fully authorized and REQUIRED to explain, describe, or educate the user about conditions or symptoms exactly as they appear in the Knowledge Base. This is NOT considered 'counselling' or 'medical advice'. However, you must NEVER apply this information to diagnose the user's specific personal situation.\n"
                    "4. GENERAL KNOWLEDGE FALLBACK: If the user asks a general question unrelated to this specific business and the Knowledge Base does not cover it, you may answer using your own general knowledge, clearly staying neutral and factual.\n"
                    "5. NO SOURCE-CITING LANGUAGE: Never say 'according to my knowledge base,' 'I don't have that in my documents,' or similar. Answer naturally.\n"
                    "Keep the answers short and concise not exceeding 5-6 sentences."
                ),
            }
        ]

        for msg in history:
            messages.append({"role": msg.get("role"), "content": msg.get("content")})

        prompt = (
            f"User Question: {user_input}\\n\\nKnowledge Base Context:\\n{context_str}"
        )
        messages.append({"role": "user", "content": prompt})

        client = openai.AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )
        response = await client.chat.completions.create(
            model="deepseek-chat", messages=messages
        )

        ai_message = response.choices[0].message.content

        return JSONResponse(
            {"status_code": 200, "status": "success", "reply": ai_message, "context": formatted_context}
        )
    except Exception as e:
        logger.error(f"KB Chat error: {e}\\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/kb/ingest")
async def ingest_kb_data(request: Request):
    """
    Ingest endpoint for MantraAssist KB data.
    Receives either a file or text content, and stores it in PostgreSQL.
    Supports both JSON and Multipart/Form data payloads.
    """
    form_data = {}
    upload_file = None

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            form_data = await request.json()
        except Exception as e:
            logger.warning(f"Failed to parse JSON body in /v1/kb/ingest: {e}")
    else:
        try:
            form = await request.form()
            raw = {}
            for k, v in form.items():
                if isinstance(v, UploadFile):
                    upload_file = v
                    raw[k] = f"UploadFile({v.filename})"
                else:
                    form_data[k] = v
                    raw[k] = v
            print(f"RAW FORM: {raw}")
        except Exception:
            try:
                form_data = await request.json()
            except Exception:
                pass

    org_id = form_data.get("org_id")
    if not upload_file:
        upload_file = form_data.get("file")
    text = form_data.get("text")
    tags_name = form_data.get("tags_name")
    document_id = form_data.get("document_id")
    process_stage_data = form_data.get("process_stage_data")
    process_assignments_raw = form_data.get("process_assignments")
    process_id_raw = form_data.get("process_id")
    stage_id_raw = form_data.get("stage_id")
    stage_ids_raw = form_data.get("stage_ids")

    if not org_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id is required"}, status_code=400)

    from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file, ingest_text

    if not upload_file and not text:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Either file or text must be provided"}, status_code=400)

    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )

    s3_url = None
    if upload_file:
        s3_bucket = os.getenv("AWS_S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")
        s3_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        s3_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        s3_region = os.getenv("AWS_REGION", "us-east-1")

        if s3_bucket and s3_access_key and s3_secret_key:
            try:
                import boto3
                import time
                file_bytes_for_s3 = await upload_file.read()
                await upload_file.seek(0)
                s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=s3_access_key,
                    aws_secret_access_key=s3_secret_key,
                    region_name=s3_region
                )
                s3_key = f"kb/{org_id}/{int(time.time())}_{upload_file.filename}"
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=s3_key,
                    Body=file_bytes_for_s3,
                    ACL="public-read",
                )
                s3_url = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{s3_key}"
                logger.info(f"Uploaded {upload_file.filename} to S3: {s3_url}")
            except Exception as e:
                logger.error(f"S3 upload error: {e}")
                return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to upload to S3: {str(e)}"}, status_code=500)
        else:
            logger.warning("S3 upload skipped — missing AWS_S3_BUCKET_NAME or credentials")

    try:
        def parse_list(val):
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(val, str):
                return [v.strip() for v in val.split(",") if v.strip()]
            return None

        parsed_process_assignments = None
        parsed_process_id = None
        parsed_stage_id = None
        parsed_stage_ids = []
        proc_desc = ""
        stage_desc = ""

        if process_assignments_raw:
            try:
                pa = json.loads(process_assignments_raw) if isinstance(process_assignments_raw, str) else process_assignments_raw
                if isinstance(pa, list) and len(pa) > 0:
                    parsed_process_assignments = pa
                    first_pa = pa[0]
                    if isinstance(first_pa, dict):
                        if first_pa.get("process_id"):
                            parsed_process_id = int(first_pa["process_id"])
                        s_ids = first_pa.get("stage_ids")
                        if isinstance(s_ids, list) and len(s_ids) > 0:
                            parsed_stage_ids = [int(s) for s in s_ids]
                            parsed_stage_id = parsed_stage_ids[0]
            except Exception as e:
                logger.warning(f"Failed to parse process_assignments: {e}")

        if process_stage_data:
            try:
                psd = json.loads(process_stage_data) if isinstance(process_stage_data, str) else process_stage_data
                if isinstance(psd, list) and len(psd) > 0:
                    extracted_assignments = []
                    extracted_sids_all = []
                    proc_descs = []
                    stage_descs = []

                    for proc in psd:
                        if isinstance(proc, dict):
                            pid = proc.get("id") or proc.get("process_id")
                            p_name = proc.get("name") or proc.get("description") or ""
                            if p_name:
                                proc_descs.append(p_name)

                            stages = proc.get("stages") or proc.get("stageDetails") or []
                            proc_sids = []
                            if isinstance(stages, list):
                                for stg in stages:
                                    if isinstance(stg, dict):
                                        sid = stg.get("id") or stg.get("stage_id")
                                        s_desc = stg.get("desc") or stg.get("description") or stg.get("name") or ""
                                        if s_desc:
                                            stage_descs.append(s_desc)
                                        if sid is not None:
                                            try:
                                                sid_int = int(sid)
                                                proc_sids.append(sid_int)
                                                extracted_sids_all.append(sid_int)
                                            except (TypeError, ValueError):
                                                pass

                            if pid is not None:
                                try:
                                    pid_int = int(pid)
                                    if parsed_process_id is None:
                                        parsed_process_id = pid_int
                                    extracted_assignments.append({
                                        "process_id": pid_int,
                                        "stage_ids": proc_sids
                                    })
                                except (TypeError, ValueError):
                                    pass

                    if extracted_assignments and not parsed_process_assignments:
                        parsed_process_assignments = extracted_assignments

                    if extracted_sids_all:
                        if not parsed_stage_ids:
                            parsed_stage_ids = extracted_sids_all
                        if parsed_stage_id is None:
                            parsed_stage_id = extracted_sids_all[0]

                    if proc_descs:
                        proc_desc = ", ".join(proc_descs)
                    if stage_descs:
                        stage_desc = ", ".join(stage_descs)
            except Exception as e:
                logger.warning(f"Failed to parse process_stage_data: {e}")

        if process_id_raw and parsed_process_id is None:
            try:
                parsed_process_id = int(process_id_raw)
            except (TypeError, ValueError):
                pass

        if stage_id_raw and parsed_stage_id is None:
            try:
                parsed_stage_id = int(stage_id_raw)
            except (TypeError, ValueError):
                pass

        if stage_ids_raw and not parsed_stage_ids:
            try:
                s_ids = json.loads(stage_ids_raw) if isinstance(stage_ids_raw, str) else stage_ids_raw
                if isinstance(s_ids, list):
                    parsed_stage_ids = [int(s) for s in s_ids]
                    if parsed_stage_ids and parsed_stage_id is None:
                        parsed_stage_id = parsed_stage_ids[0]
            except Exception:
                pass

        if parsed_process_id and parsed_stage_ids and not parsed_process_assignments:
            parsed_process_assignments = [
                {
                    "process_id": parsed_process_id,
                    "stage_ids": parsed_stage_ids
                }
            ]

        page_meta = {
            "tags_name": parse_list(tags_name),
            "s3_url": s3_url,
            "document_id": document_id,
            "process_id": parsed_process_id,
            "stage_id": parsed_stage_id,
            "stage_ids": parsed_stage_ids,
            "process_assignments": parsed_process_assignments,
        }
        if process_stage_data:
            try:
                page_meta["process_stage_data"] = json.loads(process_stage_data)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse process_stage_data as JSON: {process_stage_data}")
                page_meta["process_stage_data"] = process_stage_data
        page_meta = {k: v for k, v in page_meta.items() if v is not None}

        kb = PostgresKnowledgeBase(dsn)

        # Resolve the document_id for collection naming
        doc_id = document_id or (upload_file.filename if upload_file else "text_ingestion")

        # If document_id provided, delete old chunks across all KBs (handles backward compat cleanly)
        if document_id:
            try:
                deleted_count = await kb.delete_by_document(org_id, document_id)
                logger.info(f"Deleted {deleted_count} old chunks for document {document_id}")
            except Exception as e:
                logger.error(f"Failed to delete old chunks for document {document_id}: {e}")

        # Get or create a KB collection for this (org_id, document_id)
        collection = await kb.get_or_create_collection(
            org_id, doc_id, name=upload_file.filename if upload_file else doc_id,
            process_description=proc_desc,
            stage_description=stage_desc,
            process_id=parsed_process_id,
            stage_id=parsed_stage_id,
            stage_ids=parsed_stage_ids if parsed_stage_ids else None,
            process_assignments=parsed_process_assignments,
        )
        collection_id = str(collection["id"])
        logger.info(f"Using KB collection {collection_id} for org {org_id} document {doc_id}")

        if upload_file:
            file_bytes = await upload_file.read()
            await ingest_file(
                kb=kb,
                kb_id=collection_id,
                file_bytes=file_bytes,
                filename=upload_file.filename,
                page_meta=page_meta
            )
        elif text:
            await ingest_text(
                kb=kb,
                kb_id=collection_id,
                content_in_text=text,
                title=document_id or "Text Ingestion",
                source_type="text",
                page_meta=page_meta
            )

        await kb.close()

        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": "Data successfully ingested.",
            "document_id": document_id,
            "org_id": org_id,
            "s3_url": s3_url
        })
    except ValueError as e:
        return JSONResponse({"status_code": 400, "status": "error", "error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        logger.error(f"KB ingest error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to ingest to DB: {str(e)}"}, status_code=500)


@app.post("/v1/kb/backfill-embeddings")
async def backfill_kb_embeddings(request: Request):
    """
    Backfill missing `embedding` values on kb_pages rows (pgvector semantic search).

    Runs the same logic as tools/backfill_embeddings.py but as an HTTP endpoint,
    so it works on Docker-only deployments where a CLI cannot be executed.

    Body (JSON, all optional):
      - kb_id:       only backfill rows for this collection/org (default: all)
      - batch_size:  rows per Gemini batch (default: 100)
      - limit:       max rows to backfill (default: no limit)
      - dry_run:     if true, only report how many rows need embeddings

    Requires: kb_pages.embedding column (migration 006) + GOOGLE_API_KEY in .env.local
    """
    from mantra.knowledge_base import PostgresKnowledgeBase

    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty/non-JSON body defaults to {}
        pass

    kb_id = body.get("kb_id")
    batch_size = int(body.get("batch_size", 100))
    limit = body.get("limit")
    limit = int(limit) if limit is not None else None
    dry_run = bool(body.get("dry_run", False))

    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{quote_plus(os.getenv('POSTGRES_PASSWORD') or '')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )

    kb = PostgresKnowledgeBase(dsn)
    try:
        summary = await kb.backfill_embeddings(
            kb_id=kb_id,
            batch_size=batch_size,
            limit=limit,
            dry_run=dry_run,
        )
    except RuntimeError as e:
        return JSONResponse({"status_code": 400, "status": "error", "error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"KB backfill error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Backfill failed: {str(e)}"}, status_code=500)
    finally:
        await kb.close()

    return JSONResponse({"status_code": 200, "status": "success", "summary": summary})


@app.delete("/v1/kb/document")
async def delete_kb_document(
    org_id: str = Form(None),
    document_id: str = Form(None)
):
    """Delete all KB chunks associated with a specific document_id."""
    if not org_id or not document_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id and document_id are required"}, status_code=400)

    from mantra.knowledge_base import PostgresKnowledgeBase
    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )

    try:
        kb = PostgresKnowledgeBase(dsn)
        deleted_count = await kb.delete_by_document(org_id, document_id)
        await kb.close()
        
        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": "Document successfully deleted.",
            "deleted_chunks": deleted_count,
            "document_id": document_id,
            "org_id": org_id
        })
    except Exception as e:
        import traceback
        logger.error(f"KB document delete error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to delete document: {str(e)}"}, status_code=500)

@app.post("/dispatch-test")
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
        dispatch = await lk_client.agent_dispatch.create_dispatch(
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


@app.post("/v1/test/inbound-call")
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
        dispatch = await lk_client.agent_dispatch.create_dispatch(
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



@app.post("/v1/sip/trunks/inbound")
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

@app.post("/v1/sip/trunks/inbound/voicelink")
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
        trunk = await lk_client.sip.create_inbound_trunk(trunk_request)
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
        rule = await lk_client.sip.create_sip_dispatch_rule(req)
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


@app.get("/v1/sip/trunks/inbound")
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


@app.delete("/v1/sip/trunks/inbound/{trunk_id}")
async def delete_sip_inbound_trunk(trunk_id: str):
    """
    Delete a SIP Inbound Trunk by its ID.
    """
    if not trunk_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Trunk ID is required"}, status_code=400)
    
    try:
        await lk_client.sip.delete_trunk(
            api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
        )
        logger.info(f"Successfully deleted SIP Inbound Trunk: {trunk_id}")
        
        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": f"SIP inbound trunk {trunk_id} deleted successfully"
        })
    except Exception as e:
        logger.error(f"Failed to delete SIP inbound trunk {trunk_id}: {e}")
        return JSONResponse({"status_code": 500, "status": "error", "error": str(e)}, status_code=500)


@app.post("/v1/sip/dispatch-rules")
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


@app.get("/v1/sip/dispatch-rules")
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


@app.delete("/v1/sip/dispatch-rules/{rule_id}")
async def delete_dispatch_rule(rule_id: str):
    """
    Delete a SIP Dispatch Rule by its ID.
    """
    if not rule_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Rule ID is required"}, status_code=400)
    
    try:
        await lk_client.sip.delete_dispatch_rule(
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



def _normalize_phone_number(number: str) -> str:
    return str(number or "").replace(" ", "").replace("+", "")


async def _resolve_plivo_sip_trunk_id(to_number: str) -> str | None:
    """Resolve the LiveKit inbound SIP trunk for a Plivo dial target."""
    clean_to = _normalize_phone_number(to_number)
    candidate_numbers = [to_number, clean_to]
    if to_number and not to_number.startswith("+") and clean_to:
        candidate_numbers.append(f"+{clean_to}")

    if redis_client:
        for candidate in candidate_numbers:
            try:
                trunk_id = await redis_client.get(f"plivo:sip_trunk:{candidate}")
                if trunk_id:
                    logger.info(f"Resolved Plivo SIP trunk from Redis for {to_number}: {trunk_id}")
                    return trunk_id
            except Exception as e:
                logger.warning(f"Redis lookup failed for Plivo SIP trunk {candidate}: {e}")

    try:
        conn = await get_db_connection()
        row = await conn.fetchrow(
            "SELECT sip_trunk_id FROM org_configs WHERE phone_number IN ($1, $2)",
            to_number,
            clean_to,
        )
        await conn.close()
        if row and row["sip_trunk_id"]:
            trunk_id = row["sip_trunk_id"]
            logger.info(f"Resolved Plivo SIP trunk from DB for {to_number}: {trunk_id}")
            if redis_client:
                await redis_client.set(f"plivo:sip_trunk:{to_number}", trunk_id, ex=86400 * 30)
            return trunk_id
    except Exception as e:
        logger.warning(f"DB lookup failed for Plivo SIP trunk: {e}")

    if lk_client:
        try:
            resp = await lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
            for item in getattr(resp, "items", []) or []:
                numbers = [str(n).strip() for n in getattr(item, "numbers", []) or []]
                normalized_numbers = {_normalize_phone_number(n) for n in numbers}
                if clean_to in normalized_numbers or (to_number and _normalize_phone_number(to_number) in normalized_numbers):
                    trunk_id = getattr(item, "sip_trunk_id", None)
                    if trunk_id:
                        logger.info(f"Resolved Plivo SIP trunk from LiveKit inbound trunks for {to_number}: {trunk_id}")
                        if redis_client:
                            await redis_client.set(f"plivo:sip_trunk:{to_number}", trunk_id, ex=86400 * 30)
                        return trunk_id
        except Exception as e:
            logger.warning(f"LiveKit inbound trunk lookup failed for Plivo: {e}")

    return None


def _build_plivo_xml(sip_trunk_id: str, sip_domain: str, action_url: str, phone_number: str = "") -> str:
    """
    DEPRECATED: Plivo Application XML approach is replaced by Zentrunk SIP trunking.
    Kept for backward compatibility; new setups should use Zentrunk.
    """
    sip_username = escape(phone_number or sip_trunk_id)
    sip_domain = escape(sip_domain)
    action_url = escape(action_url)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial action="{action_url}" method="POST" timeout="20">
        <User>sip:{sip_username}@{sip_domain}</User>
    </Dial>
</Response>'''


@app.get("/v1/sip/plivo-xml")
@app.post("/v1/sip/plivo-xml")
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


@app.get("/v1/sip/twilio-webhook")
@app.post("/v1/sip/twilio-webhook")
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
    
    if redis_client:
        try:
            # Try with + prefix first, then without
            sip_trunk_id = await redis_client.get(f"twilio:sip_trunk:{to_number}")
            if not sip_trunk_id:
                sip_trunk_id = await redis_client.get(f"twilio:sip_trunk:{clean_to}")
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
                if redis_client:
                    await redis_client.set(f"twilio:sip_trunk:{to_number}", sip_trunk_id, ex=86400*30)
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


def _get_sip_domain() -> str:
    configured_domain = os.getenv("LIVEKIT_SIP_DOMAIN") or os.getenv("SIP_DOMAIN")
    if configured_domain:
        return configured_domain

    lk_url = os.getenv("LIVEKIT_URL", "")
    host_lk = lk_url.replace("wss://", "").replace("ws://", "").replace("https://", "").replace("http://", "")
    if "livekit.cloud" in host_lk:
        subdomain = host_lk.split(".")[0]
        if subdomain and subdomain != "www":
            return f"{subdomain}.sip.livekit.cloud"
    return "sip.livekit.cloud"


def _get_zadarma_credentials() -> tuple[str, str]:
    """Resolve Zadarma credentials from either the current or legacy env var names."""
    zadarma_key = os.getenv("ZADARMA_API_KEY") or os.getenv("ZADARMA_KEY")
    zadarma_secret = os.getenv("ZADARMA_API_SECRET") or os.getenv("ZADARMA_SECRET")
    return zadarma_key or "", zadarma_secret or ""


async def _update_zadarma_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Updates the SIP URI forwarding in Zadarma using their REST API.
    Handles the HMAC-SHA1 + MD5 signature required by Zadarma.
    """
    zadarma_key, zadarma_secret = _get_zadarma_credentials()
    
    if not zadarma_key or not zadarma_secret:
        raise ValueError("Zadarma API credentials not found in environment variables.")

    # Normalize phone number (Zadarma expects it without the '+')
    number_clean = phone_number.replace("+", "")
    
    # Zadarma expects external SIP URIs without the 'sip:' prefix
    sip_uri_clean = sip_uri.replace("sip:", "")
    
    # Sort parameters alphabetically as required by Zadarma for signature
    params = {
        'number': number_clean,
        'sip_id': sip_uri_clean
    }
    # Create ordered query string
    sorted_params = {k: params[k] for k in sorted(params.keys())}
    query_string = urlencode(sorted_params)
    
    # 1. MD5 of the query string
    md5_hash = hashlib.md5(query_string.encode('utf-8')).hexdigest()
    
    # 2. String to sign: API_METHOD + QUERY_STRING + MD5_HASH
    api_method = "/v1/direct_numbers/set_sip_id/"
    string_to_sign = api_method + query_string + md5_hash
    
    # 3. HMAC-SHA1 signature using Secret Key, hex digest, then Base64 encoded
    mac_hex = hmac.new(
        zadarma_secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha1
    ).hexdigest()
    signature = base64.b64encode(mac_hex.encode('utf-8')).decode('utf-8')
    
    headers = {
        'Authorization': f'{zadarma_key}:{signature}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    url = f"https://api.zadarma.com{api_method}"
    
    # Send PUT request with query parameters
    async with aiohttp.ClientSession() as session:
        async with session.put(url, data=sorted_params, headers=headers) as resp:
            text = await resp.text()
            if resp.status == 200:
                try:
                    return json.loads(text)
                except Exception:
                    return {"status": "success", "response": text}
            else:
                logger.error(f"Zadarma API error {resp.status}: {text}")
                raise Exception(f"Zadarma API error: {text}")


async def _update_twilio_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Updates the SIP URI forwarding in Twilio by updating the Incoming Phone Number's Voice URL.
    Uses Twilio REST API to set the SIP trunk as the voice webhook destination.
    """
    twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    
    if not twilio_account_sid or not twilio_auth_token:
        raise ValueError("Twilio API credentials not found in environment variables.")

    # Normalize phone number (Twilio expects E.164 format with +)
    number_clean = phone_number if phone_number.startswith("+") else f"+{phone_number}"
    
    # Twilio SIP URI format - remove sip: prefix for the Voice URL
    # Twilio expects a webhook URL that returns TwiML, but for SIP trunking
    # we use the SIP Domain approach. The SIP URI is used in the SIP Domain.
    sip_uri_clean = sip_uri.replace("sip:", "")
    
    # Find the incoming phone number resource
    import base64
    auth = base64.b64encode(f"{twilio_account_sid}:{twilio_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    async with aiohttp.ClientSession() as session:
        # First, find the phone number SID
        url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_account_sid}/IncomingPhoneNumbers.json"
        params = {"PhoneNumber": number_clean}
        async with session.get(url, headers=headers, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                logger.error(f"Twilio API error listing numbers {resp.status}: {text}")
                raise Exception(f"Twilio API error: {text}")
            
            data = json.loads(text)
            numbers = data.get("incoming_phone_numbers", [])
            if not numbers:
                raise Exception(f"Phone number {number_clean} not found in Twilio account")
            
            number_sid = numbers[0]["sid"]
        
        # Update the VoiceUrl to point to our SIP domain
        # For SIP trunking, Twilio uses SIP Domain - we need to configure the SIP Domain
        # to route to the LiveKit SIP URI. This is typically done via TwiML app or SIP Domain.
        # Here we'll use the VoiceUrl with a TwiML that forwards to the SIP URI
        voice_url = f"https://{os.getenv('LIVEKIT_URL', '').replace('wss://', '').replace('ws://', '').replace('https://', '').replace('http://', '')}/v1/sip/twilio-webhook"
        
        update_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_account_sid}/IncomingPhoneNumbers/{number_sid}.json"
        update_data = {"VoiceUrl": voice_url, "VoiceMethod": "POST"}
        async with session.post(update_url, headers=headers, data=update_data) as resp:
            text = await resp.text()
            if resp.status == 200:
                try:
                    return json.loads(text)
                except Exception:
                    return {"status": "success", "response": text}
            else:
                logger.error(f"Twilio API error updating number {resp.status}: {text}")
                raise Exception(f"Twilio API error: {text}")


async def _plivo_number_is_linked_to_zentrunk(phone_number: str) -> bool:
    """
    Return True if the Plivo number is already linked to the LiveKit SIP domain's
    Zentrunk inbound trunk, i.e. provider forwarding is genuinely configured.
    """
    plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
    plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
    if not plivo_auth_id or not plivo_auth_token:
        return True  # Cannot verify; keep the caller's existing behaviour.

    number_clean = phone_number.replace("+", "").replace(" ", "")
    import base64
    auth = base64.b64encode(f"{plivo_auth_id}:{plivo_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json'
    }
    base_url = f"https://api.plivo.com/v1/Account/{plivo_auth_id}"
    trunk_label = f"LiveKit ({_get_sip_domain().split('.')[0]})"
    expected_trunk_name = f"Inbound via {trunk_label}"

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/Zentrunk/Trunk/", headers=headers) as resp:
            trunk_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        trunk_id = None
        for trunk_obj in trunk_list.get("objects", []):
            if trunk_obj.get("name") == expected_trunk_name:
                trunk_id = trunk_obj.get("trunk_id")
                break
        if not trunk_id:
            return False
        async with session.get(f"{base_url}/Number/{number_clean}/", headers=headers) as resp:
            if resp.status != 200:
                return False
            number_obj = json.loads(await resp.text())
        return number_obj.get("app_id") == trunk_id


async def _update_plivo_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Configures Plivo Zentrunk SIP trunking for inbound calls.
    Creates a Zentrunk origination URI pointing to LiveKit's SIP domain,
    a Zentrunk inbound trunk, and links the phone number to the trunk.
    This replaces the Plivo Application XML webhook approach with
    direct SIP trunking as documented by LiveKit.
    """
    plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
    plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
    
    if not plivo_auth_id or not plivo_auth_token:
        raise ValueError("Plivo API credentials not found in environment variables.")

    number_clean = phone_number.replace("+", "").replace(" ", "")
    
    import base64
    auth = base64.b64encode(f"{plivo_auth_id}:{plivo_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json'
    }
    
    sip_domain = _get_sip_domain()
    origination_host = f"{sip_domain}:5061;transport=tls"
    base_url = f"https://api.plivo.com/v1/Account/{plivo_auth_id}"
    trunk_label = f"LiveKit ({sip_domain.split('.')[0]})"
    
    async with aiohttp.ClientSession() as session:
        # 1. Create or find existing Zentrunk origination URI for this SIP domain.
        #    The URI name is deterministic per SIP domain, so match by name as well
        #    as by uri field — a URI may exist under this name even when the uri
        #    field doesn't contain our current sip_domain string.
        async with session.get(f"{base_url}/Zentrunk/URI/", headers=headers) as resp:
            uri_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        uri_uuid = None
        for uri_obj in uri_list.get("objects", []):
            if sip_domain in uri_obj.get("uri", "") and "transport=tls" in uri_obj.get("uri", ""):
                uri_uuid = uri_obj.get("uri_uuid")
                logger.info(f"Found existing Zentrunk origination URI {uri_uuid}: {uri_obj.get('uri')}")
                break

        if not uri_uuid:
            for uri_obj in uri_list.get("objects", []):
                if uri_obj.get("name") == trunk_label:
                    uri_uuid = uri_obj.get("uri_uuid")
                    logger.info(f"Found existing Zentrunk origination URI {uri_uuid} by name: {trunk_label}")
                    break

        if not uri_uuid:
            uri_data = {"uri": origination_host, "name": trunk_label}
            async with session.post(f"{base_url}/Zentrunk/URI/", headers=headers, json=uri_data) as resp:
                result_text = await resp.text()
                result = json.loads(result_text) if result_text else {}
                if resp.status in (200, 201, 202):
                    uri_uuid = result.get("uri_uuid")
                    logger.info(f"Created Zentrunk origination URI {uri_uuid}: {origination_host}")
                else:
                    raise Exception(f"Failed to create Zentrunk URI: {result}")

        # 2. Create or find existing Zentrunk inbound trunk using this URI.
        #    The trunk name is deterministic per SIP domain, so a trunk created for a
        #    previous number already exists under this name. Match by name as well as by
        #    primary_uri_uuid so we reuse it instead of hitting Plivo's
        #    "A trunk with the same name ... already exists" error.
        expected_trunk_name = f"Inbound via {trunk_label}"
        async with session.get(f"{base_url}/Zentrunk/Trunk/", headers=headers) as resp:
            trunk_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        trunk_id = None
        for trunk_obj in trunk_list.get("objects", []):
            if trunk_obj.get("primary_uri_uuid") == uri_uuid:
                trunk_id = trunk_obj.get("trunk_id")
                logger.info(f"Found existing Zentrunk inbound trunk {trunk_id}: {trunk_obj.get('name')}")
                break

        if not trunk_id:
            for trunk_obj in trunk_list.get("objects", []):
                if trunk_obj.get("name") == expected_trunk_name:
                    trunk_id = trunk_obj.get("trunk_id")
                    logger.info(f"Found existing Zentrunk inbound trunk {trunk_id} by name: {expected_trunk_name}")
                    existing_uri = trunk_obj.get("primary_uri_uuid")
                    if existing_uri and existing_uri != uri_uuid:
                        # Trunk points to a different (possibly stale) URI; repoint it at ours.
                        try:
                            async with session.post(
                                f"{base_url}/Zentrunk/Trunk/{trunk_id}/",
                                headers=headers,
                                json={"primary_uri_uuid": uri_uuid},
                            ) as resp:
                                if resp.status in (200, 202):
                                    logger.info(f"Repointed Zentrunk inbound trunk {trunk_id} to URI {uri_uuid}")
                                else:
                                    logger.warning(f"Could not repoint Zentrunk trunk {trunk_id}: {await resp.text()}")
                        except Exception as e:
                            logger.warning(f"Error repointing Zentrunk trunk {trunk_id}: {e}")
                    elif existing_uri:
                        uri_uuid = existing_uri
                    break

        if not trunk_id:
            trunk_data = {
                "name": expected_trunk_name,
                "trunk_direction": "inbound",
                "primary_uri_uuid": uri_uuid
            }
            async with session.post(f"{base_url}/Zentrunk/Trunk/", headers=headers, json=trunk_data) as resp:
                result_text = await resp.text()
                result = json.loads(result_text) if result_text else {}
                if resp.status in (200, 201, 202):
                    trunk_id = result.get("trunk_id")
                    logger.info(f"Created Zentrunk inbound trunk {trunk_id}")
                else:
                    raise Exception(f"Failed to create Zentrunk trunk: {result}")
        
        # 3. Verify the number exists in Plivo
        async with session.get(f"{base_url}/Number/{number_clean}/", headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Phone number +{number_clean} not found in Plivo account")
        
        # 4. Link the phone number to the Zentrunk trunk (replaces any existing Application)
        update_data = {"app_id": trunk_id}
        async with session.post(f"{base_url}/Number/{number_clean}/", headers=headers, json=update_data) as resp:
            text = await resp.text()
            if resp.status in (200, 202):
                try:
                    result = json.loads(text)
                except Exception:
                    result = {"status": "success", "response": text}
                result["zentrunk_trunk_id"] = trunk_id
                result["zentrunk_uri_uuid"] = uri_uuid
                result["zentrunk_sip_domain"] = sip_domain
                logger.info(f"Linked number +{number_clean} to Zentrunk trunk {trunk_id}")
                return result
            else:
                logger.error(f"Plivo API error linking number to Zentrunk trunk {resp.status}: {text}")
                raise Exception(f"Plivo API error: {text}")


async def _update_voicelink_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    VoiceLink is a LiveKit-native SIP provider — the LiveKit inbound trunk + dispatch rule
    are already configured. The user must link this SIP URI in their VoiceLink dashboard.
    """
    logger.info(f"VoiceLink inbound SIP configured for {phone_number} -> {sip_uri}")
    return {
        "status": "success",
        "provider": "voice_link",
        "phone_number": phone_number,
        "sip_uri": sip_uri,
    }


async def _update_provider_sip_forwarding(provider: str, phone_number: str, sip_uri: str) -> dict:
    """
    Routes to the appropriate provider-specific SIP forwarding function.
    Supported providers: zadarma, twilio, plivo, voice_link
    """
    provider = provider.lower().strip()
    
    if provider == "zadarma":
        return await _update_zadarma_sip_forwarding(phone_number, sip_uri)
    elif provider == "twilio":
        return await _update_twilio_sip_forwarding(phone_number, sip_uri)
    elif provider == "plivo":
        return await _update_plivo_sip_forwarding(phone_number, sip_uri)
    elif provider in ("voicelink", "voice_link"):
        return await _update_voicelink_sip_forwarding(phone_number, sip_uri)
    else:
        raise ValueError(f"Unsupported provider: {provider}. Supported providers: zadarma, twilio, plivo, voice_link")


@app.post("/v1/sip/inbound/setup")
async def setup_inbound_sip(request: Request):
    """
    End-to-end inbound SIP setup:
    1. Creates LiveKit Inbound Trunk
    2. Creates LiveKit Dispatch Rule
    3. Triggers provider API (Zadarma/Twilio/Plivo) to update the forwarding URI
    
    Payload:
    - number (required): Phone number in E.164 format (e.g., +918031321203)
    - org_id (required): Organization ID
    - provider (optional): SIP provider - "zadarma", "twilio", or "plivo" (default: "zadarma")
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


async def _setup_inbound_sip_process(payload: dict | None) -> JSONResponse:
    if payload is None:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Invalid JSON"}, status_code=400)
    
    number = payload.get("number")
    if not number:
        return JSONResponse({"status_code": 400, "status": "error", "error": "number is required"}, status_code=400)

    provider = payload.get("provider").lower().strip()
    name = payload.get("name", f"{provider} {number}")
    prompt = payload.get("prompt", "You are a helpful voice assistant.")
    voice = payload.get("voice", "arushi")
    model = payload.get("model", "deepseek")
    
    # New fields for org configuration
    org_id = payload.get("org_id")
    if not org_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id is required"}, status_code=400)
    kb_tags = payload.get("kb_tags", [])
    transfer_numbers = payload.get("transfer_numbers", {})
    client_name = payload.get("client_name", "User")
    process_id = payload.get("process_id")
    
    logger.info(f"Starting end-to-end SIP setup for number: {number}, org_id: {org_id}, provider: {provider}")
    
    try:
        # 1. Check for existing inbound trunk with this number (skip if force_new=true)
        clean_number = number.replace("+", "")
        existing_trunk_id = None
        existing_rule_id = None
        
        force_new = payload.get("force_new", False)
        
        if not force_new:
            # Run trunk listing and rule listing in parallel
            async def _find_existing_trunk():
                try:
                    response = await lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
                    for item in response.items:
                        trunk_numbers = list(item.numbers)
                        if number in trunk_numbers or clean_number in trunk_numbers:
                            return item.sip_trunk_id
                except Exception as e:
                    logger.warning(f"Could not list existing trunks: {e}")
                return None
            
            async def _find_existing_rule(trunk_id):
                if not trunk_id:
                    return None
                try:
                    rule_response = await lk_client.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
                    for item in rule_response.items:
                        if trunk_id in list(item.trunk_ids):
                            return item.sip_dispatch_rule_id
                except Exception as e:
                    logger.warning(f"Could not list dispatch rules: {e}")
                return None
            
            # First find trunk, then find rule (rule depends on trunk)
            existing_trunk_id = await _find_existing_trunk()
            if existing_trunk_id:
                logger.info(f"Found existing inbound trunk {existing_trunk_id} for number {number}")
                existing_rule_id = await _find_existing_rule(existing_trunk_id)
                if existing_rule_id:
                    logger.info(f"Found existing dispatch rule {existing_rule_id} for trunk {existing_trunk_id}")
        else:
            logger.info(f"force_new=true: Skipping existing trunk/rule checks for {number}")
        
        # If number already fully configured, return clear error to MantraAssist.
        # Provider forwarding is the last and most fragile step of setup; if it failed
        # previously we must NOT report "already configured" — instead fall through and
        # complete the setup idempotently using the existing trunk/rule.
        if existing_trunk_id and existing_rule_id:
            already_configured = True
            if provider == "plivo":
                try:
                    already_configured = await _plivo_number_is_linked_to_zentrunk(number)
                except Exception as e:
                    logger.warning(f"Could not verify Plivo forwarding for {number}: {e}")
            if already_configured:
                return JSONResponse({
                    "status_code": 409,
                    "status": "error",
                    "error": "number_already_configured",
                    "message": f"Phone number {number} is already configured",
                    "existing_trunk_id": existing_trunk_id,
                    "existing_dispatch_rule_id": existing_rule_id
                }, status_code=409)
            logger.info(f"Number {number} partially configured (provider forwarding missing); completing setup with existing trunk/rule")
        
        # 2. Create Inbound Trunk (or reuse existing)
        if existing_trunk_id:
            trunk_id = existing_trunk_id
            logger.info(f"Reusing existing LiveKit SIP Inbound Trunk: {trunk_id}")
        else:
            trunk = await lk_client.sip.create_sip_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(
                        name=name,
                        numbers=[number, clean_number],
                        allowed_addresses=["0.0.0.0/0"],
                    )
                )
            )
            trunk_id = trunk.sip_trunk_id
            logger.info(f"Created LiveKit SIP Inbound Trunk: {trunk_id}")
        
        # Store SIP trunk mapping in Redis for webhooks lookup
        # This allows the provider webhooks (Twilio/Plivo) to find the correct SIP trunk ID for incoming calls
        if redis_client and provider in ["plivo", "twilio", "voice_link", "voicelink"]:
            try:
                # Store with both +prefix and without for flexible lookup
                await redis_client.set(f"{provider}:sip_trunk:{number}", trunk_id, ex=86400*30)  # 30 days TTL
                await redis_client.set(f"{provider}:sip_trunk:{clean_number}", trunk_id, ex=86400*30)
                logger.info(f"Stored {provider} SIP trunk mapping: {number} -> {trunk_id}")
            except Exception as e:
                logger.warning(f"Failed to store {provider} SIP trunk mapping in Redis: {e}")
        
        # 3. Create Dispatch Rule (or reuse if trunk existed but no rule)
        if existing_rule_id:
            rule_id = existing_rule_id
            logger.info(f"Reusing existing LiveKit SIP Dispatch Rule: {rule_id}")
        else:
            # We inject direction=inbound and the given prompt/voice into metadata
            room_prefix = f"inbound_{trunk_id[-6:]}"
            metadata_dict = {
                "direction": "inbound",
                "prompt": prompt,
                "voice": voice,
                "model": model,
                "phone_number": number,
                "provider": provider
            }
            
            rule_req = api.CreateSIPDispatchRuleRequest(
                name=f"Rule for {name}",
                metadata=json.dumps(metadata_dict),
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix=room_prefix
                    )
                ),
                room_config=api.RoomConfiguration(
                    empty_timeout=300,
                    departure_timeout=60,
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=AGENT_NAME,
                            metadata=json.dumps(metadata_dict)
                        )
                    ]
                ),
                trunk_ids=[trunk_id]
            )
            
            rule = await lk_client.sip.create_sip_dispatch_rule(rule_req)
            rule_id = rule.sip_dispatch_rule_id
            logger.info(f"Created LiveKit SIP Dispatch Rule: {rule_id}")
        
        # 4. Generate SIP URI
        sip_domain = _get_sip_domain()
        # Use the clean_number so that provider sends the INVITE with To: <clean_number>@<sip_domain>
        # This allows LiveKit to correctly match the inbound SIP trunk which has this number in its numbers array.
        sip_uri = f"sip:{clean_number}@{sip_domain}"
        
        # 5. Update provider SIP forwarding
        logger.info(f"Updating {provider} SIP ID for {number} to {sip_uri}")
        try:
            provider_response = await _update_provider_sip_forwarding(provider, number, sip_uri)
        except Exception as e:
            # If provider fails (e.g., number not in provider account), return clear error.
            # org_configs has not been saved yet, so a retry will complete the setup
            # instead of being rejected as "already configured".
            return JSONResponse({
                "status_code": 400,
                "status": "error",
                "error": f"{provider}_configuration_failed",
                "message": f"Failed to configure {provider} for {number}: {str(e)}. Ensure the number exists in your {provider} account.",
                "sip_trunk_id": trunk_id,
                "sip_dispatch_rule_id": rule_id,
                "sip_uri": sip_uri
            }, status_code=400)

        # 5.5 Create or update org_configs mapping (only after provider forwarding has
        # succeeded, so the DB row reflects an actually-configured number)
        try:
            conn = await get_db_connection()
            org_config_id = await conn.fetchval("""
                INSERT INTO org_configs (
                    org_id, phone_number, name, prompt, voice, model, 
                    kb_tags, transfer_numbers, client_name, process_id, 
                    sip_trunk_id, dispatch_rule_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 
                    $7, $8, $9, $10, 
                    $11, $12
                )
                ON CONFLICT (phone_number) DO UPDATE SET
                    org_id = EXCLUDED.org_id,
                    name = EXCLUDED.name,
                    prompt = EXCLUDED.prompt,
                    voice = EXCLUDED.voice,
                    model = EXCLUDED.model,
                    kb_tags = EXCLUDED.kb_tags,
                    transfer_numbers = EXCLUDED.transfer_numbers,
                    client_name = EXCLUDED.client_name,
                    process_id = EXCLUDED.process_id,
                    sip_trunk_id = EXCLUDED.sip_trunk_id,
                    dispatch_rule_id = EXCLUDED.dispatch_rule_id,
                    is_active = true,
                    updated_at = NOW()
                RETURNING id;
            """, 
            str(org_id), clean_number, name, prompt, voice, model, 
            kb_tags, json.dumps(transfer_numbers), client_name, process_id, 
            trunk_id, rule_id)
            await conn.close()
            logger.info(f"Successfully saved org_config for {clean_number} with ID: {org_config_id}")
        except Exception as e:
            logger.error(f"Failed to save org_config to database: {e}")
            # We continue even if this fails, to not break existing functionality completely,
            # though the agent might fall back to MantraAssist.
            org_config_id = None

        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "name": name,
            "org_id": org_id,
            "org_config_id": str(org_config_id) if org_config_id else None,
            "sip_trunk_id": trunk_id,
            "sip_dispatch_rule_id": rule_id,
            "sip_uri": sip_uri,
            "provider": provider,
            "provider_response": provider_response
        })
            
    except Exception as e:
        logger.error(f"Error during SIP setup: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)



@app.post("/v1/sip/plivo-dial-status")
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





@app.post("/v1/webhooks/telephony")
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

    if redis_client:
        # Clear stale backend delivery lock for new/retry attempts
        try:
            await redis_client.delete(f"backend_sent:{call_id}")
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
                await redis_client.delete(f"lock:call:{call_id}")
            except Exception:
                pass

        lock_acquired = await redis_client.set(f"lock:call:{call_id}", "1", nx=True, ex=30)
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

        if redis_client:
            try:
                await redis_client.set(f"sip_error_status:{call_id}", sip_status, ex=300)
            except Exception:
                pass

        try:
            await lk_client.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info(f"Deleted room {room_name} due to call failure ({sip_status})")
        except Exception:
            pass

        if redis_client:
            try:
                await redis_client.delete(f"lock:call:{call_id}")
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
            await lk_client.agent_dispatch.create_dispatch(
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

        sip_client = lk_client
        if provider == "plivo" and plivo_client:
            sip_client = plivo_client
        elif provider == "voice_link" and voicelink_client:
            sip_client = voicelink_client
        proxy_msg = (
            "proxied Plivo client"
            if sip_client == plivo_client
            else "proxied VoiceLink client"
            if sip_client == voicelink_client
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
                participants = await lk_client.room.list_participants(
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


async def _create_sip_outbound_trunk(
    name: str,
    address: str,
    numbers: list,
    auth_username: str,
    auth_password: str,
    client: api.LiveKitAPI = None,
    destination_country: str = None,
):
    if not all([name, address, numbers, auth_username, auth_password]):
        missing = [
            f
            for f, v in [
                ("name", name),
                ("address", address),
                ("numbers", numbers),
                ("auth_username", auth_username),
                ("auth_password", auth_password),
            ]
            if not v
        ]
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
                name=name,
                address=address,
                numbers=numbers,
                auth_username=auth_username,
                auth_password=auth_password,
                destination_country=destination_country,
            )
        )
        trunk = await svc.create_outbound_trunk(trunk_request)
        logger.info(
            f"Successfully created SIP outbound trunk: {trunk.sip_trunk_id} ({name})"
        )
        return trunk
    except Exception as e:
        logger.error(f"LiveKit API error creating SIP trunk: {e}")
        raise


async def _get_provider_from_trunk(trunk_id: str) -> str | None:
    if redis_client:
        stored = await redis_client.get(f"trunk:provider:{trunk_id}")
        if stored:
            return stored

    provider = None

    try:
        response = await lk_client.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest(trunk_ids=[trunk_id])
        )
        if response.items:
            address = (response.items[0].address or "").lower()
            logger.info(f"Trunk lookup: {trunk_id} address={address}")
            if "twilio" in address:
                provider = "twilio"
            elif "plivo" in address:
                provider = "plivo"
            elif "zadarma" in address:
                provider = "zadarma"
            else:
                logger.warning(f"Trunk {trunk_id} address '{address}' does not match known providers")
    except Exception as e:
        logger.warning(f"Cannot list trunk {trunk_id} via lk_client: {e}")

    if provider is None and voicelink_client:
        try:
            vl_resp = await voicelink_client.sip.list_outbound_trunk(
                api.ListSIPOutboundTrunkRequest(trunk_ids=[trunk_id])
            )
            if vl_resp.items:
                provider = "voice_link"
        except Exception as e:
            logger.warning(f"Cannot list trunk {trunk_id} via voicelink_client: {e}")

    if redis_client and provider:
        await redis_client.set(f"trunk:provider:{trunk_id}", provider, ex=86400 * 30)
    return provider


@app.post("/v1/sip/trunks/outbound")
@app.post("/v1/sip/trunks/outbound/zadarma")
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


@app.post("/v1/sip/trunks/outbound/twilio")
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

@app.post("/v1/sip/trunks/outbound/voice_link")
async def create_voicelink_sip_trunk(request: Request):
    """
    Create a new Voicelink SIP trunk.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"},
        status_code=400)

    if voicelink_client is None:
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
                client=voicelink_client,
                destination_country="in",
            )
            trunk_id = trunk.sip_trunk_id
            if redis_client:
                await redis_client.set(f"trunk:provider:{trunk_id}", "voice_link", ex=86400 * 30)
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



@app.post("/v1/sip/trunks/outbound/plivo")
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
                auth_username=trunk_data.get("authUsername")
                or trunk_data.get("auth_username")
                or trunk_data.get("auth_user"),
                auth_password=trunk_data.get("authPassword")
                or trunk_data.get("auth_password")
                or trunk_data.get("auth_pass"),
                client=plivo_client,
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
                client=plivo_client,
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
        if redis_client:
            lock_acquired = await redis_client.set(f"lock:call:{call_id}", "1", nx=True, ex=30)
            if not lock_acquired:
                logger.warning(f"Duplicate Plivo call request ignored for call_id: {call_id} (call in-progress)")
                return JSONResponse({
                    "status": "ignored",
                    "message": f"Duplicate request for call_id {call_id} already in progress",
                    "room": room_name
                }, status_code=200)
        


        logger.info(f"Dispatching agent to room {room_name}")
        await lk_client.agent_dispatch.create_dispatch(
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

        sip_part = await plivo_client.sip.create_sip_participant(
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


@app.get("/v1/sip/trunks/outbound")
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


@app.delete("/v1/sip/trunks/outbound/{trunk_id}")
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

@app.patch("/v1/sip/trunks/inbound/{trunk_id}")
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

@app.patch("/v1/sip/dispatch-rules/{rule_id}")
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
    return JSONResponse({"url": os.getenv("LIVEKIT_URL")})


# ── Dashboard API (authenticated) ────────────────────────────────────────


@app.get("/v1/dashboard/stream")
async def dashboard_stream(request: Request):
    """SSE endpoint with real-time queue status + active call details."""
    # require_auth(request)

    async def event_generator():
        if not redis_client:
            yield 'data: {"error": "Redis not connected"}\n\n'
            return

        MAX_CONCURRENCY = int(
            os.getenv("MAX_CONCURRENCY", os.getenv("CARTESIA_MAX_CONCURRENCY", "5"))
        )

        while True:
            try:
                pending_count = await redis_client.zcard("queue:pending")
                active_calls_map = await redis_client.hgetall("calls:active")
                active_count = len(active_calls_map)

                active_details = []
                for call_id, room_name in active_calls_map.items():
                    status = await redis_client.get(f"calls:status:{call_id}")
                    active_details.append(
                        {
                            "call_id": call_id,
                            "room_name": room_name,
                            "status": status or "unknown",
                        }
                    )

                data = json.dumps(
                    {
                        "pending_calls": pending_count,
                        "active_calls": active_count,
                        "max_concurrency": MAX_CONCURRENCY,
                        "active_call_details": active_details,
                        "timestamp": time.time(),
                    }
                )
                yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"Dashboard SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/v1/dashboard/metrics")
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
                            WHERE call_log::json ->> 'call_duration_seconds' ~ '^\\d+$'
                        )
                    )::int AS avg_duration_seconds
                FROM call_logs
                WHERE created_at >= CURRENT_DATE
            """)
        finally:
            await conn.close()

        metrics = (
            dict(row)
            if row
            else {
                "total_calls": 0,
                "completed_calls": 0,
                "busy_calls": 0,
                "no_answer_calls": 0,
                "error_calls": 0,
                "incomplete_calls": 0,
                "avg_duration_seconds": 0,
            }
        )

        answer_rate = (
            round(metrics["completed_calls"] / metrics["total_calls"] * 100, 1)
            if metrics["total_calls"] > 0
            else 0
        )

        return {
            **metrics,
            "answer_rate": answer_rate,
        }
    except Exception as e:
        logger.error(f"Dashboard metrics error: {e}")
        return {"error": str(e)}


@app.get("/v1/dashboard/calls")
async def dashboard_calls(request: Request, limit: int = 20, offset: int = 0, search: str = None, status: str = None):
    """Paginated call history from PostgreSQL with search & status filtering."""
    try:
        conn = await get_db_connection()
        try:
            conditions = []
            params = []
            param_idx = 1

            if search and search.strip():
                conditions.append(f"(CAST(call_id AS TEXT) ILIKE ${param_idx} OR caller_number ILIKE ${param_idx} OR called_number ILIKE ${param_idx} OR call_log::text ILIKE ${param_idx})")
                params.append(f"%{search.strip()}%")
                param_idx += 1

            if status and status.strip() and status.lower() != "all":
                st_clean = status.strip().replace(" ", "").replace("_", "").lower()
                conditions.append(f"REPLACE(REPLACE(LOWER(status), '_', ''), ' ', '') LIKE ${param_idx}")
                params.append(f"%{st_clean}%")
                param_idx += 1

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            query = f"""
                SELECT call_id, status, recording_url, created_at, caller_number, called_number, trunk_id,
                       call_log::json AS call_log,
                       COALESCE(attempts, '[]'::jsonb) AS attempts
                FROM call_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params_with_limit = params + [limit, offset]
            rows = await conn.fetch(query, *params_with_limit)

            count_query = f"SELECT COUNT(*)::int AS total FROM call_logs {where_clause}"
            count_row = await conn.fetchrow(count_query, *params)
            total = count_row["total"] if count_row else 0
        finally:
            await conn.close()

        calls = []
        for row in rows:
            cl_raw = row["call_log"]
            if isinstance(cl_raw, str):
                try:
                    cl = json.loads(cl_raw)
                except Exception:
                    cl = {}
            elif isinstance(cl_raw, dict):
                cl = cl_raw
            else:
                cl = {}

            attempts_raw = row.get("attempts")
            if isinstance(attempts_raw, str):
                try:
                    attempts_list = json.loads(attempts_raw)
                except Exception:
                    attempts_list = []
            elif isinstance(attempts_raw, list):
                attempts_list = attempts_raw
            else:
                attempts_list = []

            trunk_val = (
                row["trunk_id"]
                or cl.get("trunk_id")
                or cl.get("sip_trunk_id")
                or cl.get("call_from_id")
                or cl.get("_resolved_trunk_id")
                or cl.get("provider")
                or ""
            )
            calls.append(
                {
                    "call_id": str(row["call_id"]),
                    "status": row["status"],
                    "recording_url": row["recording_url"] or "",
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "caller_number": row["caller_number"] or cl.get("caller_number") or cl.get("client_phone") or "",
                    "called_number": row["called_number"] or cl.get("called_number") or "",
                    "trunk_id": trunk_val,
                    "client_name": cl.get("client_name") or cl.get("client_id") or "",
                    "client_phone": cl.get("client_phone") or row["caller_number"] or "",
                    "duration": cl.get("call_duration_seconds"),
                    "summary": cl.get("ai_summary") or cl.get("summary") or "",
                    "transcript": cl.get("call_transcript") or cl.get("transcript") or cl.get("conversation") or None,
                    "purpose": (cl.get("prompt") or "")[:120],
                    "attempts": attempts_list,
                    "attempts_count": len(attempts_list),
                    "call_log_raw": cl,
                }
            )

        return {"calls": calls, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"Dashboard calls error: {e}")
        return {"error": str(e), "calls": [], "total": 0}


@app.get("/v1/dashboard/active-calls")
async def dashboard_active_calls(request: Request):
    """Current active calls from Redis."""
    if not redis_client:
        return {"active_calls": [], "error": "Redis not connected"}

    try:
        active_map = await redis_client.hgetall("calls:active")
        calls = []
        for call_id, room_name in active_map.items():
            status = await redis_client.get(f"calls:status:{call_id}")
            calls.append(
                {
                    "call_id": call_id,
                    "room_name": room_name,
                    "status": status or "unknown",
                }
            )
        return {"active_calls": calls}
    except Exception as e:
        logger.error(f"Active calls error: {e}")
        return {"active_calls": [], "error": str(e)}



@app.get("/v1/redis/info")
async def redis_info(request: Request):
    """Get Redis server telemetry & summary statistics."""
    if not redis_client:
        return {"error": "Redis client not connected", "status": "disconnected"}

    try:
        raw_info = await redis_client.info()
        pending_count = await redis_client.zcard("queue:pending")
        active_count = await redis_client.hlen("calls:active")
        db_size = await redis_client.dbsize()

        return {
            "status": "online",
            "redis_version": raw_info.get("redis_version", "N/A"),
            "used_memory_human": raw_info.get("used_memory_human", "N/A"),
            "used_memory_peak_human": raw_info.get("used_memory_peak_human", "N/A"),
            "connected_clients": raw_info.get("connected_clients", 0),
            "uptime_in_seconds": raw_info.get("uptime_in_seconds", 0),
            "uptime_in_days": raw_info.get("uptime_in_days", 0),
            "total_commands_processed": raw_info.get("total_commands_processed", 0),
            "instantaneous_ops_per_sec": raw_info.get("instantaneous_ops_per_sec", 0),
            "total_keys": db_size,
            "queue_pending_count": pending_count,
            "active_calls_count": active_count,
        }
    except Exception as e:
        logger.error(f"Redis info error: {e}")
        return {"error": str(e), "status": "error"}


@app.get("/v1/redis/queue")
async def redis_queue_items(request: Request):
    """Fetch all pending jobs in the queue:pending sorted set."""
    if not redis_client:
        return {"error": "Redis client not connected", "items": []}

    try:
        raw_items = await redis_client.zrange("queue:pending", 0, -1, withscores=True)
        items = []
        for rank, (payload_str, score) in enumerate(raw_items):
            parsed_payload = {}
            call_id = "N/A"
            client_name = "N/A"
            phone = "N/A"
            try:
                parsed_payload = json.loads(payload_str)
                call_id = str(parsed_payload.get("call_id", "N/A"))
                client_name = parsed_payload.get("client_name", "N/A")
                phone = parsed_payload.get("phone_number") or parsed_payload.get("client_phone") or "N/A"
            except Exception:
                pass

            items.append({
                "rank": rank + 1,
                "score": score,
                "call_id": call_id,
                "client_name": client_name,
                "phone": phone,
                "raw_payload": payload_str,
                "parsed_payload": parsed_payload
            })

        return {"count": len(items), "items": items}
    except Exception as e:
        logger.error(f"Redis queue error: {e}")
        return {"error": str(e), "items": []}


@app.get("/v1/redis/active-details")
async def redis_active_details(request: Request):
    """Fetch active calls hash and their detailed status in Redis."""
    if not redis_client:
        return {"error": "Redis client not connected", "calls": []}

    try:
        active_map = await redis_client.hgetall("calls:active")
        calls = []
        for call_id, room_name in active_map.items():
            status = await redis_client.get(f"calls:status:{call_id}")
            lock_ttl = await redis_client.ttl(f"lock:call:{call_id}")
            sip_status = await redis_client.get(f"sip_error_status:{call_id}")

            calls.append({
                "call_id": str(call_id),
                "room_name": room_name,
                "status": status or "in_progress",
                "lock_ttl": lock_ttl if lock_ttl > 0 else 0,
                "sip_error_status": sip_status or None,
            })
        return {"count": len(calls), "calls": calls}
    except Exception as e:
        logger.error(f"Redis active calls error: {e}")
        return {"error": str(e), "calls": []}


@app.get("/v1/redis/keys")
async def redis_keys_list(request: Request, pattern: str = "*", limit: int = 100):
    """Scan and list keys matching pattern with type and TTL."""
    if not redis_client:
        return {"error": "Redis client not connected", "keys": []}

    try:
        matched_keys = []
        cursor = "0"
        count = 0
        while True:
            cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=100)
            for k in keys:
                matched_keys.append(k)
                count += 1
                if count >= limit:
                    break
            if cursor == "0" or cursor == 0 or count >= limit:
                break

        key_details = []
        for k in matched_keys[:limit]:
            k_type = await redis_client.type(k)
            k_ttl = await redis_client.ttl(k)
            key_details.append({
                "key": k,
                "type": k_type.upper() if isinstance(k_type, str) else "UNKNOWN",
                "ttl": k_ttl,
            })

        return {"pattern": pattern, "total_found": len(key_details), "keys": key_details}
    except Exception as e:
        logger.error(f"Redis keys error: {e}")
        return {"error": str(e), "keys": []}


@app.get("/v1/redis/key-detail")
async def redis_key_detail(request: Request, key: str):
    """Get full data of a specific Redis key regardless of type."""
    if not redis_client or not key:
        return {"error": "Key parameter is required"}

    try:
        k_type = await redis_client.type(key)
        k_ttl = await redis_client.ttl(key)
        k_type_str = k_type.upper() if isinstance(k_type, str) else "UNKNOWN"

        val = None
        if k_type_str == "STRING":
            val = await redis_client.get(key)
        elif k_type_str == "HASH":
            val = await redis_client.hgetall(key)
        elif k_type_str == "ZSET":
            val = await redis_client.zrange(key, 0, -1, withscores=True)
        elif k_type_str == "LIST":
            val = await redis_client.lrange(key, 0, -1)
        elif k_type_str == "SET":
            val = list(await redis_client.smembers(key))
        else:
            val = str(await redis_client.get(key))

        return {
            "key": key,
            "type": k_type_str,
            "ttl": k_ttl,
            "value": val,
        }
    except Exception as e:
        logger.error(f"Redis key detail error: {e}")
        return {"error": str(e)}


@app.delete("/v1/redis/key")
async def redis_delete_key(request: Request, key: str):
    """Delete a specific key from Redis."""
    if not redis_client or not key:
        return {"error": "Key is required"}

    try:
        deleted = await redis_client.delete(key)
        return {"status": "success", "key": key, "deleted": deleted}
    except Exception as e:
        logger.error(f"Redis delete key error: {e}")
        return {"error": str(e)}


# ── Knowledge Base Ingestion Endpoints ────────────────────────────────


@app.post("/v1/knowledge/upload")
async def kb_upload(request: Request, kb_id: str, file: UploadFile = File(...)):
    """Upload a file (.pdf, .txt, .md) and index it into the specified KB."""
    # require_auth(request)

    if not file.filename:
        return JSONResponse({"error": "No filename provided"}, status_code=400)

    ext = file.filename.lower().split(".")[-1]
    if ext not in ("pdf", "txt", "md"):
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


@app.post("/v1/knowledge/text")
async def kb_text(request: Request):
    """Ingest a raw text block into the specified KB."""
    # require_auth(request)

    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        content = body.get("content")
        title = body.get("title")

        if not kb_id or not content:
            return JSONResponse(
                {"error": "kb_id and content are required"}, status_code=400
            )

        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_text

        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_text(kb, kb_id, content, title=title, source_type="text")
        await kb.close()

        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB text ingest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/knowledge/url")
async def kb_url(request: Request):
    """Fetch a URL, extract text, and index it into the specified KB."""
    # require_auth(request)

    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        url = body.get("url")

        if not kb_id or not url:
            return JSONResponse(
                {"error": "kb_id and url are required"}, status_code=400
            )

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


@app.get("/v1/knowledge/list")
async def kb_list(request: Request):
    """List distinct KB IDs available in the database."""
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase

        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        kb = PostgresKnowledgeBase(dsn)
        pool = await kb._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT kb_id FROM kb_pages ORDER BY kb_id"
            )
            kbs = [r["kb_id"] for r in rows]
        await kb.close()
        return {"status": "success", "kbs": kbs}
    except Exception as e:
        logger.error(f"KB list error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/v1/knowledge/{page_id}")
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


@app.delete("/v1/knowledge/by-kb/{kb_id}")
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


# ──────────────────────────────────────────────
# ORG CONFIGS MANAGEMENT (FOR MANTRAASSIST)
# ──────────────────────────────────────────────

@app.get("/v1/org-configs")
async def list_org_configs(request: Request):
    """List all org configs, optionally filtered by org_id."""
    org_id = request.query_params.get("org_id")
    try:
        conn = await get_db_connection()
        if org_id:
            rows = await conn.fetch("SELECT * FROM org_configs WHERE org_id = $1 ORDER BY created_at DESC", org_id)
        else:
            rows = await conn.fetch("SELECT * FROM org_configs ORDER BY created_at DESC")
        await conn.close()
        
        results = [dict(row) for row in rows]
        # Convert datetime objects and lists/dicts to JSON serializable formats
        for r in results:
            if r.get('created_at'): r['created_at'] = r['created_at'].isoformat()
            if r.get('updated_at'): r['updated_at'] = r['updated_at'].isoformat()
            if r.get('id'): r['id'] = str(r['id'])
            if r.get('transfer_numbers') and isinstance(r['transfer_numbers'], str):
                try: r['transfer_numbers'] = json.loads(r['transfer_numbers'])
                except: pass
        
        return {"status": "success", "count": len(results), "org_configs": results}
    except Exception as e:
        logger.error(f"Failed to list org configs: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/v1/org-configs/{phone_number}")
async def get_org_config(phone_number: str):
    """Get a specific org config by phone number."""
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        # Check with and without plus
        row = await conn.fetchrow(
            "SELECT * FROM org_configs WHERE phone_number IN ($1, $2)", 
            phone_number, clean_number
        )
        await conn.close()
        
        if not row:
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        result = dict(row)
        if result.get('created_at'): result['created_at'] = result['created_at'].isoformat()
        if result.get('updated_at'): result['updated_at'] = result['updated_at'].isoformat()
        if result.get('id'): result['id'] = str(result['id'])
        if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
            try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
            except: pass
            
        return {"status": "success", "org_config": result}
    except Exception as e:
        logger.error(f"Failed to get org config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/v1/org-configs/{phone_number}")
async def update_org_config(phone_number: str, request: Request):
    """Update fields on an existing org config."""
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
        
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        
        # Check if exists
        row = await conn.fetchrow(
            "SELECT id FROM org_configs WHERE phone_number IN ($1, $2)", 
            phone_number, clean_number
        )
        if not row:
            await conn.close()
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        # Build dynamic update query
        update_fields = []
        values = [row['id']]
        idx = 2
        
        allowed_fields = [
            "name", "prompt", "voice", "model", "kb_tags", 
            "transfer_numbers", "client_name", "process_id", "is_active"
        ]
        
        for field in allowed_fields:
            if field in payload:
                val = payload[field]
                if field == "transfer_numbers" and isinstance(val, dict):
                    val = json.dumps(val)
                
                update_fields.append(f"{field} = ${idx}")
                values.append(val)
                idx += 1
                
        if not update_fields:
            await conn.close()
            return {"status": "success", "message": "No valid fields to update"}
            
        update_fields.append(f"updated_at = NOW()")
        
        query = f"UPDATE org_configs SET {', '.join(update_fields)} WHERE id = $1 RETURNING *"
        updated_row = await conn.fetchrow(query, *values)
        await conn.close()
        
        result = dict(updated_row)
        if result.get('created_at'): result['created_at'] = result['created_at'].isoformat()
        if result.get('updated_at'): result['updated_at'] = result['updated_at'].isoformat()
        if result.get('id'): result['id'] = str(result['id'])
        if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
            try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
            except: pass
            
        return {"status": "success", "org_config": result}
    except Exception as e:
        logger.error(f"Failed to update org config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/v1/org-configs/{phone_number}")
async def delete_org_config(phone_number: str):
    """Soft delete an org config by setting is_active = false."""
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        
        row = await conn.fetchrow(
            "UPDATE org_configs SET is_active = false, updated_at = NOW() WHERE phone_number IN ($1, $2) RETURNING id", 
            phone_number, clean_number
        )
        await conn.close()
        
        if not row:
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        return {"status": "success", "message": f"Org config for {phone_number} deactivated"}
    except Exception as e:
        logger.error(f"Failed to delete org config: {e}")
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

            asyncio.run(
                send_crash_email(
                    service_name="Mantra UI Server (Core/Startup)",
                    error=e,
                    context_data={
                        "Status": "Crashloop / Process Death",
                        "PID": os.getpid(),
                    },
                )
            )
        except Exception as email_err:
            logger.error(f"Failed to dispatch core crash email: {email_err}")
        raise


if __name__ == "__main__":
    main()
