"""Persistent service clients (LiveKit API, Redis, HTTP) for the UI server."""
import logging
import os

import httpx
import redis.asyncio as redis
from livekit import api

logger = logging.getLogger("mantra.clients")

AGENT_NAME = os.getenv("AGENT_NAME", "mantra-agent")

# Persistent LiveKit API client for self-hosted LiveKit server
lk_client: api.LiveKitAPI = None
# Backward compatibility aliases pointing to unified lk_client
plivo_client: api.LiveKitAPI = None
voicelink_client: api.LiveKitAPI = None

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


async def init_clients():
    """Construct all persistent clients. Called once at app startup."""
    global lk_client, plivo_client, voicelink_client, redis_client, http_client
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")

    if lk_url.startswith("wss://"):
        api_url = lk_url.replace("wss://", "https://")
    elif lk_url.startswith("ws://"):
        api_url = lk_url.replace("ws://", "http://")
    else:
        api_url = lk_url

    logger.info(f"Connecting to self-hosted LiveKit API at {api_url}")
    lk_client = api.LiveKitAPI(url=api_url, api_key=api_key, api_secret=api_secret)
    # Set aliases for smooth backward compatibility
    plivo_client = lk_client
    voicelink_client = lk_client

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


async def close_clients():
    """Close all persistent clients. Called once at app shutdown."""
    if lk_client:
        await lk_client.aclose()
    if http_client:
        await http_client.aclose()