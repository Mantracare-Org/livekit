# Conventions

## Code Style

- **Asynchronous:** All I/O is async via `asyncio`
- **Logging:** Each module defines its own logger
- **Env Loading:** `load_dotenv(".env.local")` in entrypoints
- **Error Handling:** `try/except Exception as e:` at network boundaries, log with traceback

## Architectural Patterns

- **Redis Queueing:** Sorted sets for priority queuing
- **Capacity Management:** Explicit limits enforced before dispatch
- **Zombie Cleanup:** Periodic reconciliation between Redis state and LiveKit rooms
- **Two LiveKit clients:** Direct + Proxied (Plivo India)

## Naming

- `call_id` — Unique call identifier (from payload or auto-generated)
- `room_name` — LiveKit room: `call_{call_id}` or `test_{call_id}`
- `trunk_id` — SIP trunk identifier
- Module loggers: `mantra.{module_name}`

## Process Boundaries

- UI Server → Redis → Dispatcher → LiveKit → Agent
- Each process runs independently
- Redis is the shared state layer
