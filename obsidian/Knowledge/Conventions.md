# Conventions

## Code Style

- **Asynchronous:** All I/O is async via `asyncio`
- **Logging:** Each module defines its own logger; plain `logging.Formatter` (no colorama)
- **Env Loading:** `load_dotenv(".env.local")` in entrypoints
- **Error Handling:** `try/except Exception as e:` at network boundaries, log with traceback

## Timestamps

- All timeline events and `normalize_to_iso8601` use **IST (+05:30)** — never UTC
- `datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))` is the standard pattern
- ISO-8601 output format: `YYYY-MM-DDTHH:MM:SS+05:30`

## Architectural Patterns

- **Redis Queueing:** Sorted sets for priority queuing
- **Capacity Management:** Explicit limits enforced before dispatch
- **Zombie Cleanup:** Periodic reconciliation between Redis state and LiveKit rooms
- **Two LiveKit clients:** Direct + Proxied (Plivo India)
- **Null-safe stage tracking:** Stage ID variables initialized to `None`; filtered with list comprehension before comparison

## Naming

- `call_id` — Unique call identifier (from payload or auto-generated)
- `room_name` — LiveKit room: `call_{call_id}` or `test_{call_id}`
- `trunk_id` — SIP trunk identifier
- Module loggers: `mantra.{module_name}`

## Process Boundaries

- UI Server → Redis → Dispatcher → LiveKit → Agent
- Each process runs independently
- Redis is the shared state layer
