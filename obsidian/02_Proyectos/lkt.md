---
title: "Project: lkt"
type: antigravity-project
tags:
  - antigravity/project
created: 2026-10-27
updated: 2026-10-27
---

# Project: lkt

> [!INFO] **Project Dossier**
> - **Name**: `lkt`
> - **Local Path**: `/home/fardeen/lkt`
> - **Stack**: Python 3.12 (LiveKit Agents + FastAPI)
> - **Summary**: Production-grade real-time conversational AI voice agent and telephony orchestrator for inbound and outbound healthcare coordination, appointment management, and triage.
> - **Associated Skill**: —
> - **Last Synchronization**: 2026-10-27

### Detected Technical Details
- **Version**: `0.5.0`
- **Framework / Core**: `Python 3.12` / `LiveKit Agents` / `FastAPI`
- **Key Dependencies**: `livekit-agents`, `livekit-plugins-ai-coustics`, `deepgram-sdk`, `openai`, `google-genai`, `asyncpg`, `redis`, `fastapi`, `uvicorn`, `geopy`, `pydantic`

### Architectural Structure & Key Modules
| Directory / Module | Architectural Role | Detected Contents |
|---|---|---|
| `mantra/core/` | Modular voice agent subsystems | `assistant_functions.py`, `call_monitors.py`, `finalize.py`, `inbound.py`, `engines.py`, `room_control.py`, `live_agent.py`, `common.py` |
| `mantra/routers/` | Decomposed FastAPI HTTP route handlers | `sip.py`, `knowledge.py`, `telephony.py`, `dashboard.py`, `redis_api.py`, `org_configs.py`, `pages.py`, `auth.py` |
| `mantra/services/` | Telephony, capacity, and webhook services | `telephony.py`, `health.py`, `clients.py`, `webhook_queue.py`, `capacity.py` |
| `mantra/dependencies/` | FastAPI dependency injection providers | `auth.py` (JWT bearer), `database.py` (connection pool) |
| `mantra/migrations/` | PostgreSQL schema migrations | `001_kb_pages` through `008_webhook_deliveries_outbox` |
| `mantra/` | Top-level shims and orchestrators | `agent.py`, `ui_server.py`, `prompts.py`, `language_manager.py`, `mcp_client.py`, `knowledge_base.py`, `utils.py`, `dispatcher.py` |
| `mcp/` | Model Context Protocol server | `server.py` (legacy local stdio/SSE tools) |
| `static/` | Frontend web applications | `dashboard.html`, `dashboard.js`, `index.html`, `login.html`, `network.html`, `redis.html`, `kb_chat.html` |
| `obsidian/` | Permanent repository knowledge base | `Architecture/`, `Development/`, `Features/`, `Knowledge/`, `Context/`, `Agents/`, `00_Agente/`, `03_Sesiones/` |

**Root Configuration & Launchers:** `AGENTS.md`, `Dockerfile`, `dev.sh`, `entrypoint.sh`, `livekit.toml`, `pyproject.toml`, `uv.lock`

### Entrypoints & Services
- **Voice Agent Worker:** `uv run python -m mantra.agent dev` (or `./entrypoint.sh agent`)
- **UI / API Server:** `uv run python -m mantra.ui_server` (or `./entrypoint.sh ui`)
- **Local MCP Server:** `uv run python mcp/server.py` (or `./entrypoint.sh mcp`)
- **Full Dev Environment:** `./dev.sh`

### Database & Telephony Infrastructure
- **PostgreSQL (`asyncpg`):** Stores `call_logs`, `call_events`, `kb_pages`, `kb_collections`, `org_configs`, and `webhook_deliveries` replay outbox.
- **Redis:** Manages pending call queue (`queue:pending`), active call states (`calls:active`), trunk concurrency capacity gating, and lock deduplication (`lock:call:{call_id}`).
- **Telephony Trunks:** Twilio, Plivo, Zadarma, VoiceLink.
- **Remote MCP:** `livekit-mcp` service over SSE JSON-RPC 2.0 with dynamic OAuth authentication for doctor scheduling and client recognition.

## Mandatory Project Rules & Conditions
1. Always maintain code conventions and documentation integrity across `obsidian/`.
2. Keep `obsidian/Development/Changelog.md` and `obsidian/Development/Current Sprint.md` updated after each milestone.
3. Test python compilation via `python -m py_compile <file>` and run unit tests prior to deployment.
4. Strictly zero emojis across code, documentation, and agent output.

## Anti-Patterns & Forbidden Traps
- Never commit hardcoded secrets or unhashed JWT keys; always source from `.env.local` or environment variables.
- Never block the LiveKit agent event loop with synchronous sleep or blocking HTTP requests.
- Never use heuristic farewell phrase scanning to disconnect calls; rely on `StopResponse()` and `graceful_disconnect_after_speech()`.
- Never invent unlisted medical departments or confirm unavailable doctor appointment slots.

## Backlog & Pending Tasks
- [ ] Ingest KB data for org 66 (`kb_pages` has zero rows)
- [ ] Configure `AWS_S3_BUCKET_NAME` for recording persistence
- [ ] Replace dispatcher 0.5s polling loop with Redis Pub/Sub
- [ ] Complete end-to-end integration test suite for telephony call flows

## Recent Sessions & Checkpoints
| Date | Summary | Session |
|---|---|---|
| 2026-10-26 | Addressed CodeRabbit review comments on Obsidian documentation | [[2026-10-26 - lkt]] |
| 2026-10-23 | Hito de trabajo: 1 archivo editado en lkt | [[2026-10-23 - lkt]] |

---
*Graph Connections:* [[00 Antigravity Hub]] | [[00 Projects Index]] | [[Architecture/Overview.md]] | [[Development/Current Sprint.md]]
