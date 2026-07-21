# Current Sprint

> **Sprint:** N/A (no formal sprint process)  
> **Last Updated:** 2026-07-18  
> **Status:** Active maintenance + incremental features

## In Progress

- [ ] Migrate remaining `mantra/agent.py` tool callbacks to separate module
- [ ] KB audit findings to address:
  - [ ] Implement vector/embedding search (pgvector + OpenAI embeddings) to replace FTS
  - [ ] Remove misleading docstring in `knowledge_base.py` claiming pgvector
  - [ ] Consider upfront KB injection for small KBs (hybrid approach)

## Recently Completed

- [x] **2026-07-21** — 51 automated tests added (pytest): webhook payload, MCP tools, UI server endpoints, utils
- [x] **2026-07-21** — Fixed inbound call webhook payload: now includes `direction` and `inbound_context` (org_id, kb_id, phone_number, provider) for backend correlation
- [x] **2026-07-21** — Fixed MCP `call_logs` tool: was dead code (no SQL), now properly upserts into call_logs table
- [x] **2026-07-21** — Fixed `test_inbound_call` and `create_dispatch_rule` phone_number normalization
- [x] **2026-07-17** — Fix Knowledge Base ingestion UTF8 encoding errors and add detailed logger in `ui_server.py`
- [x] **2026-07-17** — Fix environment variable overriding consistency (`override=True` for `.env.local`)
- [x] **2026-07-16** — KB integration audit: confirmed KB is available to inbound calls via `search_knowledge_base` function tool; documented gap between docs and actual FTS-only implementation
- [x] **2026-07-16** — Local inbound mappings fallback (`inbound_mappings.json`) to test KB + inbound call integration without the external MantraAssist backend. Set `LOCAL_INBOUND_MAPPINGS=1` to skip backend entirely.
- [x] Add color-coded logging for inbound SIP setup payloads in `ui_server.py` — 2026-07
- [x] Cartesia TTS migration to LiveKit Inference — 2026-06
- [x] Dynamic tone/style configurations for agent prompts — 2026-06
- [x] `end_call` tool with graceful disconnect — 2026-05
- [x] Automated crash email notifications — 2026-05
- [x] Webhook-based call log storage — 2026-05

## Blocked

- Plivo proxy routing stability (India infra) — awaiting provider feedback