""" Redis inspector API routes for the UI server. """

from fastapi import APIRouter, Request
from mantra.services import clients as _svc_clients
import json

import logging

logger = logging.getLogger("mantra.redis_api")
router = APIRouter()

@router.get("/v1/redis/info")
async def redis_info(request: Request):
    """Get Redis server telemetry & summary statistics."""
    if not _svc_clients.redis_client:
        return {"error": "Redis client not connected", "status": "disconnected"}

    try:
        raw_info = await _svc_clients.redis_client.info()
        pending_count = await _svc_clients.redis_client.zcard("queue:pending")
        active_count = await _svc_clients.redis_client.hlen("calls:active")
        db_size = await _svc_clients.redis_client.dbsize()

        return {
            "status": "online",
            "redis_version": raw_info.get("redis_version", "N/A"),
            "used_memory_human": raw_info.get("used_memory_human", "N/A"),
            "used_memory_peak_human": raw_info.get("used_memory_peak_human", "N/A"),
            "connected_clients": raw_info.get("connected_clients", 0),
            "uptime_in_seconds": raw_info.get("uptime_in_seconds", 0),
            "uptime_in_days": raw_info.get("uptime_in_days", 0),
            "total_commands_processed": raw_info.get("total_commands_processed", 0),
            "instantaneous_ops_per_sec": raw_info.get("instantaneous_ops_per_sec", 0),
            "total_keys": db_size,
            "queue_pending_count": pending_count,
            "active_calls_count": active_count,
        }
    except Exception as e:
        logger.error(f"Redis info error: {e}")
        return {"error": str(e), "status": "error"}



@router.get("/v1/redis/queue")
async def redis_queue_items(request: Request):
    """Fetch all pending jobs in the queue:pending sorted set."""
    if not _svc_clients.redis_client:
        return {"error": "Redis client not connected", "items": []}

    try:
        raw_items = await _svc_clients.redis_client.zrange("queue:pending", 0, -1, withscores=True)
        items = []
        for rank, (payload_str, score) in enumerate(raw_items):
            parsed_payload = {}
            call_id = "N/A"
            client_name = "N/A"
            phone = "N/A"
            try:
                parsed_payload = json.loads(payload_str)
                call_id = str(parsed_payload.get("call_id", "N/A"))
                client_name = parsed_payload.get("client_name", "N/A")
                phone = parsed_payload.get("phone_number") or parsed_payload.get("client_phone") or "N/A"
            except Exception:
                pass

            items.append({
                "rank": rank + 1,
                "score": score,
                "call_id": call_id,
                "client_name": client_name,
                "phone": phone,
                "raw_payload": payload_str,
                "parsed_payload": parsed_payload
            })

        return {"count": len(items), "items": items}
    except Exception as e:
        logger.error(f"Redis queue error: {e}")
        return {"error": str(e), "items": []}



@router.get("/v1/redis/active-details")
async def redis_active_details(request: Request):
    """Fetch active calls hash and their detailed status in Redis."""
    if not _svc_clients.redis_client:
        return {"error": "Redis client not connected", "calls": []}

    try:
        active_map = await _svc_clients.redis_client.hgetall("calls:active")
        calls = []
        for call_id, room_name in active_map.items():
            status = await _svc_clients.redis_client.get(f"calls:status:{call_id}")
            lock_ttl = await _svc_clients.redis_client.ttl(f"lock:call:{call_id}")
            sip_status = await _svc_clients.redis_client.get(f"sip_error_status:{call_id}")

            calls.append({
                "call_id": str(call_id),
                "room_name": room_name,
                "status": status or "in_progress",
                "lock_ttl": lock_ttl if lock_ttl > 0 else 0,
                "sip_error_status": sip_status or None,
            })
        return {"count": len(calls), "calls": calls}
    except Exception as e:
        logger.error(f"Redis active calls error: {e}")
        return {"error": str(e), "calls": []}



@router.get("/v1/redis/keys")
async def redis_keys_list(request: Request, pattern: str = "*", limit: int = 100):
    """Scan and list keys matching pattern with type and TTL."""
    if not _svc_clients.redis_client:
        return {"error": "Redis client not connected", "keys": []}

    try:
        matched_keys = []
        cursor = "0"
        count = 0
        while True:
            cursor, keys = await _svc_clients.redis_client.scan(cursor=cursor, match=pattern, count=100)
            for k in keys:
                matched_keys.append(k)
                count += 1
                if count >= limit:
                    break
            if cursor == "0" or cursor == 0 or count >= limit:
                break

        key_details = []
        for k in matched_keys[:limit]:
            k_type = await _svc_clients.redis_client.type(k)
            k_ttl = await _svc_clients.redis_client.ttl(k)
            key_details.append({
                "key": k,
                "type": k_type.upper() if isinstance(k_type, str) else "UNKNOWN",
                "ttl": k_ttl,
            })

        return {"pattern": pattern, "total_found": len(key_details), "keys": key_details}
    except Exception as e:
        logger.error(f"Redis keys error: {e}")
        return {"error": str(e), "keys": []}



@router.get("/v1/redis/key-detail")
async def redis_key_detail(request: Request, key: str):
    """Get full data of a specific Redis key regardless of type."""
    if not _svc_clients.redis_client or not key:
        return {"error": "Key parameter is required"}

    try:
        k_type = await _svc_clients.redis_client.type(key)
        k_ttl = await _svc_clients.redis_client.ttl(key)
        k_type_str = k_type.upper() if isinstance(k_type, str) else "UNKNOWN"

        val = None
        if k_type_str == "STRING":
            val = await _svc_clients.redis_client.get(key)
        elif k_type_str == "HASH":
            val = await _svc_clients.redis_client.hgetall(key)
        elif k_type_str == "ZSET":
            val = await _svc_clients.redis_client.zrange(key, 0, -1, withscores=True)
        elif k_type_str == "LIST":
            val = await _svc_clients.redis_client.lrange(key, 0, -1)
        elif k_type_str == "SET":
            val = list(await _svc_clients.redis_client.smembers(key))
        else:
            val = str(await _svc_clients.redis_client.get(key))

        return {
            "key": key,
            "type": k_type_str,
            "ttl": k_ttl,
            "value": val,
        }
    except Exception as e:
        logger.error(f"Redis key detail error: {e}")
        return {"error": str(e)}



@router.delete("/v1/redis/key")
async def redis_delete_key(request: Request, key: str):
    """Delete a specific key from Redis."""
    if not _svc_clients.redis_client or not key:
        return {"error": "Key is required"}

    try:
        deleted = await _svc_clients.redis_client.delete(key)
        return {"status": "success", "key": key, "deleted": deleted}
    except Exception as e:
        logger.error(f"Redis delete key error: {e}")
        return {"error": str(e)}


# ── Knowledge Base Ingestion Endpoints ────────────────────────────────



