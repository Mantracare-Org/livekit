""" Per-trunk concurrency capacity helpers for the UI server. """

from datetime import datetime, timezone
from livekit import api
from mantra.services import clients as _svc_clients
from mantra.services.clients import PROVIDER_DEFAULT_CONCURRENCY
from mantra.services.telephony import _get_provider_from_trunk
from mantra.utils import save_call_log_to_db
import json

import logging

logger = logging.getLogger("mantra.capacity")

# ── Per-trunk capacity helpers ─────────────────────────────────────────

_TRUNK_TO_PROVIDER: dict[str, str] = {}

async def _resolve_trunk_limit(trunk_id: str) -> tuple[str | None, int]:
    provider = _TRUNK_TO_PROVIDER.get(trunk_id)
    if provider is None:
        provider = await _get_provider_from_trunk(trunk_id)
        if provider:
            _TRUNK_TO_PROVIDER[trunk_id] = provider
    if provider and provider in PROVIDER_DEFAULT_CONCURRENCY:
        return provider, PROVIDER_DEFAULT_CONCURRENCY[provider]
    return provider, 1

async def _active_call_rooms() -> list[str]:
    if not _svc_clients.lk_client:
        raise RuntimeError("LiveKit client not initialised")
    resp = await _svc_clients.lk_client.room.list_rooms(api.ListRoomsRequest())
    return [r.name for r in resp.rooms if (r.name or "").startswith("call_")]

async def _extract_trunk_ids(rooms: list[str]) -> list[str]:
    trunk_ids: list[str] = []
    for name in rooms:
        if not name or not name.startswith("call_"):
            continue
        parts = name[5:].rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            trunk_ids.append(parts[0])
    return trunk_ids

async def _active_per_trunk(rooms: list[str], trunk_id: str) -> int:
    prefix = f"call_{trunk_id}_"
    return sum(1 for r in rooms if r and r.startswith(prefix))

async def _trunk_at_capacity(trunk_id: str) -> tuple[bool, int]:
    provider, limit = await _resolve_trunk_limit(trunk_id)
    if provider is None:
        return False, 0
    rooms = await _active_call_rooms()
    active = await _active_per_trunk(rooms, trunk_id)
    return active >= limit, active

async def _log_blocked_call(
    call_id: str, provider: str | None, active_count: int,
    trunk_id: str = "", phone: str = "", caller_number: str = "",
):
    limit = PROVIDER_DEFAULT_CONCURRENCY.get(provider or "", "?")
    call_log = json.dumps({
        "call_id": call_id,
        "provider": provider,
        "blocked": True,
        "reason": "trunk_at_concurrency_limit",
        "active_calls": active_count,
        "max_concurrency": limit,
        "trunk_id": trunk_id,
        "phone": phone,
        "requested_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    await save_call_log_to_db(
        call_id, call_log, "Busy", "",
        caller_number=caller_number,
        called_number=phone,
        trunk_id=trunk_id,
    )

