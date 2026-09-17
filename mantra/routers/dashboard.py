""" Dashboard API routes for the UI server. """

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from mantra.dependencies.database import get_db_connection
from mantra.services import clients as _svc_clients
import asyncio
import json
import os
import time

import logging

logger = logging.getLogger("mantra.dashboard")
router = APIRouter()

@router.get("/v1/dashboard/stream")
async def dashboard_stream(request: Request):
    """SSE endpoint with real-time queue status + active call details."""
    # require_auth(request)

    async def event_generator():
        if not _svc_clients.redis_client:
            yield 'data: {"error": "Redis not connected"}\n\n'
            return

        MAX_CONCURRENCY = int(
            os.getenv("MAX_CONCURRENCY", os.getenv("CARTESIA_MAX_CONCURRENCY", "5"))
        )

        while True:
            try:
                pending_count = await _svc_clients.redis_client.zcard("queue:pending")
                active_calls_map = await _svc_clients.redis_client.hgetall("calls:active")
                active_count = len(active_calls_map)

                active_details = []
                for call_id, room_name in active_calls_map.items():
                    status = await _svc_clients.redis_client.get(f"calls:status:{call_id}")
                    active_details.append(
                        {
                            "call_id": call_id,
                            "room_name": room_name,
                            "status": status or "unknown",
                        }
                    )

                data = json.dumps(
                    {
                        "pending_calls": pending_count,
                        "active_calls": active_count,
                        "max_concurrency": MAX_CONCURRENCY,
                        "active_call_details": active_details,
                        "timestamp": time.time(),
                    }
                )
                yield f"data: {data}\n\n"
            except Exception as e:
                logger.error(f"Dashboard SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")



@router.get("/v1/dashboard/metrics")
async def dashboard_metrics(request: Request):
    """Today's call metrics from PostgreSQL."""
    # require_auth(request)

    try:
        conn = await get_db_connection()
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*)::int AS total_calls,
                    COUNT(*) FILTER (WHERE status = 'Completed')::int AS completed_calls,
                    COUNT(*) FILTER (WHERE status = 'Busy')::int AS busy_calls,
                    COUNT(*) FILTER (WHERE status = 'No Answer')::int AS no_answer_calls,
                    COUNT(*) FILTER (WHERE status = 'Error')::int AS error_calls,
                    COUNT(*) FILTER (WHERE status = 'Incomplete')::int AS incomplete_calls,
                    ROUND(
                        AVG(
                            CAST(NULLIF(call_log::json ->> 'call_duration_seconds', '') AS integer)
                        ) FILTER (
                            WHERE call_log::json ->> 'call_duration_seconds' ~ '^\\d+$'
                        )
                    )::int AS avg_duration_seconds
                FROM call_logs
                WHERE created_at >= CURRENT_DATE
            """)
        finally:
            await conn.close()

        metrics = (
            dict(row)
            if row
            else {
                "total_calls": 0,
                "completed_calls": 0,
                "busy_calls": 0,
                "no_answer_calls": 0,
                "error_calls": 0,
                "incomplete_calls": 0,
                "avg_duration_seconds": 0,
            }
        )

        answer_rate = (
            round(metrics["completed_calls"] / metrics["total_calls"] * 100, 1)
            if metrics["total_calls"] > 0
            else 0
        )

        return {
            **metrics,
            "answer_rate": answer_rate,
        }
    except Exception as e:
        logger.error(f"Dashboard metrics error: {e}")
        return {"error": str(e)}



@router.get("/v1/dashboard/calls")
async def dashboard_calls(request: Request, limit: int = 20, offset: int = 0, search: str = None, status: str = None):
    """Paginated call history from PostgreSQL with search & status filtering."""
    try:
        conn = await get_db_connection()
        try:
            conditions = []
            params = []
            param_idx = 1

            if search and search.strip():
                conditions.append(f"(CAST(call_id AS TEXT) ILIKE ${param_idx} OR caller_number ILIKE ${param_idx} OR called_number ILIKE ${param_idx} OR call_log::text ILIKE ${param_idx})")
                params.append(f"%{search.strip()}%")
                param_idx += 1

            if status and status.strip() and status.lower() != "all":
                st_clean = status.strip().replace(" ", "").replace("_", "").lower()
                conditions.append(f"REPLACE(REPLACE(LOWER(status), '_', ''), ' ', '') LIKE ${param_idx}")
                params.append(f"%{st_clean}%")
                param_idx += 1

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            query = f"""
                SELECT call_id, status, recording_url, created_at, caller_number, called_number, trunk_id,
                       call_log::json AS call_log,
                       COALESCE(attempts, '[]'::jsonb) AS attempts
                FROM call_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params_with_limit = params + [limit, offset]
            rows = await conn.fetch(query, *params_with_limit)

            count_query = f"SELECT COUNT(*)::int AS total FROM call_logs {where_clause}"
            count_row = await conn.fetchrow(count_query, *params)
            total = count_row["total"] if count_row else 0
        finally:
            await conn.close()

        calls = []
        for row in rows:
            cl_raw = row["call_log"]
            if isinstance(cl_raw, str):
                try:
                    cl = json.loads(cl_raw)
                except Exception:
                    cl = {}
            elif isinstance(cl_raw, dict):
                cl = cl_raw
            else:
                cl = {}

            attempts_raw = row.get("attempts")
            if isinstance(attempts_raw, str):
                try:
                    attempts_list = json.loads(attempts_raw)
                except Exception:
                    attempts_list = []
            elif isinstance(attempts_raw, list):
                attempts_list = attempts_raw
            else:
                attempts_list = []

            trunk_val = (
                row["trunk_id"]
                or cl.get("trunk_id")
                or cl.get("sip_trunk_id")
                or cl.get("call_from_id")
                or cl.get("_resolved_trunk_id")
                or cl.get("provider")
                or ""
            )
            calls.append(
                {
                    "call_id": str(row["call_id"]),
                    "status": row["status"],
                    "recording_url": row["recording_url"] or "",
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "caller_number": row["caller_number"] or cl.get("caller_number") or cl.get("client_phone") or "",
                    "called_number": row["called_number"] or cl.get("called_number") or "",
                    "trunk_id": trunk_val,
                    "client_name": cl.get("client_name") or cl.get("client_id") or "",
                    "client_phone": cl.get("client_phone") or row["caller_number"] or "",
                    "duration": cl.get("call_duration_seconds"),
                    "summary": cl.get("ai_summary") or cl.get("summary") or "",
                    "transcript": cl.get("call_transcript") or cl.get("transcript") or cl.get("conversation") or None,
                    "purpose": (cl.get("prompt") or "")[:120],
                    "attempts": attempts_list,
                    "attempts_count": len(attempts_list),
                    "call_log_raw": cl,
                }
            )

        return {"calls": calls, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"Dashboard calls error: {e}")
        return {"error": str(e), "calls": [], "total": 0}



@router.get("/v1/dashboard/active-calls")
async def dashboard_active_calls(request: Request):
    """Current active calls from Redis."""
    if not _svc_clients.redis_client:
        return {"active_calls": [], "error": "Redis not connected"}

    try:
        active_map = await _svc_clients.redis_client.hgetall("calls:active")
        calls = []
        for call_id, room_name in active_map.items():
            status = await _svc_clients.redis_client.get(f"calls:status:{call_id}")
            calls.append(
                {
                    "call_id": call_id,
                    "room_name": room_name,
                    "status": status or "unknown",
                }
            )
        return {"active_calls": calls}
    except Exception as e:
        logger.error(f"Active calls error: {e}")
        return {"active_calls": [], "error": str(e)}




