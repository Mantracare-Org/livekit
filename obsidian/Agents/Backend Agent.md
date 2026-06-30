# Backend Agent

## Context

Backend consists of `mantra/` Python package with FastAPI server, LiveKit agent, and utilities.

## Key Conventions

- All async, `asyncio` throughout
- Logging: `logger = logging.getLogger("mantra.{module}")`
- Env vars from `.env.local` with `dotenv`
- `try/except Exception as e: logger.error(...)` pattern for network boundaries

## Testing

No automated tests exist. Manual testing via:
- `./dev.sh` to launch services
- `curl` to webhook endpoints
- Dashboard for metrics verification
