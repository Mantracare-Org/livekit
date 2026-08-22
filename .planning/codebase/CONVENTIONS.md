# CONVENTIONS.md

## Code Style
- **Asynchronous Execution**: Deeply integrated with Python `asyncio`. Heavy use of `async`/`await` across FastAPI, Redis, and LiveKit tasks.
- **Typing**: Minimal/moderate use of standard Python type hints.
- **Logging**: Extensive use of Python's built-in `logging` module. Every module defines its own logger (e.g., `logger = logging.getLogger("mantra.dispatcher")`).

## Architectural Patterns
- **Redis Queueing**: Tasks are passed between the UI Server and the Dispatcher via Redis sorted sets.
- **Capacity Management**: Explicit concurrency limits are enforced via `CARTESIA_MAX_CONCURRENCY`, `LIVEKIT_MAX_ROOMS`, and `AGENT_MAX_WORKERS` before popping tasks off the queue.
- **Zombie Cleanup**: Background tasks in the dispatcher actively query Redis against the LiveKit Server to purge dangling "zombie" rooms.

## Error Handling
- Broad `try/except Exception as e:` blocks are used around critical network boundaries (like API requests and SIP participant creation).
- Errors are logged explicitly alongside `traceback.format_exc()` for debugging.
- In the dispatcher, failed SIP triggers or dispatch errors attempt to re-queue the call payload and update the Redis status to `failed_dispatch_requeued`.
