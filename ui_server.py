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
