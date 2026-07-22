# Current Sprint

> **Sprint:** N/A (no formal sprint process)  
> **Last Updated:** 2026-07-22  
> **Status:** Pre-prod hardening — 6 issues found in call review

## In Progress

- [ ] **BLOCKER:** Fix MCP server — `CstdioServerParameters` attribute missing in `livekit.agents.llm.mcp` (upstream API changed)
- [ ] **BLOCKER:** Ingest KB data for org 66 — `kb_pages` table has zero rows for this org
- [ ] Fix post-call webhook 404 — n8n endpoint missing on ngrok backend
- [ ] Fix handoff TTS glitch — silence instructions race with tool return producing `"..."` utterance
- [ ] Set `AWS_S3_BUCKET_NAME` or suppress recording errors
- [ ] Migrate remaining `mantra/agent.py` tool callbacks to separate module

## Inbound Call Review (2026-07-22)

Live Plivo call vetted end-to-end. **Inbound flow is solid.** Issues found are in periphery:

| Area | Verdict | Issue |
|------|---------|-------|
| Inbound webhook (Plivo → FastAPI) | ✅ | Works |
| Inbound context resolution (DB) | ✅ | Resolved org 66 from phone number |
| Agent deployment (LiveKit) | ✅ | Room created, agent answered |
| STT / LLM / TTS pipeline | ✅ | Full conversation cycled |
| KB search | ❌ | No data for org 66 |
| Handoff (`transfer_to_human`) | ✅ (w/ glitch) | SIP participant added, but `"..."` TTS error |
| Post-call webhook | ❌ | 404 to n8n |
| Recordings | ❌ | S3 not configured |

## Recently Completed

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