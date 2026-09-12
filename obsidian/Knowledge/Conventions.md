# Conventions

## Code Style

- **Asynchronous:** All I/O is async via `asyncio`
- **Logging:** Each module defines its own logger
- **Env Loading:** `load_dotenv(".env.local")` in entrypoints
- **Error Handling:** `try/except Exception as e:` at network boundaries, log with traceback

## Architectural Patterns

- **Redis Queueing:** Sorted sets for priority queuing
- **Capacity Management:** Per-trunk limits + global cap enforced before dispatch (`_resolve_trunk_limit`, `_trunk_at_capacity`)
- **Zombie Cleanup:** Periodic reconciliation between Redis state and LiveKit rooms (every 60s + one-shot on startup; deletes empty `call_*` rooms)
- **Three LiveKit clients:** Direct + Proxied (Plivo India) + Proxied (VoiceLink)

## Naming

- `call_id` — Unique call identifier (from payload or auto-generated)
- `room_name` — LiveKit room: `call_{trunk_id}_{call_id}` (e.g. `call_ST_xxx_abc123`), `test_{call_id}`, or `test_inbound_{call_id}`
- `trunk_id` — SIP trunk identifier (LiveKit `ST_xxx`); cached to provider via Redis `trunk:provider:{trunk_id}`
- Module loggers: `mantra.{module_name}`

## Process Boundaries

- UI Server → Redis → Dispatcher → LiveKit → Agent
- Each process runs independently
- Redis is the shared state layer
