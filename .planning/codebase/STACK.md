# STACK.md

## Languages
- **Python**: >= 3.11 is required by the project.
- **JavaScript/HTML/CSS**: For the static web UI (`static/app.js`, `static/index.html`).

## Runtime & Frameworks
- **LiveKit Agents**: The core real-time voice agent framework (`livekit-agents`, `livekit-api`).
- **FastAPI**: Used for the UI server and webhook endpoints (`mantra/ui_server.py`).
- **Uvicorn**: ASGI web server implementation used to run FastAPI.

## Key Dependencies
- **LiveKit Plugins**: Extensive use of AI plugins:
  - `livekit-plugins-openai` (LLM)
  - `livekit-plugins-cartesia` (TTS)
  - `livekit-plugins-deepgram`, `livekit-plugins-assemblyai` (STT)
  - `livekit-plugins-noise-cancellation`
- **Redis**: `redis.asyncio` used for queueing calls, concurrency checks, and tracking active rooms.
- **PostgreSQL**: Accessed asynchronously via `asyncpg` and exposed via `mcp[cli]`.
- **AWS / Boto3**: For handling S3 uploads (call recordings).
- **Audio Processing**: `pydub`, `torch`.
- **HTTP client**: `httpx` for making async outbound API calls (e.g., to n8n webhooks).

## Configuration
- Environment variables loaded via `python-dotenv` from `.env.local`.
- Packaging and dependencies managed via `uv` (`uv.lock`, `pyproject.toml`).
- `livekit.toml` configuration for the LiveKit agent worker.
