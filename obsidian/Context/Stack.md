# Stack

## Runtime
- **Python** 3.11+ (managed via `uv`)
- **uv** — Fast Python package manager (replaces pip/poetry)
- **ASGI** — FastAPI + Uvicorn

## Voice AI
- **LiveKit Agents ~1.4** — Agent framework
- **LiveKit API >=1.1** — Cloud API client
- **Deepgram Nova-3** — Speech-to-Text
- **OpenAI GPT-4o-mini** — Default LLM
- **Gemini 2.5 Flash** — Alternative LLM
- **DeepSeek v4 Flash** — Alternative LLM
- **Cartesia Sonic-3** — Text-to-Speech
- **Silero VAD** — Voice Activity Detection
- **Multilingual Turn Detector** — Turn detection

## Infrastructure
- **PostgreSQL** — Call log storage + KB (Full-Text Search via `tsvector`)
- **Redis >=8.0** — Queue, state, capacity
- **AWS S3** — Recording storage
- **LiveKit Cloud** — WebRTC + SIP

## Web Server
- **FastAPI >=0.115** — HTTP API framework
- **Uvicorn >=0.34** — ASGI server
- **PyJWT >=2.8** — Authentication tokens

## Frontend
- **Vanilla HTML/CSS/JS** — No framework
- **LiveKit Client SDK** — WebRTC
- **OpsCraft Design** — Dark theme (Discord/Linear inspired)

## Dev Tools
- **Docker** — Multi-stage build
- **MCP** — Model Context Protocol

## Key Environment
- **Ubuntu/Linux** — Deployment target
- **Cloudflare** — Possible CDN (Daemon deployment)
