"""Persistent service clients (LiveKit API, Redis, HTTP) for the UI server."""
import logging
import os

import aiohttp
import httpx
import redis.asyncio as redis
from livekit import api

logger = logging.getLogger("mantra.clients")

AGENT_NAME = os.getenv("AGENT_NAME", "mantra-agent")

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


async def init_clients():
    """Construct all persistent clients. Called once at app startup."""
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


async def close_clients():
    """Close all persistent clients. Called once at app shutdown."""
    for client in [lk_client, plivo_client, voicelink_client]:
        if client:
            await client.aclose()
    if plivo_session:
        await plivo_session.close()
    if voicelink_session:
        await voicelink_session.close()
    if http_client:
        await http_client.aclose()