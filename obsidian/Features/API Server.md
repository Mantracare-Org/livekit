# API Server

**File:** `mantra/ui_server.py` (933 lines)

## Overview

FastAPI HTTP server that handles:
- Telephony webhooks for outbound calls
- SIP trunk CRUD operations
- Static file serving (login, dashboard, console)
- JWT authentication
- Dashboard data APIs
- Real-time SSE streams

## Architecture

- Two LiveKit API clients: `lk_client` (direct) + `plivo_client` (proxied for India)
- Request logging middleware with scanner path suppression
- Global crash exception handler → email alert
- Lifespan: connects LiveKit clients + Redis on startup, closes on shutdown

## JWT Auth

- SHA-256 hashed username/password from env
- 24h token expiry (HS256)
- Dashboard routes protected via `require_auth()` dependency

## SIP Trunk Management

Three provider-specific endpoints, all sharing `_create_sip_outbound_trunk()`:
- `/zadarma` — Backward-compatible root endpoint
- `/twilio` — Default address `live-kit-mc.pstn.twilio.com`
- `/plivo` — Supports on-the-fly trunk provisioning + call placement; uses proxied client for India routing

## Call Log Webhook

`POST /api/v1/webhooks/call-logs` — Receives the full call log payload from the LiveKit agent and writes it to PostgreSQL. This exists because the agent worker can't reach the database directly (cloud security group blocks). The UI Server acts as a DB proxy.

## SIP Failure Immediate DB Logging

When a SIP call fails (busy, no answer), the webhook handler now saves the call log to PostgreSQL **immediately** via `save_call_log_to_db()`, so failed calls appear in the dashboard right away instead of waiting for the agent's post-call pipeline.

## DB Query Schema Update

The `call_log` JSONB structure changed. Duration is now nested under `call_log -> 'data' ->> 'call_duration_seconds'` instead of top-level `call_log ->> 'call_duration_seconds'`. Dashboard metrics and call history queries updated accordingly.

## Webhook Flow

```python
webhook_handler() → create_dispatch() + background_task(trigger_sip())
                                             ├── SIP participant creation
                                             ├── On failure: save call log to DB immediately
                                             ├── On failure: write sip_error_status to Redis
                                             └── On failure: delete room
```
