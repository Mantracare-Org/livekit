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

## Webhook Flow

```python
webhook_handler() → create_dispatch() + background_task(trigger_sip())
                                             ├── SIP participant creation
                                             ├── On failure: write sip_error_status to Redis
                                             └── On failure: delete room
```
