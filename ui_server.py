import os
import logging
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

@app.route("/token")
def get_token():
    """Generate a signed LiveKit JWT for the participant."""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not api_key or not api_secret:
        return jsonify({"error": "LiveKit credentials not found in .env.local"}), 500

    token = api.AccessToken(api_key, api_secret) \
        .with_identity("LocalUser") \
        .with_name("Local User") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room_create=True,
            room="default-room",
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
    
    # EXPLICIT DISPATCH: Force the agent to join the room
    async def trigger_dispatch():
        try:
            lkapi = api.LiveKitAPI(api_key=api_key, api_secret=api_secret)
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room="default-room",
                    agent_name="mantra-agent"
                )
            )
            await lkapi.aclose()
        except Exception as e:
            print(f"Dispatch failed: {e}")

    # Run the dispatch trigger in the background
    asyncio.run(trigger_dispatch())

    return jsonify({
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
