import os
import json
import time
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from livekit import api
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("trigger")
logging.basicConfig(level=logging.INFO)

lk_api: api.LiveKitAPI = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_api
    lk_url = os.getenv("LIVEKIT_URL")
    lk_key = os.getenv("LIVEKIT_API_KEY")
    lk_secret = os.getenv("LIVEKIT_API_SECRET")
    if all([lk_url, lk_key, lk_secret]):
        lk_api = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)
    else:
        logger.warning("LiveKit env vars missing — API calls will fail")
    yield
    if lk_api:
        await lk_api.aclose()

app = FastAPI(title="Mantra Trigger Service", lifespan=lifespan)

@app.post("/trigger-call")
async def trigger_call(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    for field in ["phone_number", "lead_id"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    if not lk_api:
        raise HTTPException(status_code=500, detail="LiveKit API client not initialized")

    phone_number = payload["phone_number"]
    lead_id = payload["lead_id"]
    timestamp = int(time.time())
    room_name = f"call_{lead_id}_{timestamp}"

    logger.info(f"Triggering call for lead {lead_id} to {phone_number} in room {room_name}")

    # 1. Create room first
    try:
        await lk_api.room.create_room(
            api.CreateRoomRequest(name=room_name)
        )
        logger.info(f"Room created: {room_name}")
    except Exception as e:
        logger.error(f"Failed to create room: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to create room: {str(e)}"})

    # 2. Dispatch agent
    try:
        dispatch = await lk_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(payload)
            )
        )
        logger.info(f"Agent dispatched: {dispatch.id}")
    except Exception as e:
        logger.error(f"Failed to dispatch agent: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to dispatch agent: {str(e)}"})

    # 3. Create SIP participant
    sip_trunk_id = os.getenv("LIVEKIT_SIP_TRUNK_ID")
    if not sip_trunk_id:
        return JSONResponse(status_code=500, content={"error": "LIVEKIT_SIP_TRUNK_ID not set"})

    try:
        sip_part = await lk_api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=sip_trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=f"sip_{lead_id}_{timestamp}",
                participant_name="Mantra Caller"
            )
        )
        logger.info(f"SIP participant created: {sip_part.participant_identity}")
    except Exception as e:
        logger.error(f"Failed to create SIP participant: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create SIP participant: {str(e)}", "room_name": room_name}
        )

    return {"room_name": room_name, "dispatch_id": dispatch.id, "status": "dispatched"}
