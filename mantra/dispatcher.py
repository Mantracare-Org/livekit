import os
import json
import time
import asyncio
import logging
import traceback
from dotenv import load_dotenv

import redis.asyncio as redis
from livekit import api

# Load environment variables
load_dotenv(".env.local")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mantra.dispatcher")

# Limits
CARTESIA_MAX_CONCURRENCY = int(os.getenv("CARTESIA_MAX_CONCURRENCY"))
LIVEKIT_MAX_ROOMS = int(os.getenv("LIVEKIT_MAX_ROOMS"))
AGENT_MAX_WORKERS = int(os.getenv("AGENT_MAX_WORKERS"))

redis_url = os.getenv("REDIS_URL")

async def get_lk_client():
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
        return api.LiveKitAPI(url=api_url, api_key=api_key, api_secret=api_secret)
    return None

async def dispatch_call(lk_client: api.LiveKitAPI, payload: dict):
    call_id = payload.get("call_id") or payload.get("voice_id")
    room_name = payload.get("_resolved_room_name", f"call_{call_id}")
    phone_number = payload.get("_resolved_phone_number")
    trunk_id = payload.get("_resolved_trunk_id")
    sip_number = payload.get("_resolved_sip_number")

    try:
        logger.info(f"[Call {call_id}] Creating agent dispatch for room {room_name}")
        dispatch = await lk_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="mantra-agent",
                metadata=json.dumps(payload)
            )
        )
        logger.info(f"[Call {call_id}] Dispatch created: {dispatch.id}")
    except Exception as e:
        logger.error(f"[Call {call_id}] Agent dispatch failed: {e}\n{traceback.format_exc()}")
        raise e

    try:
        logger.info(f"[Call {call_id}] Initiating SIP call to {phone_number} via trunk {trunk_id}")
        sip_part = await lk_client.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                sip_number=sip_number,
                room_name=room_name,
                participant_identity=f"sip_{call_id}",
                participant_name="Mantra Voice",
                play_ringtone=False,
                wait_until_answered=True
            )
        )
        logger.info(f"[Call {call_id}] SIP Participant created: {sip_part.participant_identity}")
    except Exception as e:
        logger.error(f"[Call {call_id}] SIP Call trigger failed: {e}\n{traceback.format_exc()}")
        raise e

async def cleanup_zombies(redis_client, lk_client):
    """Periodic check to remove rooms from 'calls:active' that no longer exist in LiveKit."""
    try:
        active_calls = await redis_client.hgetall("calls:active")
        if not active_calls:
            return

        response = await lk_client.room.list_rooms(api.ListRoomsRequest())
        active_rooms = {room.name for room in response.rooms}

        for call_id, room_name in active_calls.items():
            if room_name not in active_rooms:
                logger.warning(f"Zombie detected! Room {room_name} not found in LiveKit. Removing call {call_id} from active.")
                await redis_client.hdel("calls:active", call_id)
                await redis_client.set(f"calls:status:{call_id}", "completed_or_failed_zombie")
    except Exception as e:
        logger.error(f"Zombie cleanup failed: {e}")

async def main():
    redis_client = redis.from_url(redis_url, decode_responses=True)
    await redis_client.ping()
    logger.info(f"Dispatcher connected to Redis at {redis_url}")

    lk_client = await get_lk_client()
    if not lk_client:
        logger.error("LiveKit credentials not found. Exiting.")
        return

    logger.info(f"Dispatcher started. Limits: Cartesia={CARTESIA_MAX_CONCURRENCY}, Agent={AGENT_MAX_WORKERS}, LiveKit={LIVEKIT_MAX_ROOMS}")

    last_zombie_check = time.time()

    try:
        while True:
            try:
                # 1. Periodic zombie sweep
                now = time.time()
                if now - last_zombie_check > 60:
                    await cleanup_zombies(redis_client, lk_client)
                    last_zombie_check = now

                # 2. Check Capacity
                active_count = await redis_client.hlen("calls:active")
                available_capacity = min(
                    CARTESIA_MAX_CONCURRENCY - active_count,
                    AGENT_MAX_WORKERS - active_count,
                    LIVEKIT_MAX_ROOMS - active_count
                )

                if available_capacity > 0:
                    # 3. Dequeue highest priority call
                    # zpopmin returns a list of tuples: [(member, score)]
                    popped = await redis_client.zpopmin("queue:pending")
                    if popped:
                        call_entry, score = popped[0]
                        payload = json.loads(call_entry)
                        call_id = payload.get("call_id") or payload.get("voice_id")
                        room_name = payload.get("_resolved_room_name", f"call_{call_id}")

                        logger.info(f"Dequeued call {call_id}. Capacity before: {active_count}. Available: {available_capacity}")

                        # 4. State Update
                        await redis_client.hset("calls:active", call_id, room_name)
                        await redis_client.set(f"calls:status:{call_id}", "dispatching")

                        # 5. Dispatch
                        try:
                            await dispatch_call(lk_client, payload)
                            await redis_client.set(f"calls:status:{call_id}", "in_progress")
                        except Exception as e:
                            # Re-queue on failure and free up capacity
                            logger.error(f"Failed to dispatch {call_id}. Re-queueing...")
                            await redis_client.hdel("calls:active", call_id)
                            # Increment score to push it back slightly, or keep same score
                            await redis_client.zadd("queue:pending", {call_entry: score + 10})
                            await redis_client.set(f"calls:status:{call_id}", "failed_dispatch_requeued")
            except Exception as e:
                logger.error(f"Dispatcher loop error: {e}")
            
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        await lk_client.aclose()
        await redis_client.aclose()
        logger.info("Dispatcher shutdown gracefully.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Dispatcher stopped by user.")
