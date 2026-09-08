# TODO

## Blocker (Prod Gate)

- [ ] **MCP server broken** — `livekit.agents.llm.mcp` missing `CstdioServerParameters`. Likely an upstream API change. DB tool unavailable to agent.
- [ ] **Ingest KB data for org 66** — `kb_pages` has zero rows. The FTS retriever works but needs content.
- [ ] **Fix post-call webhook 404** — n8n endpoint missing on the ngrok backend at `MANTRAASSIST_BACKEND_URL`
- [ ] **Handoff TTS glitch** — `"..."` residual utterance after `transfer_to_human` causes traceback. Race between tool return and `update_instructions` silence enforcement.
- [ ] **Configure S3** — `AWS_S3_BUCKET_NAME` not set, recordings silently dropped

## High Priority

- [ ] Extract LLM prompts from `agent.py` into separate config/prompts module
- [ ] Set up automated test suite (pytest for utils, integration test for call flow)
- [ ] Add input validation to all webhook endpoints
- [ ] Implement Redis Pub/Sub for dispatcher (replace 0.5s polling)
- [ ] **KB: Backfill embeddings on prod** — run migration `006_kb_english_vector.py`/`.sql` then backfill via `docker run <image> backfill` or `POST /v1/kb/backfill-embeddings` on 52.7.20.203 (user's part). Code + scratch validation done 2026-08-09.
- [ ] **KB: Add upfront prompt injection mode** — For small KBs, inject content into system prompt for zero-latency access

## Medium Priority

- [ ] Add graceful shutdown handler for agent.py
- [ ] Rate limit dispatch-test endpoint
- [ ] Add request ID tracing across webhook → dispatch → agent
- [ ] Improve Plivo proxy error handling (retry with backoff) — note: proxy only covers API calls, not SIP signaling

- [ ] Migrate frontend to a framework (React/Vue) for maintainability
- [ ] Add WebSocket logging stream for real-time agent transcript
- [ ] Create admin user management (currently single hashed user)
- [ ] Add Prometheus metrics endpoint
- [ ] Replace hardcoded `FAREWELL_PHRASES` with configurable list
