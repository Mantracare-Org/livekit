import os
import logging
import json
import time

from flask import Flask, jsonify, send_from_directory
import asyncio
from livekit import api
from dotenv import load_dotenv

# Load environment variables from .env.local
load_dotenv(".env.local")

app = Flask(__name__)
logger = logging.getLogger(__name__)

@app.route("/")
def index():
    """Serve the main UI."""
    return send_from_directory("static", "index.html")

@app.route("/static/<path:path>")
def static_files(path):
    """Serve static assets."""
    return send_from_directory("static", path)

@app.route("/dispatch-test", methods=["POST"])
def dispatch_test():
    """
    Manually trigger an agent dispatch with a custom payload.
    """
    from flask import request
    payload = request.json

    if not payload:
        return jsonify({"error": "No payload provided"}), 400
    
    logger.info(f"Manual dispatch request with payload: {json.dumps(payload, indent=2)}")
    
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    # Generate a unique room name for this test session
    import time
    room_name = f"test_{int(time.time())}"
    
    async def trigger_dispatch():
        try:
            lkapi = api.LiveKitAPI(
                url=os.getenv("LIVEKIT_URL"),
                api_key=api_key,
                api_secret=api_secret
            )
            # Create dispatch with payload as metadata
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name,
                    agent_name="mantra-agent",
                    metadata=json.dumps(payload)
                )
            )
            await lkapi.aclose()
            logger.info(f"Successfully dispatched agent to room {room_name}")
        except Exception as e:
            logger.error(f"Dispatch failed: {e}")
            raise e

    try:
        asyncio.run(trigger_dispatch())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Generate token for the user to join the same room
    token = api.AccessToken(api_key, api_secret) \
        .with_identity("Tester") \
        .with_name("Manual Tester") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))

    return jsonify({
        "status": "success",
        "room": room_name,
        "token": token.to_jwt(),
        "url": os.getenv("LIVEKIT_URL"),
    })


@app.route("/webhook/<event_name>", methods=["POST"])
def create_call_webhook(event_name):
    """
    Webhook to trigger an agent dispatch for a telephony call.
    Expects a JSON payload with call context.
    """
    from flask import request
    payload = request.json

    if not payload:
        return jsonify({"error": "No payload provided"}), 400
    
    logger.info(f"Webhook received call request: {json.dumps(payload, indent=2)}")
    
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
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
        return jsonify({"error": "No client_phone provided in payload"}), 400

    async def trigger_dispatch_and_call():
        try:
            lk_url = os.getenv("LIVEKIT_URL")
            if lk_url.startswith("wss://"):
                api_url = lk_url.replace("wss://", "https://")
            elif lk_url.startswith("ws://"):
                api_url = lk_url.replace("ws://", "http://")
            else:
                api_url = lk_url

            logger.info(f"Connecting to LiveKit API at {api_url}")
            lkapi = api.LiveKitAPI(
                url=api_url,
                api_key=api_key,
                api_secret=api_secret
            )
            
            # 1. Create agent dispatch for the room
            logger.info(f"Step 1: Creating agent dispatch for room {room_name}")
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=room_name,
                    agent_name="mantra-agent",
                    metadata=json.dumps(payload)
                )
            )
            logger.info(f"Dispatch created: {dispatch.id}")
            
            # 2. Initiate SIP outbound call
            trunk_id = os.getenv("SIP_TRUNK_ID")
            logger.info(f"Step 2: Initiating SIP call to {phone_number} via trunk {trunk_id}")
            sip_part = await lkapi.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    room_name=room_name,
                    participant_identity=f"sip_{call_id}",
                    participant_name="SIP Caller"
                )
            )
            logger.info(f"SIP Participant created: {sip_part.participant_identity}")
            
            await lkapi.aclose()
            logger.info(f"Successfully triggered call and agent for room {room_name}")
        except Exception as e:
            import traceback
            logger.error(f"Failed to trigger call/dispatch: {e}\n{traceback.format_exc()}")
            if 'lkapi' in locals():
                await lkapi.aclose()
            raise e

    try:
        asyncio.run(trigger_dispatch_and_call())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Generate token for anyone needing to join/monitor the call
    token = api.AccessToken(api_key, api_secret) \
        .with_identity(f"monitor_{call_id}") \
        .with_name("Call Monitor") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=False,
            can_subscribe=True,
        ))

    return jsonify({
        "status": "success",
        "message": f"Agent dispatched for {event_name}",
        "client_name": payload.get("client_name", "Unknown"),
        "purpose": payload.get("prompt", "Voice interaction")[:100] + ("..." if len(payload.get("prompt", "")) > 100 else ""),
        "room": room_name,
        "token": token.to_jwt(),
        "url": os.getenv("LIVEKIT_URL"),
    })


@app.route("/config")
def get_config():
    """Return the LiveKit URL for the frontend."""
    return jsonify({
        "url": os.getenv("LIVEKIT_URL")
    })

if __name__ == "__main__":
    print("UI Server starting on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
