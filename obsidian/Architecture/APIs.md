# API Reference

## Authentication

### POST /v1/auth/login

Authenticate and get JWT token.

```json
// Request
{ "username": "admin", "password": "secret" }
// Response
{ "token": "jwt...", "expires_in": 86400, "username": "admin" }
```

Auth required for all `/v1/dashboard/*` endpoints via `Authorization: Bearer <token>` header or `?token=` query parameter.

---

## Telephony Webhook

### POST /v1/webhooks/telephony

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
  "ai_payload": {
    "ai_model": "openai",
    "voice_id": "arushi",
    "voice_speed": 1.0
  }
}
```

**Response:** `{ status, client_name, purpose, room, token, url }`

---

## SIP Trunk Management

### POST /v1/sip/trunks/outbound/zadarma

### POST /v1/sip/trunks/outbound/twilio

### POST /v1/sip/trunks/outbound/plivo

### POST /v1/sip/trunks/outbound/voice_link

```json
{
  "name": "My Trunk",
  "address": "sip.example.com",
  "numbers": ["+14155551234"],
  "auth_username": "user",
  "auth_password": "pass"
}
```

### GET /v1/sip/trunks/outbound

List all trunks.

### DELETE /v1/sip/trunks/outbound/{trunk_id}

Delete a trunk.

### POST /v1/sip/trunks/inbound / GET / DELETE / PATCH

Inbound trunk CRUD — create, list, delete, and update inbound SIP trunks. The Voicelink variant (`/v1/sip/trunks/inbound/voicelink`) auto-creates a dispatch rule. `DELETE /v1/sip/trunks/inbound/{trunk_id}` cascades: deletes associated dispatch rules (union of `org_configs.dispatch_rule_id` + LiveKit list) before the trunk, then removes matching `org_configs` rows and the Redis `trunk:provider:{id}` cache entry.

### POST /v1/sip/inbound/setup

End-to-end inbound SIP setup: creates LiveKit inbound trunk + dispatch rule + configures provider SIP forwarding (Zadarma, Twilio, Plivo Zentrunk, VoiceLink). `provider` is **required** (no default; `zadarma|twilio|plivo|voice_link`) and validated before any LiveKit provisioning. On provider-forwarding failure, newly-created trunk/dispatch-rule are rolled back. Accepts `org_id`, `provider`, `number`, `prompt`, `voice`, `model`, `kb_tags`, `transfer_numbers`, `client_name`, `process_id`. Stores config in `org_configs` only after provider forwarding succeeds.

### POST /v1/sip/dispatch-rules / GET / DELETE / PATCH

SIP dispatch rule CRUD for inbound call routing.

---

## Dashboard APIs

All require JWT auth.

### GET /v1/dashboard/metrics

Today's call metrics from PostgreSQL.

**Response:** `{ total_calls, completed_calls, busy_calls, no_answer_calls, error_calls, incomplete_calls, avg_duration_seconds, answer_rate }`

### GET /v1/dashboard/calls?limit=20&offset=0

Paginated call history.

### GET /v1/dashboard/active-calls

Current active calls from Redis.

### GET /v1/dashboard/stream (SSE)

Real-time stream: every 2s sends `{ pending_calls, active_calls, max_concurrency, active_call_details, timestamp }`

---

## Test & Utility

### POST /dispatch-test

Manually dispatch agent to a test room.

```json
{
  "client_name": "Test",
  "call_id": "99999",
  "prompt": "Hello",
  "lead_id": "12345"
}
```

### POST /v1/test/inbound-call

Simulate an inbound call — dispatches agent with `direction: inbound` metadata and triggers a SIP outbound call.

### KB Endpoints

- `POST /v1/kb/ingest` — File/text/URL ingestion with `org_id` + optional `document_id`
- `POST /v1/kb/chat` — Text chat test against KB
- `DELETE /v1/kb/document` — Delete by `org_id` + `document_id`
- `POST /v1/knowledge/upload` — Upload file to KB
- `POST /v1/knowledge/text` — Ingest raw text
- `POST /v1/knowledge/url` — Fetch and ingest URL
- `GET /v1/knowledge/list` — List distinct KB IDs

### Organization Configs

- `GET /v1/org-configs?org_id=X` — List configs
- `GET /v1/org-configs/{phone_number}` — Get specific config
- `PUT /v1/org-configs/{phone_number}` — Update config
- `DELETE /v1/org-configs/{phone_number}` — Soft delete (deactivate)

### GET /config

Returns `{ "url": "wss://..." }` — LiveKit server URL for frontend.

### GET /health

Readiness endpoint. Returns `{ "healthy": true|false }` — `false` when any infrastructure dependency fails OR any trunk is at its concurrency limit OR the global agent pool (`MAX_CALL_CONCURRENCY`, default 5) is saturated.

Checks: LiveKit, Redis, PostgreSQL, Deepgram STT, MantraAssist backend, S3, per-trunk `trunk_capacity_{trunk_id}`, `capacity_max_concurrency`.

---

## Static Pages

| Route        | File             | Description          |
| ------------ | ---------------- | -------------------- |
| `/`          | `login.html`     | Login page           |
| `/dashboard` | `dashboard.html` | Operations dashboard |
| `/console`   | `index.html`     | Test console         |
