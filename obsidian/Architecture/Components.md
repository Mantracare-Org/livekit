# Components

## 1. Voice Agent (`mantra/agent.py`)

The core real-time voice AI agent. ~995 lines.

**Responsibilities:**
- Connect to LiveKit rooms via `AgentServer`
- Orchestrate STT (Deepgram Nova-3) → LLM (OpenAI/Gemini/DeepSeek) → TTS (Cartesia Sonic-3)
- Bilingual English/Hindi support (detected from user speech)
- Dynamic voice selection from `VOICE_MAPPING` dict (8 voices)
- Call lifecycle: join → converse → goodbye → disconnect
- Inactivity monitoring (10s timeout)
- Farewell safety net (auto-disconnect on goodbye without `end_call`)
- Call duration limiter (3-minute hard kill)
- Post-call: recording → S3 → webhook → DB

**LLM Options:**
- `openai` — GPT-4o-mini (default)
- `gemini` — Gemini 2.5 Flash
- `deepseek` — DeepSeek v4 Flash (via OpenAI-compatible API)

**Tone/Style System:**
- 6 configurable tones (professional, friendly, empathetic, persuasive, educational, motivational)
- 3 configurable styles (concise, balanced, detailed)
- Injected from payload metadata into agent instructions at call start

**DB Write Path:**
- Agent sends call log via HTTP POST to UI Server (`/api/v1/webhooks/call-logs`)
- UI Server writes to PostgreSQL (bypasses cloud security group blocks)

---

## 2. API/UI Server (`mantra/ui_server.py`)

FastAPI-based HTTP server. ~933 lines.

**Endpoints:**
- `POST /api/v1/auth/login` — JWT authentication
- `POST /api/v1/webhooks/telephony` — Primary call trigger
- `POST /api/v1/sip/trunks/outbound/{zadarma|twilio|plivo}` — SIP trunk provisioning
- `GET /api/v1/sip/trunks/outbound` — List trunks
- `DELETE /api/v1/sip/trunks/outbound/{trunk_id}` — Delete trunks
- `POST /dispatch-test` — Manual dispatch for testing
- `GET /api/v1/dashboard/stream` — SSE for real-time metrics
- `GET /api/v1/dashboard/metrics` — Today's call metrics
- `GET /api/v1/dashboard/calls` — Paginated call history
- `GET /api/v1/dashboard/active-calls` — Active calls from Redis
- `GET /health` — Health check

**Key Details:**
- Dual LiveKit API clients: `lk_client` (direct), `plivo_client` (proxied for India)
- Webhook → Redis queue → dispatcher → agent → SIP call flow
- JWT auth with SHA-256 hashed credentials
- Scanner path suppression in request logging

---

## 3. Dispatcher (`mantra/dispatcher.py`)

Background queue consumer. ~172 lines.

**Flow:**
1. Connect to Redis + LiveKit API
2. Every 0.5s: check capacity (`CARTESIA_MAX_CONCURRENCY`, `LIVEKIT_MAX_ROOMS`, `AGENT_MAX_WORKERS`)
3. Pop from `queue:pending` (sorted set, lowest score first)
4. Create agent dispatch + SIP participant
5. On failure: re-queue with incremented score
6. Every 60s: zombie cleanup (remove Redis entries for rooms that no longer exist)

---

## 4. MCP Server (`mcp/server.py`)

Model Context Protocol server for PostgreSQL. ~177 lines.

**Tools:**
- `list_tables` — List all public schema tables
- `describe_table(table_name)` — Column details
- `execute_query(query)` — Read-only SELECT
- `call_logs(log_data)` — Upsert call log by `call_id`

---

## 5. Frontend (`static/`)

| File | Lines | Purpose |
|------|-------|---------|
| `login.html` | 259 | Authentication page (Discord-style dark theme) |
| `index.html` | 475 | Test console — LiveKit room connection, payload config |
| `dashboard.html` | 577 | Operations dashboard — metrics, active calls, history |
| `app.js` | 253 | WebRTC client: room connection, mic, transcript display |
| `dashboard.js` | 206 | Dashboard logic: SSE stream, metrics, call table, activity feed |
