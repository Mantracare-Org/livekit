# Current Sprint

> **Sprint:** N/A (no formal sprint process)  
 > **Last Updated:** 2026-07-23  
 > **Status:** Plivo inbound call fix — migrated from Application XML to Zentrunk SIP trunking

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

- [x] **2026-07-23** — Fixed Plivo inbound call: migrated from Plivo Application XML → Plivo Zentrunk SIP trunking
   1. `<User>` + trunk ID + `;transport=tcp` → `UNALLOCATED_NUMBER`
   2. `<Sip>` + trunk ID + `;transport=tcp` → `Invalid Answer XML`
   3. `<User>` + `+918031321203` + `;transport=tcp` → `End Of XML Instructions` (`+` confuses Plivo's `<User>` parser)
   4. `<User>` + `918031321203` → Plivo executes Dial but LiveKit returns `UNALLOCATED_NUMBER` (Plivo Application approach fundamentally incompatible)
   5. **Zentrunk migration**: `_update_plivo_sip_forwarding` now creates Zentrunk origination URI → inbound trunk → links number directly via Plivo API. Plivo sends SIP INVITE directly to LiveKit's SIP domain without XML intermediary. Deprecated `_build_plivo_xml`, `/api/v1/sip/plivo-xml`, `/api/v1/sip/plivo-dial-status`.
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

## Resolved (was blocked)

- ~~Plivo proxy routing stability (India infra) — awaiting provider feedback~~ → **Root cause found and fixed**: Not a proxy issue. Plivo XML used `<User>` instead of `<Sip>`, causing Plivo to do internal SIP user lookup instead of forwarding to external LiveKit SIP endpoint. `PLIVO_PROXY` is only for API calls, not SIP signaling — proxy was never involved.