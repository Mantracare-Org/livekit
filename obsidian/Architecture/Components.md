# Components

## 1. Voice Agent (`mantra/agent.py`)

The core real-time voice AI agent. ~3,007 lines.

**Responsibilities:**
- Connect to LiveKit rooms via `AgentServer`
- Orchestrate STT (Deepgram Nova-3) → LLM (OpenAI/Gemini/DeepSeek) → TTS (LiveKit native sonic-3, no Cartesia dependency)
- Hinglish telesales system prompt (short 1-2 sentence turns, flat prosody) with `<!-- LANGUAGE_DIRECTIVE_START/END -->` per-turn alignment via `MantraMultilingualAgent.llm_node`
- Dynamic STT locale routing (`resolve_stt_language`: `en-IN` for India, `en-US` otherwise, `hi` model for Hindi, `multi` default) + per-call keyterm memory (seeded from context, ≤80 terms, learns names/places per turn, endpointing 100ms)
- Fast VAD barge-in: `interruption.mode="vad"`, `min_duration=0.15s`, `discard_audio_if_uninterruptible=True`; dynamic endpointing (0.10s–0.80s), preemptive TTS on
- Dynamic voice selection from `VOICE_MAPPING` dict (13 voices)
- Call lifecycle: join → converse → goodbye → disconnect
- Inactivity monitoring (15s nudge, 30s silence disconnect; greeting tracked strictly after TTS finishes)
- Farewell safety net (auto-disconnect on goodbye without `end_call`, directional inbound vs outbound phrases)
- Call duration limiter (`mantra/call_duration.py`: 150s farewell / 180s hard; outbound positive-intent extension via `mantra/positive_intent.py` to 270s / 300s)
- Inbound call KB context resolution (DID → `org_configs` → `org_id`) + live process/stage context injection (≤12k chars) + KB collection expansion
- Inbound client recognition: pre-greeting bounded (3s) `recognize_client` using LiveKit SIP participant caller number; injects `client_name` + `client_metadata.ai_summaries/custom_fields`; anonymous fallback
- `search_knowledge_base` function tool for RAG access (silent, single-turn, scheduling questions excluded)
- `clarify_medical_department` + `check_doctor_availability(department?)` function tools (MCP-backed, deterministic org-list validation)
- `end_call` function tool for graceful disconnect (guarded before greeting/user speech)
- Post-call: recording → S3 → LLM analysis (`appointment_metadata`, `user_intent`, `derived_process_id` + stage reconciliation, UTC ISO-8601) → webhook → DB → TOS telemetry
- `transfer_to_human` tool — code preserved but currently disabled/comment-blocked

**LLM Options:**
- `openai` — GPT-4o-mini (default)
- `gemini` — Gemini 2.5 Flash
- `deepseek` — DeepSeek v4 Flash (via OpenAI-compatible API)

---

## 2. API/UI Server (`mantra/ui_server.py`)

FastAPI-based HTTP server. ~4,489 lines.

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
- `GET /health` — Health check (per-trunk + global capacity + all deps)

**Key Details:**
- Three LiveKit API clients: `lk_client` (direct), `plivo_client` (proxied for India), `voicelink_client` (proxied for VoiceLink)
- Trunk-based capacity gating middleware (per-trunk limits: Plivo=2, Zadarma=3, VoiceLink=5, Twilio=3; Global=5); rooms named `call_{trunk_id}_{call_id}`
- Inbound setup (`POST /v1/sip/inbound/setup`) requires explicit `provider` (`zadarma|twilio|plivo|voice_link`); unsupported values rejected before LiveKit provisioning; newly-created trunk/dispatch-rule rolled back on provider-forwarding failure; `org_configs` written only after forwarding succeeds
- Inbound trunk delete (`DELETE /v1/sip/trunks/inbound/{trunk_id}`) cascades: dispatch rules (from `org_configs` + LiveKit list) → trunk → `org_configs` rows → Redis `trunk:provider:{id}`
- Webhook → direct agent dispatch + SIP call (with 503 on SIP failure: 408→No Answer, 486→Busy)
- JWT auth with SHA-256 hashed credentials
- Scanner path suppression in request logging
- Prometheus metrics via `prometheus_fastapi_instrumentator`

---

## 3. Dispatcher (`mantra/dispatcher.py`)

Background queue consumer. ~252 lines.

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

Hybrid FTS + semantic KB over PostgreSQL. ~1,238 lines total (`knowledge_base.py` ~1,120 + `retriever.py` ~118).

**Components:**
- `PostgresKnowledgeBase` — tiered search (strict FTS+vector RRF → loose OR → tag-only → doc listing), org pre-fetch cache, 350ms fast-race embedding guard (`_raced_embed`), in-memory query embedding cache, `warmup()`, CRUD on `kb_pages` + `kb_collections` tables
- `KnowledgeRetriever` — Session-cached wrapper that formats results for LLM with accessed page tracking, `prefetch()` (returns pages; feeds live process-context injection)
- `adaptive_chunk()` + strategies — Heading, paragraph, sliding-window chunking
- Ingestion pipeline — PDF, URL, and raw text ingestion with S3 upload (JSON + Form compatible, embedding-column guard)
- Multi-KB per org via `kb_collections` table — each document = one collection per org (with `process_description`/`stage_description`)

**Endpoints (via `ui_server.py`):**
- `POST /v1/kb/ingest` — File/text/URL ingestion
- `POST /v1/kb/chat` — Test chat against KB
- `DELETE /v1/kb/document` — Delete by document_id

See [[../Features/Knowledge Base.md|Knowledge Base feature doc]] for full details.

---

## 5. MCP (`mcp/server.py` + `mantra/mcp_client.py` + remote `livekit-mcp`)

Local legacy server (`mcp/server.py`, ~1,073 lines, stdio→SSE fixed `transport="sse"`, port 8000) exposes PostgreSQL tools:

**Local tools (13):**
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

**Live tools (remote `livekit-mcp` via `MantraMCPClient`, SSE + dynamic OAuth `OAUTH_CLIENT_ID/SECRET`, browser UA, `ngrok-skip-browser-warning`, `NO_PROXY` bypass):**
- `receive_doctor_availability` — Real-time provider slots (UTC params, `department` aware, past-year roll-forward, `User ID: N` injection → `appointment_metadata.provider_user_id`)
- `get_org_departments` — Org department list for `clarify_medical_department` validation
- `fetch_org_processes` / `receive_org_processes` — Processes + stages for post-call `derived_process_id`/`new_stage_id` reconciliation (10-min TTL cache)
- `recognize_client` — Pre-greeting client lookup (`GET /webhooks/mcp/lead?org_id=&phone=`), returns `client_name` + `client_metadata`

---

## 6. Frontend (`static/`)

| File | Lines | Purpose |
|------|-------|---------|
| `login.html` | 259 | Authentication page (Discord-style dark theme) |
| `index.html` | 475 | Test console — LiveKit room connection, payload config |
| `dashboard.html` | 577 | Operations dashboard — metrics, active calls, history |
| `app.js` | 253 | WebRTC client: room connection, mic, transcript display |
| `dashboard.js` | 206 | Dashboard logic: SSE stream, metrics, call table, activity feed |
