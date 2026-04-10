import os
import uuid
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from livekit import api
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env and .env.local
load_dotenv()
load_dotenv(".env.local")

app = FastAPI(title="LiveKit Voice Agent Control Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def get_frontend():
    return FileResponse("static/index.html")

class SessionResponse(BaseModel):
    room: str
    token: str
    url: str

@app.post("/session/start", response_model=SessionResponse)
async def start_session():
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not livekit_url or not api_key or not api_secret:
        print(f"ERROR: Missing credentials. URL: {bool(livekit_url)}, Key: {bool(api_key)}, Secret: {bool(api_secret)}")
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    # Generate a unique room name and participant identity
    room_name = f"room-{uuid.uuid4().hex[:8]}"
    participant_identity = f"user-{uuid.uuid4().hex[:8]}"
    
    # Generate token with appropriate permissions
    token = api.AccessToken(api_key, api_secret) \
        .with_identity(participant_identity) \
        .with_name("Web User") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_publish_data=True,
            can_subscribe=True
        ))

    # Explicitly dispatch the agent to the room
    # This is the "Control Plane" way to ensure the agent joins
    try:
        # Convert wss:// to https:// for API calls
        api_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")
        async with api.LiveKitAPI(api_url, api_key, api_secret) as lkapi:
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name="massist",
                    room=room_name
                )
            )
        print(f"Agent 'massist' dispatched to room '{room_name}'")
    except Exception as e:
        print(f"Warning: Failed to dispatch agent: {e}")
        # We don't fail the request here, as the user might still join
        # and the agent might be triggered via other rules if they exist.

    return SessionResponse(
        room=room_name,
        token=token.to_jwt(),
        url=livekit_url
    )
