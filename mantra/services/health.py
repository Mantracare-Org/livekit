""" Dependency health checks and the per-provider capacity gate. """

from fastapi import Request, Response
from livekit import api
from mantra.dependencies.database import get_db_connection
from mantra.services.capacity import _active_call_rooms, _active_per_trunk, _extract_trunk_ids, _log_blocked_call, _resolve_trunk_limit, _trunk_at_capacity
from mantra.services import clients as _svc_clients
from mantra.services.clients import MAX_CALL_CONCURRENCY
import asyncio
import json
import os
import time

import logging

logger = logging.getLogger("mantra.health")

# ── Paths that require a clean bill of health before processing ─────────
_DISPATCH_PATHS = frozenset({
    "/dispatch-test",
    "/v1/webhooks/telephony",
    "/v1/sip/trunks/outbound",
    "/v1/sip/trunks/outbound/zadarma",
    "/v1/sip/trunks/outbound/twilio",
    "/v1/sip/trunks/outbound/plivo",
})

async def _run_dependency_checks() -> tuple[bool, dict[str, bool | str]]:
    """Run infrastructure dependency checks only (no capacity). Used by the coarse gate."""
    if os.getenv("BYPASS_HEALTH_CHECKS") == "1":
        logger.warning("BYPASS_HEALTH_CHECKS is active. Skipping all service health checks.")
        return True, {}

    checks: dict[str, bool | str] = {}

    async def _check(domain: str, coro, timeout: float = 1.0):
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            checks[domain] = True
        except Exception as e:
            checks[domain] = repr(e)

    async def _check_stt():
        key = os.getenv("DEEPGRAM_API_KEY")
        if not key:
            checks["stt_deepgram"] = "DEEPGRAM_API_KEY not set"
            return
        try:
            r = await _svc_clients.http_client.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {key}"},
            )
            checks["stt_deepgram"] = r.is_success
        except Exception as e:
            checks["stt_deepgram"] = str(e)

    async def _check_mantraassist_backend():
        url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
        if not url:
            checks["mantraassist_backend"] = "MANTRAASSIST_BACKEND_URL not set"
            return
        try:
            r = await _svc_clients.http_client.get(f"{url}/v1/health")
            if r.is_success:
                data = r.json()
                if data.get("success") is True:
                    checks["mantraassist_backend"] = True
                else:
                    checks["mantraassist_backend"] = f"Unexpected response body: {data}"
            else:
                checks["mantraassist_backend"] = f"HTTP status {r.status_code}"
        except Exception as e:
            checks["mantraassist_backend"] = str(e)

    async def _check_s3():
        bucket = os.getenv("AWS_S3_BUCKET_NAME")
        if not bucket:
            checks["s3"] = "AWS_S3_BUCKET_NAME not set"
            return
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _check_s3_bucket, bucket),
                timeout=1.0,
            )
            checks["s3"] = True
        except Exception as e:
            checks["s3"] = str(e)

    async def _check_postgres():
        conn = None
        try:
            conn = await asyncio.wait_for(get_db_connection(), timeout=1.0)
            checks["postgres"] = True
        except Exception as e:
            checks["postgres"] = repr(e)
        finally:
            if conn:
                try:
                    await conn.close()
                except:
                    pass

    async def _check_redis():
        if not _svc_clients.redis_client:
            checks["redis"] = "Redis client not initialised"
            return
        try:
            await asyncio.wait_for(_svc_clients.redis_client.ping(), timeout=1.0)
            checks["redis"] = True
        except Exception as e:
            checks["redis"] = str(e)

    async def _check_livekit_primary():
        lk_url = os.getenv("LIVEKIT_URL", "")
        if not lk_url:
            checks["livekit_primary"] = "LIVEKIT_URL not set"
            return
        http_url = lk_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
        try:
            r = await _svc_clients.http_client.get(http_url, timeout=2.0)
            if r.status_code < 500:
                checks["livekit_primary"] = True
            else:
                checks["livekit_primary"] = f"Primary LiveKit endpoint HTTP {r.status_code}"
        except Exception as e:
            checks["livekit_primary"] = f"Primary LiveKit endpoint unreachable: {e}"

    await asyncio.gather(
        _check_livekit_primary(),
        _check("livekit", _svc_clients.lk_client.room.list_rooms(api.ListRoomsRequest()), timeout=5.0),
        _check_redis(),
        _check_postgres(),
        _check_stt(),
        _check_mantraassist_backend(),
        _check_s3(),
        return_exceptions=True
    )

    all_ok = True
    for service, status in checks.items():
        if status is True:
            logger.info(f"  - Healthcheck OK: {service}")
        else:
            all_ok = False
            logger.warning(f"  - Healthcheck FAILED: {service} -> {status}")

    return all_ok, checks

async def _run_health_checks() -> bool:
    """Run all dependency + capacity checks. Returns False if any check fails."""
    if os.getenv("BYPASS_HEALTH_CHECKS") == "1":
        logger.warning("BYPASS_HEALTH_CHECKS is active. Skipping all service health checks.")
        return True

    dep_ok, checks = await _run_dependency_checks()
    if not dep_ok:
        return False

    rooms = []
    try:
        rooms = await _active_call_rooms()
    except Exception as e:
        checks["capacity_max_concurrency"] = f"error: {e}"
        rooms = []

    total = len(rooms)
    if total >= MAX_CALL_CONCURRENCY:
        checks["capacity_max_concurrency"] = f"BUSY {total}/{MAX_CALL_CONCURRENCY}"
    else:
        checks["capacity_max_concurrency"] = True

    try:
        trunk_ids = await _extract_trunk_ids(rooms)
    except Exception:
        trunk_ids = []
    seen: set[str] = set()
    for trunk_id in trunk_ids:
        if trunk_id in seen:
            continue
        seen.add(trunk_id)
        provider, limit = await _resolve_trunk_limit(trunk_id)
        active = await _active_per_trunk(rooms, trunk_id)
        key = f"trunk_capacity_{trunk_id}"
        if active >= limit:
            checks[key] = f"BUSY {active}/{limit}"
        else:
            checks[key] = True

    all_ok = True
    for service, status in checks.items():
        if status is True:
            logger.info(f"  - Healthcheck OK: {service}")
        else:
            all_ok = False
            logger.warning(f"  - Healthcheck FAILED: {service} -> {status}")

    return all_ok

async def health_gate_middleware(request: Request, call_next):
    """Per-provider capacity gate + coarse dependency gate. 503 on blocked dispatch."""
    path = request.url.path
    if request.method == "POST" and path in _DISPATCH_PATHS:
        if path == "/v1/webhooks/telephony":
            try:
                body = await request.body()
                payload = json.loads(body) if body else {}
                call_id = str(
                    payload.get("call_id")
                    or payload.get("voice_id")
                    or payload.get("event_id")
                    or int(time.time())
                )
                trunk_id = (
                    payload.get("trunk_id")
                    or payload.get("call_from_id")
                    or os.getenv("SIP_TRUNK_ID")
                )
                if trunk_id:
                    provider, limit = await _resolve_trunk_limit(trunk_id)
                    if provider is not None:
                        busy, active = await _trunk_at_capacity(trunk_id)
                        if busy:
                            logger.warning(
                                f"Trunk gate blocked {trunk_id} ({provider}): {active}/{limit}"
                            )
                            asyncio.create_task(
                                _log_blocked_call(
                                    call_id, provider, active,
                                    trunk_id=trunk_id,
                                    phone=str(payload.get("client_phone", "")),
                                    caller_number=str(payload.get("call_from", "")),
                                )
                            )
                            return Response(status_code=503)
            except Exception as e:
                logger.error(f"trunk capacity gate error, blocking: {e}")
                return Response(status_code=503)

        # ── Global capacity gate (agent pool) ───────────────────
        try:
            rooms = await _active_call_rooms()
            if len(rooms) >= MAX_CALL_CONCURRENCY:
                logger.warning(f"Global capacity gate blocked: {len(rooms)}/{MAX_CALL_CONCURRENCY}")
                return Response(status_code=503)
        except Exception as e:
            logger.warning(f"Global capacity gate unavailable, continuing: {e}")

        ok, _ = await _run_dependency_checks()
        if not ok:
            logger.warning(f"Health gate blocked {request.method} {path}")
            return Response(status_code=503)

    return await call_next(request)

def _check_s3_bucket(bucket: str):
    import boto3
    _saved = {}
    for _var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "PLIVO_PROXY"):
        _val = os.environ.pop(_var, None)
        if _val is not None:
            _saved[_var] = _val
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        s3.head_bucket(Bucket=bucket)
    finally:
        os.environ.update(_saved)

