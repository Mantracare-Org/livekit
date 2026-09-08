# API Server

**File:** `mantra/ui_server.py` (3,613 lines)

## Overview

FastAPI HTTP server that handles:
- Telephony webhooks for outbound calls
- SIP trunk CRUD (inbound + outbound, all four providers)
- End-to-end inbound SIP provisioning (LiveKit trunk + dispatch rule + provider API)
- Static file serving (login, dashboard, console)
- JWT authentication
- Dashboard data APIs
- Real-time SSE streams
- Knowledge base ingestion (file, text, URL, chat, document delete)
- Organization config CRUD
- Per-provider capacity gating middleware
- Health checks (dependencies + per-provider capacity)

## Architecture

- Three LiveKit API clients: `lk_client` (direct), `plivo_client` (proxied for India), `voicelink_client` (proxied)
- Request logging middleware with scanner path suppression
- Global crash exception handler → email alert
- Lifespan: connects LiveKit clients + Redis + httpx + PostgreSQL on startup, closes on shutdown
- Startup healthcheck on all dependencies
- Prometheus metrics via `prometheus_fastapi_instrumentator`

## JWT Auth

- SHA-256 hashed username/password from env
- 24h token expiry (HS256)
- Dashboard routes protected via `require_auth()` dependency (currently commented out)

## SIP Trunk Management

Five provider-specific outbound endpoints, all sharing `_create_sip_outbound_trunk()`:
- `/zadarma` — Backward-compatible root endpoint
- `/twilio` — Default address `live-kit-mc.pstn.twilio.com`
- `/plivo` — Supports on-the-fly trunk provisioning + call placement; uses proxied client for India routing
- `/voice_link` — Supports on-the-fly trunk provisioning with the Voicelink proxied client

Inbound trunk endpoints: CRUD + Voicelink variant with auto dispatch rule creation.
End-to-end SIP inbound setup: `/v1/sip/inbound/setup` handles trunk + dispatch rule + provider forwarding.

## Webhook Flow

```python
webhook_handler() → create_dispatch() + await trigger_sip()
                                              ├── SIP participant creation
                                              ├── On success: return 200 with room + token
                                              └── On failure: classify error (408→NoAnswer, 486→Busy),
                                                  delete room, return 503
```

## Capacity Gating

Per-provider concurrency limits enforced via middleware on POST dispatch paths:
- Plivo: 2, Zadarma: 3, VoiceLink: 5, Twilio: 2, Global: 5
- Provider detected from trunk → tracked via room name prefix (`call_{provider}_{call_id}`)
- Blocked calls logged as `Busy` to `call_logs` with reason `provider_at_concurrency_limit`
- `/health` returns `false` when any provider or global pool is saturated
