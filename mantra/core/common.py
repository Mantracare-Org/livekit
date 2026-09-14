"""Shared primitives for the agent worker: background task tracking, coercion
helpers, the singleton knowledge base handle, and the CallContext bundle that
is threaded through monitors and finalization.
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from mantra.knowledge_base import PostgresKnowledgeBase

_bg_tasks = set()


def create_bg_task(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _as_int(value):
    """Coerce value to int for backend Zod schemas. Returns None if not coercible."""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


_global_kb: PostgresKnowledgeBase | None = None


def get_global_kb() -> PostgresKnowledgeBase:
    global _global_kb
    if _global_kb is None:
        dsn = (
            f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
            f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        _global_kb = PostgresKnowledgeBase(dsn)
    return _global_kb


@dataclass
class CallContext:
    """Everything a call monitor / finalizer needs, threaded from entrypoint."""

    ctx: Any
    session: Any
    agent: Any
    fnc_ctx: Any
    call_state: dict
    recorder: Any
    language_mgr: Any
    tts_engine: Any
    stt_engine: Any
    keyterm_memory: Any
    llm_engine: Any
    entrypoint_start_time: float
    is_inbound: bool
    client_name: str
    voice_id: str
    effective_call_metadata: Optional[dict]
    telemetry: Callable
    agent_name: str = "mantra-agent"
    task_handles: dict = field(default_factory=dict)