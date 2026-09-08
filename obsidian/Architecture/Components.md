# Components

## 1. Voice Agent (`mantra/agent.py`)

The core real-time voice AI agent. ~1,629 lines.

**Responsibilities:**
- Connect to LiveKit rooms via `AgentServer`
- Orchestrate STT (Deepgram Nova-3) → LLM (OpenAI/Gemini/DeepSeek) → TTS (LiveKit native sonic-3, no Cartesia dependency)
- Bilingual English/Hindi support (detected from user speech)
- Dynamic voice selection from `VOICE_MAPPING` dict (13 voices)
- Call lifecycle: join → converse → goodbye → disconnect
- Inactivity monitoring (5s prompt, 10s timeout)
- Farewell safety net (auto-disconnect on goodbye without `end_call`)
- Call duration limiter (2m30s farewell, 3min hard kill)
- Inbound call KB context resolution (`resolve_inbound_context` from PostgreSQL `org_configs`)
- `search_knowledge_base` function tool for RAG access
- `end_call` function tool for graceful disconnect
- Post-call: recording → S3 → LLM analysis → webhook → DB → TOS telemetry
- `transfer_to_human` tool — code preserved but currently disabled/comment-blocked

**LLM Options:**
- `openai` — GPT-4o-mini (default)
- `gemini` — Gemini 2.5 Flash
- `deepseek` — DeepSeek v4 Flash (via OpenAI-compatible API)

---

## 2. API/UI Server (`mantra/ui_server.py`)

FastAPI-based HTTP server. ~3,613 lines.

**Endpoints:**
- `POST /v1/auth/login` — JWT authentication
- `POST /v1/webhooks/telephony` — Primary outbound call trigger
- `POST /v1/sip/trunks/outbound/{zadarma|twilio|plivo|voice_link}` — SIP trunk provisioning
- `GET /v1/sip/trunks/outbound` — List trunks
- `DELETE /v1/sip/trunks/outbound/{trunk_id}` — Delete trunks
- `POST /v1/sip/trunks/inbound` / `GET` / `DELETE` — Inbound trunk CRUD
- `POST /v1/sip/trunks/inbound/voicelink` — Voicelink inbound trunk
- `PATCH /v1/sip/trunks/inbound/{trunk_id}` — Update inbound trunk
- `POST /v1/sip/inbound/setup` — End-to-end inbound SIP provisioning (LiveKit trunk + dispatch rule + provider API)
- `POST /v1/sip/dispatch-rules` / `GET` / `DELETE` / `PATCH` — Dispatch rule CRUD
- `POST /dispatch-test` — Manual dispatch for testing
- `POST /v1/test/inbound-call` — Simulated inbound call testing
- `POST /v1/kb/**` — Knowledge base ingestion, chat, delete
- `GET /v1/knowledge/**` — KB file/text/URL ingestion and listing
- `GET/PUT/DELETE /v1/org-configs/**` — Organization config CRUD
- `GET /v1/dashboard/stream` — SSE for real-time metrics
- `GET /v1/dashboard/metrics` — Today's call metrics
- `GET /v1/dashboard/calls` — Paginated call history
- `GET /v1/dashboard/active-calls` — Active calls from Redis
- `GET /health` — Health check (per-provider + global capacity + all deps)

**Key Details:**
- Three LiveKit API clients: `lk_client` (direct), `plivo_client` (proxied for India), `voicelink_client` (proxied for VoiceLink)
- Per-provider capacity gating middleware (Plivo=2, Zadarma=3, VoiceLink=5, Twilio=2, Global=5)
- Webhook → direct agent dispatch + SIP call (with 503 on SIP failure)
- JWT auth with SHA-256 hashed credentials
- Scanner path suppression in request logging
- Prometheus metrics via `prometheus_fastapi_instrumentator`

---

## 3. Dispatcher (`mantra/dispatcher.py`)

Background queue consumer. ~223 lines.

**Flow:**
1. Connect to Redis + LiveKit API
2. Every 0.5s: check capacity (`MAX_CONCURRENCY`, `LIVEKIT_MAX_ROOMS`, `AGENT_MAX_WORKERS`)
3. Pop from `queue:pending` (sorted set, lowest score first)
4. Create agent dispatch + SIP participant
5. On failure: re-queue with incremented score
6. Every 60s: zombie cleanup (remove Redis entries for rooms that no longer exist)
7. TOS telemetry on dequeue/dispatch/failure events

---

## 4. Knowledge Base (`mantra/knowledge_base.py` + `mantra/retriever.py`)

Vectorless KB with PostgreSQL Full-Text Search. ~619 lines total.

**Components:**
- `PostgresKnowledgeBase` — FTS search, CRUD on `kb_pages` + `kb_collections` tables
- `KnowledgeRetriever` — Session-cached wrapper that formats results for LLM with accessed page tracking
- `adaptive_chunk()` + strategies — Heading, paragraph, sliding-window chunking
- Ingestion pipeline — PDF, URL, and raw text ingestion with S3 upload
- Multi-KB per org via `kb_collections` table — each document = one collection per org

**Endpoints (via `ui_server.py`):**
- `POST /v1/kb/ingest` — File/text/URL ingestion
- `POST /v1/kb/chat` — Test chat against KB
- `DELETE /v1/kb/document` — Delete by document_id

See [[../Features/Knowledge Base.md|Knowledge Base feature doc]] for full details.

---

## 5. MCP Server (`mcp/server.py`)

Model Context Protocol server for PostgreSQL. ~1,073 lines.

**Tools (10+):**
- `list_tables` — List all public schema tables
- `describe_table(table_name)` — Column details
- `execute_query(query)` — Read-only SELECT
- `call_logs(log_data)` — Upsert call log by `call_id`
- `get_patient_info(identifier)` — Look up patient by phone/ID
- `get_hospitals()` — List hospital locations
- `get_doctors(hospital)` — List doctors, optionally filtered
- `get_available_slots(doctor_id, date)` — Check appointment slots
- `create_appointment(...)` — Book a new appointment
- `update_appointment(appointment_id, updates)` — Update/reschedule/cancel
- `get_appointments(...)` — Query appointments with filters
- `get_call_history(identifier)` — Retrieve call history for a patient
- `get_db_status()` — Connection status and table stats

---

## 6. Frontend (`static/`)

| File | Lines | Purpose |
|------|-------|---------|
| `login.html` | 259 | Authentication page (Discord-style dark theme) |
| `index.html` | 475 | Test console — LiveKit room connection, payload config |
| `dashboard.html` | 577 | Operations dashboard — metrics, active calls, history |
| `app.js` | 253 | WebRTC client: room connection, mic, transcript display |
| `dashboard.js` | 206 | Dashboard logic: SSE stream, metrics, call table, activity feed |
