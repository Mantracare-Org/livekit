import os
import asyncio
import datetime
import logging

logger = logging.getLogger("mantra.call_duration")

BASE_FAREWELL_SECONDS = int(os.getenv("CALL_BASE_FAREWELL_SECONDS", "150"))
BASE_HARD_LIMIT_SECONDS = int(os.getenv("CALL_BASE_HARD_LIMIT_SECONDS", "180"))
EXTENDED_FAREWELL_SECONDS = int(os.getenv("CALL_EXTENDED_FAREWELL_SECONDS", "270"))
EXTENDED_HARD_LIMIT_SECONDS = int(os.getenv("CALL_EXTENDED_HARD_LIMIT_SECONDS", "300"))


def current_limits(extended: bool) -> tuple[int, int]:
    if extended:
        return EXTENDED_FAREWELL_SECONDS, EXTENDED_HARD_LIMIT_SECONDS
    return BASE_FAREWELL_SECONDS, BASE_HARD_LIMIT_SECONDS


def is_extendable(*, is_inbound: bool, already_extended: bool, elapsed: float) -> bool:
    if is_inbound:
        return False
    if already_extended:
        return False
    if elapsed >= EXTENDED_HARD_LIMIT_SECONDS - 5:
        return False
    return True


async def extend_call(call_state: dict, reason: str, elapsed: float) -> bool:
    if not is_extendable(
        is_inbound=bool(call_state.get("is_inbound")),
        already_extended=bool(call_state.get("duration_extended")),
        elapsed=elapsed,
    ):
        return False
    call_state["duration_extended"] = True
    call_state["extension_reason"] = reason
    call_state["extension_elapsed"] = elapsed
    evt = call_state.get("extension_event")
    if isinstance(evt, asyncio.Event):
        evt.set()
    call_state["timeline"].append(
        {
            "event": "Call Duration Extended to 5m — Positive Intent",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "reason": reason,
            "elapsed": round(elapsed, 1),
        }
    )
    logger.warning(f"[CALL_DURATION] Extended to {EXTENDED_HARD_LIMIT_SECONDS}s at t={elapsed:.1f}s — reason: {reason}")
    if call_state.get("farewell_triggered"):
        agent = call_state.get("_agent_ref")
        if agent is not None:
            try:
                orig = call_state.get("original_instructions")
                if isinstance(orig, str):
                    await agent.update_instructions(orig)
                    logger.info("[CALL_DURATION] Farewell instructions reverted after extension")
                call_state["farewell_triggered"] = False
            except Exception as e:
                logger.error(f"[CALL_DURATION] Failed to revert farewell: {e}")
    return True
