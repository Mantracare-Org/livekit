# Changelog

## 2026-07-09

- **feat:** Grafana observability integration (Prometheus metrics + structured JSON logging)
  - New `mantra/telemetry.py` — shared Prometheus metrics registry (HTTP, calls, queue, pipeline, SIP)
  - New `mantra/log_config.py` — structured JSON logging via `python-json-logger`
  - Added `/metrics` endpoint on UI server (port 9090) for Prometheus scraping
  - Instruments FastAPI HTTP middleware with request count, latency, in-flight gauges
  - Instrumented call lifecycle: `calls_total`, `calls_in_progress`, `call_duration_seconds`
  - Instrumented dispatcher: `queue_depth`, `dispatch_attempts_total`, `dispatches_in_flight`
  - Instrumented SIP calls: `sip_calls_total` by provider/status
  - Added `crash_total` counter to exception handlers
  - Added `LOG_LEVEL`, `LOG_FORMAT`, `METRICS_PORT`, `METRICS_PREFIX` env vars
  - Updated Dockerfile to expose port 9090

## 2026-06-30

- **doc:** Created `obsidian/` — comprehensive Obsidian knowledge base (48 files)
  - `Architecture/` — 8 files (overview, components, data flow, APIs, DB, infra, decisions, deps)
  - `Features/` — 10 files (index + 9 feature pages covering all modules)
  - `Development/` — 7 files (sprint, TODO, backlog, bugs, changelog, releases, roadmap)
  - `Agents/` — 6 files (master, backend, frontend, devops, docs, QA agent guides)
  - `Knowledge/` — 7 files (standards, conventions, best practices, commands, debugging, env, glossary)
  - `Context/` — 5 files (project summary, stack, repo map, external services)
  - `Templates/` — 3 file templates
  - `Inbox/` — placeholder
- **doc:** Updated root `README.md` to reference the Obsidian vault

## 2026-06-15

- **refactor:** Renamed `CARTESIA_MAX_CONCURRENCY` to `MAX_CONCURRENCY` + env var fallback
- **refactor:** Migrated Cartesia TTS to LiveKit Inference, removed redundant API key management
- **feat:** Webhook-based call log storage, updated DB query schemas
- **feat:** Dynamic tone and style configurations for agent prompts

## 2026-05

- **feat:** Emotional tone optimization for Cartesia voice synthesis
- **feat:** `end_call` tool with graceful disconnect + safety net
- **feat:** Automated crash email notifications
- **feat:** Gemini LLM integration
- **feat:** DeepSeek LLM integration
- **feat:** Plivo India proxy routing
- **feat:** Redis queue-based dispatcher system
- **feat:** Dashboard with SSE real-time metrics
- **feat:** JWT authentication
- **refactor:** Bilingual STT (Deepgram Hindi model)
- **refactor:** Custom ColorFormatter for multi-process logging
