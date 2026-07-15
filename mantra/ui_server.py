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
from fastapi import FastAPI, Request, Response
import hmac
import base64
from urllib.parse import urlencode, HTTPException, File, UploadFile, Form
from prometheus_fastapi_instrumentator import Instrumentator
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from mantra.email_alerts import send_crash_email
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

from livekit.protocol import sip as proto_sip
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
lk_client: api.LiveKitAPI = (
    None  # Direct — used for Twilio, Zadarma, and general operations
)
plivo_client: api.LiveKitAPI = None  # Proxied — used for Plivo (India routing)
plivo_session: aiohttp.ClientSession = (
    None  # Owned session for plivo_client; closed manually on shutdown
)
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
            logger.info(
                "Creating Plivo LiveKit client without proxy (PLIVO_PROXY not set)"
            )
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


@app.get("/network")
async def network_page():
    """Serve the network monitoring page."""
    return FileResponse(os.path.join(STATIC_DIR, "network.html"))


@app.get("/kb-chat")
async def kb_chat_page():
    """Serve the Knowledge Base text chat tester."""
    return FileResponse(os.path.join(STATIC_DIR, "kb_chat.html"))


@app.get("/health")
async def health():
    """Simple health check."""
    return {"status": "ok", "service": "ui_server"}


@app.post("/api/v1/kb/chat")
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

        client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages
        )

        ai_message = response.choices[0].message.content

        return JSONResponse(
            {"status": "success", "reply": ai_message, "context": formatted_context}
        )
    except Exception as e:
        logger.error(f"KB Chat error: {e}\\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/kb/ingest")
async def ingest_kb_data(
    file: UploadFile = File(...),
    org_id: str = Form(...),
    process_id: str = Form(None),
    stage_id: str = Form(None),
    tags_name: str = Form(None),
    category_name: str = Form(None)
):
    """
    Ingest endpoint for MantraAssist KB data.
    Receives a file and metadata, uploads the file to S3 (if credentials exist), 
    and stores its content via Vectorless FTS in PostgreSQL.
    
    The `tags_name` parameter can accept a single tag or a comma-separated 
    list of tags (e.g. "sales, support"). These are parsed into a JSONB array 
    and appended to the chunk's `page_meta` for runtime filtering.
    """
    from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file

    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )

    s3_bucket = os.getenv("AWS_BUCKET_NAME")
    s3_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    s3_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    s3_region = os.getenv("AWS_REGION", "us-east-1")

    # Upload to S3 if configured
    s3_url = None
    if s3_bucket and s3_access_key and s3_secret_key:
        try:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=s3_access_key,
                aws_secret_access_key=s3_secret_key,
                region_name=s3_region
            )
            # Create a unique key using timestamp and org_id
            s3_key = f"kb/{org_id}/{int(time.time())}_{file.filename}"
            s3_client.upload_fileobj(file.file, s3_bucket, s3_key)
            # Reset file pointer for the next step
            await file.seek(0)
            s3_url = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{s3_key}"
            logger.info(f"Successfully uploaded {file.filename} to {s3_url}")
        except (NoCredentialsError, PartialCredentialsError) as e:
            logger.error(f"S3 credentials error: {e}")
            return JSONResponse({"error": "S3 configuration error"}, status_code=500)
        except Exception as e:
            logger.error(f"S3 upload error: {e}")
            return JSONResponse({"error": f"Failed to upload to S3: {str(e)}"}, status_code=500)

    try:
        # Read the file contents for PostgreSQL ingestion
        file_bytes = await file.read()
        
        # Parse tags_name into a list if it contains commas
        parsed_tags = None
        if tags_name:
            parsed_tags = [t.strip() for t in tags_name.split(",")] if "," in tags_name else [tags_name.strip()]

        # Build the metadata dictionary
        page_meta = {
            "process_id": process_id,
            "stage_id": stage_id,
            "tags_name": parsed_tags,
            "category_name": category_name,
            "s3_url": s3_url
        }
        # Filter out None values to keep JSONB clean
        page_meta = {k: v for k, v in page_meta.items() if v is not None}

        # Initialize KB and ingest
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_file(
            kb=kb,
            kb_id=org_id,
            file_bytes=file_bytes,
            filename=file.filename,
            page_meta=page_meta
        )
        await kb.close()
        
        return JSONResponse({
            "status": "success",
            "message": "Data successfully ingested into Knowledge Base and S3.",
            "details": result
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"KB ingest error: {e}\\n{traceback.format_exc()}")
        return JSONResponse({"error": f"Failed to ingest to DB: {str(e)}"}, status_code=500)


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

    # Generate a unique room name for this test session using the call_id if provided
    call_id = payload.get("call_id") or int(time.time())
    room_name = f"test_{call_id}"

    try:
        # Create dispatch with payload as metadata
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name, agent_name="mantra-agent", metadata=json.dumps(payload)
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
        
    sip_domain = _get_sip_domain()
        
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


def _get_sip_domain() -> str:
    lk_url = os.getenv("LIVEKIT_URL", "")
    host_lk = lk_url.replace("wss://", "").replace("ws://", "")
    if "mantraassist-0ek43ife" in host_lk:
        return "4mp2ouvchg3.india.sip.livekit.cloud"
    elif "livekit.cloud" in host_lk:
        subdomain = host_lk.split(".")[0]
        return f"{subdomain}.sip.livekit.cloud"
    return "4mp2ouvchg3.india.sip.livekit.cloud"


async def _update_zadarma_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Updates the SIP URI forwarding in Zadarma using their REST API.
    Handles the HMAC-SHA1 + MD5 signature required by Zadarma.
    """
    zadarma_key = os.getenv("ZADARMA_API_KEY")
    zadarma_secret = os.getenv("ZADARMA_API_SECRET")
    
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


@app.post("/api/v1/sip/inbound/setup")
async def setup_inbound_sip(request: Request):
    """
    End-to-end inbound SIP setup:
    1. Creates LiveKit Inbound Trunk
    2. Creates LiveKit Dispatch Rule
    3. Triggers Zadarma API to update the forwarding URI
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        
    number = payload.get("number")
    if not number:
        return JSONResponse({"error": "number is required"}, status_code=400)
        
    name = payload.get("name", f"Inbound {number}")
    prompt = payload.get("prompt", "You are a helpful voice assistant.")
    voice = payload.get("voice", "arushi")
    model = payload.get("model", "deepseek")
    
    logger.info(f"Starting end-to-end SIP setup for number: {number}")
    
    try:
        # 1. Create Inbound Trunk
        # Add both exact number and + stripped number so LiveKit definitely matches the To header
        clean_number = number.replace("+", "")
        trunk = await lk_client.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name=name,
                    numbers=[number, clean_number],
                )
            )
        )
        trunk_id = trunk.sip_trunk_id
        logger.info(f"Created LiveKit SIP Inbound Trunk: {trunk_id}")
        
        # 2. Create Dispatch Rule
        # We inject direction=inbound and the given prompt/voice into metadata
        room_prefix = f"inbound_{trunk_id[-6:]}"
        metadata_dict = {
            "direction": "inbound",
            "prompt": prompt,
            "voice": voice,
            "model": model,
            "phone_number": number
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
                        agent_name="mantra-agent",
                        metadata=json.dumps(metadata_dict)
                    )
                ]
            ),
            trunk_ids=[trunk_id]
        )
        
        rule = await lk_client.sip.create_sip_dispatch_rule(rule_req)
        rule_id = rule.sip_dispatch_rule_id
        logger.info(f"Created LiveKit SIP Dispatch Rule: {rule_id}")
        
        # 3. Generate SIP URI
        sip_domain = _get_sip_domain()
        # Use the clean_number so that Zadarma sends the INVITE with To: <clean_number>@<sip_domain>
        # This allows LiveKit to correctly match the inbound SIP trunk which has this number in its numbers array.
        sip_uri = f"sip:{clean_number}@{sip_domain}"
        
        # 4. Update Zadarma
        logger.info(f"Updating Zadarma SIP ID for {number} to {sip_uri}")
        zadarma_response = await _update_zadarma_sip_forwarding(number, sip_uri)
        
        return JSONResponse({
            "status": "success",
            "name": name,
            "sip_trunk_id": trunk_id,
            "sip_dispatch_rule_id": rule_id,
            "sip_uri": sip_uri,
            "zadarma_response": zadarma_response
        })
        
    except Exception as e:
        logger.error(f"Error during SIP setup: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)



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
    logger.info(
        f"Webhook received call request for event {event_name}: {json.dumps(payload, separators=(',', ':'))}"
    )

    # Use call_id or voice_id from payload if available, otherwise use timestamp
    call_id = (
        payload.get("call_id")
        or payload.get("voice_id")
        or payload.get("event_id")
        or int(time.time())
    )
    room_name = f"call_{call_id}"

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
    logger.info(f"Provider detected: {provider}")

    # Trigger agent dispatch — always use lk_client (direct, no proxy)
    # LiveKit Cloud API calls don't need the Indian proxy; region pinning is on the trunk itself
    try:
        logger.info(f"Step 1: Creating agent dispatch for room {room_name}")
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name, agent_name="mantra-agent", metadata=json.dumps(payload)
            )
        )
        logger.info(f"Dispatch created: {dispatch.id}")
    except Exception as e:
        logger.error(f"Agent dispatch failed: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            {"error": f"Agent dispatch failed: {str(e)}"}, status_code=500
        )

    # Trigger SIP outbound call in background to prevent webhook timeouts
    async def trigger_sip():
        try:
            sip_number = payload.get("call_from")
            if sip_number and not sip_number.startswith("+"):
                sip_number = f"+{sip_number}"

            sip_client = (
                plivo_client if provider == "plivo" and plivo_client else lk_client
            )
            proxy_msg = (
                "proxied Plivo client"
                if sip_client == plivo_client
                else "direct LiveKit client"
            )
            logger.info(
                f"Step 2: Initiating SIP call to {phone_number} via trunk {trunk_id} using {proxy_msg}"
                + (f" (Caller ID: {sip_number})" if sip_number else "")
            )

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
            logger.info(f"SIP Participant created: {sip_part.participant_identity}")
        except Exception as e:
            logger.error(
                f"SIP Call trigger failed for {room_name}: {e}\n{traceback.format_exc()}"
            )

            # Store exact SIP failure reason in Redis for the agent to read
            if redis_client:
                err_str = str(e).lower()
                if any(token in err_str for token in ("408", "timeout", "no answer")):
                    status_guess = "No Answer"
                elif any(
                    token in err_str
                    for token in ("486", "busy", "603", "decline", "rejected")
                ):
                    status_guess = "Busy"
                else:
                    status_guess = "Incomplete"
                try:
                    await redis_client.set(
                        f"sip_error_status:{call_id}", status_guess, ex=300
                    )
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
    token = (
        api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))
        .with_identity(f"monitor_{call_id}")
        .with_name("Call Monitor")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=False,
                can_subscribe=True,
            )
        )
    )

    return JSONResponse(
        {
            "status": "success",
            "message": f"Agent dispatched for {event_name}",
            "client_name": payload.get("client_name", "Unknown"),
            "purpose": payload.get("prompt", "Voice interaction")[:100]
            + ("..." if len(payload.get("prompt", "")) > 100 else ""),
            "room": room_name,
            "token": token.to_jwt(),
            "url": os.getenv("LIVEKIT_URL"),
        }
    )


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

    logger.info(
        f"[POST /api/v1/sip/trunks/outbound] Payload received: {json.dumps(payload, separators=(',', ':'))}"
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


@app.post("/api/v1/sip/trunks/outbound/twilio")
async def create_twilio_sip_trunk(request: Request):
    """
    Create a new Twilio SIP trunk using professional nomenclature.
    Aligns with LiveKit CLI parameters: auth_user, auth_pass.
    """
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)

    logger.info(
        f"[POST /api/v1/sip/trunks/outbound/twilio] Payload received: {json.dumps(payload, separators=(',', ':'))}"
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

        # 3. Trigger Agent Dispatch — use direct client (no proxy needed for LiveKit Cloud)
        call_id = payload.get("call_id") or payload.get("voice_id") or int(time.time())
        room_name = f"call_{call_id}"

        logger.info(f"Dispatching agent to room {room_name}")
        await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name, agent_name="mantra-agent", metadata=json.dumps(payload)
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
    return JSONResponse({"url": os.getenv("LIVEKIT_URL")})


# ── Dashboard API (authenticated) ────────────────────────────────────────


@app.get("/api/v1/dashboard/stream")
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
                limit,
                offset,
            )

            count_row = await conn.fetchrow(
                "SELECT COUNT(*)::int AS total FROM call_logs"
            )
            total = count_row["total"] if count_row else 0
        finally:
            await conn.close()

        calls = []
        for row in rows:
            cl = row["call_log"] if isinstance(row["call_log"], dict) else {}
            calls.append(
                {
                    "call_id": row["call_id"],
                    "status": row["status"],
                    "recording_url": row["recording_url"] or "",
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "client_name": cl.get("client_name") or cl.get("client_id") or "",
                    "client_phone": cl.get("client_phone") or "",
                    "duration": cl.get("call_duration_seconds"),
                    "summary": cl.get("ai_summary") or "",
                    "purpose": (cl.get("prompt") or "")[:120],
                }
            )

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


# ── Knowledge Base Ingestion Endpoints ────────────────────────────────


@app.post("/api/v1/knowledge/upload")
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


@app.post("/api/v1/knowledge/url")
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


@app.get("/api/v1/knowledge/list")
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
