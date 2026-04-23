import os
import logging
import json
import time
import asyncio
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from livekit import api
from dotenv import load_dotenv

# Load environment variables from .env.local
load_dotenv(".env.local")

app = FastAPI()
logger = logging.getLogger("ui_server")
logger.setLevel(logging.INFO)

# Mount static files
# We will serve index.html separately so we can mount static for assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Persistent LiveKit API client — created once, reused across requests
lk_client: api.LiveKitAPI = None

@app.on_event("startup")
async def startup_event():
    global lk_client
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
        lk_client = api.LiveKitAPI(
            url=api_url,
            api_key=api_key,
            api_secret=api_secret
        )

@app.on_event("shutdown")
async def shutdown_event():
    global lk_client
    if lk_client:
        await lk_client.aclose()

@app.get("/")
async def index():
    """Serve the main UI."""
    return FileResponse(os.path.join("static", "index.html"))

@app.post("/dispatch-test")
async def dispatch_test(request: Request):
    """
    Manually trigger an agent dispatch with a custom payload.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Manual dispatch request with payload: {json.dumps(payload, indent=2)}")
    
    # Generate a unique room name for this test session
    room_name = f"test_{int(time.time())}"
    
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


@app.post("/webhook/{event_name}")
async def create_call_webhook(event_name: str, request: Request):
    """
    Webhook to trigger an agent dispatch for a telephony call.
    Expects a JSON payload with call context.
    """
    payload = await request.json()

    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
    
    logger.info(f"Webhook received call request for event {event_name}: {json.dumps(payload, indent=2)}")
    
    # Use call_id from payload if available, otherwise use timestamp
    call_id = payload.get("call_id") or payload.get("event_id") or int(time.time())
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

    # Trigger agent dispatch
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

    # Trigger SIP outbound call
    try:
        trunk_id = os.getenv("SIP_TRUNK_ID")
        logger.info(f"Step 2: Initiating SIP call to {phone_number} via trunk {trunk_id}")
        sip_part = await lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
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


@app.get("/config")
async def get_config():
    """Return the LiveKit URL for the frontend."""
    return JSONResponse({
        "url": os.getenv("LIVEKIT_URL")
    })

if __name__ == "__main__":
    import uvicorn
    print("UI Server starting on http://0.0.0.0:5000")
    uvicorn.run("ui_server:app", host="0.0.0.0", port=5000, reload=True)
