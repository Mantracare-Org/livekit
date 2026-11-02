# TODO

> **Last Updated:** 2026-10-27

## Blocker (Prod Gate)

- [x] **RESOLVED (2026-09-01):** MCP SSE transport fix — `mcp/server.py` was stdio-only; now `mcp.run(transport="sse", port=8000)` and `check_doctor_availability` re-enabled in `agent_tools`.
- [ ] **Ingest KB data for org 66** — `kb_pages` has zero rows. The hybrid retriever works but needs content.
- [ ] **Fix post-call webhook 404** — n8n endpoint missing on ngrok backend
- [ ] **Handoff TTS glitch** — `"..."` residual utterance after `transfer_to_human` causes traceback. Race between tool return and `update_instructions` silence enforcement.
- [ ] **Configure S3** — `AWS_S3_BUCKET_NAME` not set, recordings silently dropped

## High Priority

- [x] **RESOLVED (2026-09-12):** Extract LLM prompts from `agent.py` into separate config/prompts module (`mantra/prompts.py`)
- [x] **RESOLVED (2026-10-06):** KB upfront prompt injection mode — Injected upfront KB content into agent instructions during session warmup
- [x] **RESOLVED (2026-10-19):** Zero cold-start latency & precomputed initial greeting
- [x] **RESOLVED (2026-10-19):** Inbound noise cancellation (ai-coustics Quail & Krisp NC)
- [x] **RESOLVED (2026-10-26):** Dashboard modal one-click copy buttons (Summary, Transcript, Payload)
- [ ] Set up automated test suite (pytest for utils, integration test for call flow)
- [ ] Add input validation to all webhook endpoints
- [ ] Implement Redis Pub/Sub for dispatcher (replace 0.5s polling)
- [ ] **KB: Backfill embeddings on prod** — run migration `006_kb_english_vector.py`/`.sql` then backfill via `docker run <image> backfill` or `POST /v1/kb/backfill-embeddings` on 52.7.20.203 (user's part). Code + scratch validation done 2026-08-09.

## Medium Priority

- [x] **RESOLVED (2026-10-11):** Remove hardcoded `FAREWELL_PHRASES` scanning — replaced with `StopResponse()` and `graceful_disconnect_after_speech()`
- [ ] Add graceful shutdown handler for agent.py
- [ ] Rate limit dispatch-test endpoint
- [ ] Add request ID tracing across webhook → dispatch → agent
- [ ] Improve Plivo proxy error handling (retry with backoff) — note: proxy only covers API calls, not SIP signaling

- [ ] Migrate frontend to a framework (React/Vue) for maintainability
- [ ] Add WebSocket logging stream for real-time agent transcript
- [ ] Create admin user management (currently single hashed user)
- [ ] Add Prometheus metrics endpoint
