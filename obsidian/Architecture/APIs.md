# API Reference

## Authentication

### POST /api/v1/auth/login

Authenticate and get JWT token.

```json
// Request
{ "username": "admin", "password": "secret" }
// Response
{ "token": "jwt...", "expires_in": 86400, "username": "admin" }
```

Auth required for all `/api/v1/dashboard/*` endpoints via `Authorization: Bearer <token>` header or `?token=` query parameter.

---

## Telephony Webhook

### POST /api/v1/webhooks/telephony

Primary endpoint to trigger outbound calls.

```json
{
  "client_phone": "919876543210",
  "client_country_code": "91",
  "client_name": "Anurag",
  "prompt": "You are calling from MantraCare...",
  "call_id": "abc-123",
  "lead_id": "lead-456",
  "trunk_id": "ST_xxx",
  "call_from": "+14155551234",
  "stage_id": 1,
  "stageDetails": [{ "stage_id": 1, "description": "Initial Call" }],
  "client_custom_fields": {},
  "ai_payload": { "ai_model": "openai", "voice_id": "arushi", "voice_speed": 1.0 }
}
```

**Response:** `{ status, client_name, purpose, room, token, url }`

---

## SIP Trunk Management

### POST /api/v1/sip/trunks/outbound/zadarma
### POST /api/v1/sip/trunks/outbound/twilio
### POST /api/v1/sip/trunks/outbound/plivo

```json
{
  "name": "My Trunk",
  "address": "sip.example.com",
  "numbers": ["+14155551234"],
  "auth_username": "user",
  "auth_password": "pass"
}
```

### GET /api/v1/sip/trunks/outbound

List all trunks.

### DELETE /api/v1/sip/trunks/outbound/{trunk_id}

Delete a trunk.

---

## Dashboard APIs

All require JWT auth.

### GET /api/v1/dashboard/metrics

Today's call metrics from PostgreSQL.

**Response:** `{ total_calls, completed_calls, busy_calls, no_answer_calls, error_calls, incomplete_calls, avg_duration_seconds, answer_rate }`

### GET /api/v1/dashboard/calls?limit=20&offset=0

Paginated call history.

### GET /api/v1/dashboard/active-calls

Current active calls from Redis.

### GET /api/v1/dashboard/stream (SSE)

Real-time stream: every 2s sends `{ pending_calls, active_calls, max_concurrency, active_call_details, timestamp }`

---

## Test & Utility

### POST /dispatch-test

Manually dispatch agent to a test room.

```json
{ "client_name": "Test", "call_id": "99999", "prompt": "Hello", "lead_id": "12345" }
```

### GET /config

Returns `{ "url": "wss://..." }` — LiveKit server URL for frontend.

### GET /health

Returns `{ "status": "ok", "service": "ui_server" }`.

---

## Static Pages

| Route | File | Description |
|-------|------|-------------|
| `/` | `login.html` | Login page |
| `/dashboard` | `dashboard.html` | Operations dashboard |
| `/console` | `index.html` | Test console |
