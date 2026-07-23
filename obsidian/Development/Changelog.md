# Changelog

## 2026-07-23

- **fix:** Plivo inbound call routing — `_build_plivo_xml` used `<User>` (Plivo internal SIP user lookup) instead of `<Sip>` (external SIP forward). Caused `UNALLOCATED_NUMBER` on every inbound call. Fixed at `mantra/ui_server.py:959`.
- **doc:** Updated Obsidian vault: Current Sprint, Changelog — closed the "Plivo proxy routing stability" blocker with root cause

## 2026-07-22

- **analysis:** Inbound call + KB prod-readiness review on live Plivo call (org 66)
- **bug:** MCP server fails at startup — `module 'livekit.agents.llm.mcp' has no attribute 'CstdioServerParameters'` — DB tool unavailable to agent
- **bug:** KB retriever returned zero results for org 66 across 5 queries — likely empty `kb_pages` table, not a code issue
- **bug:** Post-call webhook to n8n returns 404 — ngrok endpoint lacks `/webhooks/n8n` route
- **bug:** Handoff TTS glitch — residual `"..."` utterance causes `APIError` traceback after `transfer_to_human` (race between tool return and silence instructions)
- **ops:** `AWS_S3_BUCKET_NAME` not set — recordings skipped
- **doc:** Confirmed `transfer_to_human` is fully implemented (not commented out as TODO claimed)
- **doc:** Updated Obsidian vault: Current Sprint, TODO, Components, Voice Agent, Architecture Overview

## 2026-07-21

- **fix:** Inbound call webhook payload now includes `direction`, `inbound_context` (org_id, kb_id, phone_number, provider) so MantraAssist backend can correlate inbound call results
- **fix:** Resolved inbound context (org_id, kb_id, etc.) now flows through to `finalize()` via `_effective_call_metadata` closure variable instead of being lost during metadata re-parse
- **fix:** MCP `call_logs` tool was broken (no SQL executed) — rewritten to properly upsert into `call_logs`
- **fix:** `test_inbound_call` and `create_dispatch_rule` endpoints now normalize `phone` → `phone_number` so agent can resolve inbound context
- **safety:** All new code paths wrapped in try/except with `non-fatal` logging; direction defaults to `"outbound"` so outbound system is completely unaffected
- **logging:** Added structured logging for webhook payload construction (direction, call_status, call_id) and inbound context addition

## 2026-07-18

- **feat:** DB Inbound Context Resolution: Added `org_configs` table and integration in `/api/v1/sip/inbound/setup` to map incoming phone numbers to organizations in the database. The agent now queries this DB first for context (prompt, voice, KB scope), supplementing the MantraAssist API. Added `/api/v1/org-configs` CRUD endpoints for backend management.
- **feat:** Add color-coded logging for inbound SIP setup requests/responses in `mantra/ui_server.py`
- **fix:** Knowledge Ingestion Encoding: Stripped null bytes (`\x00`) recursively from metadata and text inputs in `PostgresKnowledgeBase.add_page` and `delete_by_document` to prevent `CharacterNotInRepertoireError` (invalid byte sequence for UTF8).
- **feat:** Ingestion Logger: Added request parameters logging at the start of `/api/v1/kb/ingest` in `mantra/ui_server.py`.
- **refactor:** Env Loading: Updated `ui_server.py`, `dispatcher.py`, and migrations to load `.env` first and override with `.env.local` using `override=True` to resolve environment conflicts (e.g., Redis host/port).

## 2026-07-16

- **feat:** Added local inbound mappings fallback — `inbound_mappings.json` for testing KB + inbound call integration without the external MantraAssist backend
  - `resolve_inbound_context()` now falls back to local JSON config when the backend is unreachable
  - Set `LOCAL_INBOUND_MAPPINGS=1` env var to skip backend entirely and use local mappings only
- **doc:** Synced Obsidian vault with actual codebase state after KB audit
  - `Features/Knowledge Base.md` — Corrected from "pgvector + upfront prompt injection" to "PostgreSQL FTS + function tool RAG"; added known gaps, tag filtering docs, and accurate schema
  - `Architecture/Components.md` — Fixed line counts (agent.py: 1513, ui_server.py: 2143), added KB module section
  - `Architecture/Data Flow.md` — Added KB context resolution steps to inbound call flow
  - `Context/Repository Map.md` — Fixed line counts, added `knowledge_base.py` and `retriever.py`
  - `Context/Stack.md` — Updated PostgreSQL description to include KB FTS
  - `Home.md` — Fixed stats (7 modules, 6,206 total lines)

## 2026-07-03

- **feat:** Knowledge Base: Implemented absolute override 5-rule framework to force agent to answer factual questions directly (overriding strict prompt constraints like "never give advice")
- **refactor:** Knowledge Base: Made all prompt rules completely generic and industry-agnostic, removing hardcoded references to specific verticals like OCD/ERP

## 2026-06-30

- **doc:** Created `obsidian/` — comprehensive Obsidian knowledge base (48 files)
  - `Architecture/` — 8 files (overview, components, data flow, APIs, DB, infra, decisions, deps)
  - `Features/` — 10 files (index + 9 feature pages covering all modules)
  - `Development/` — 7 files (sprint, TODO, backlog, bugs, changelog, releases, roadmap)
  - `Agents/` — 6 files (master, backend, frontend, devops, docs, QA agent guides)
  - `Knowledge/` — 7 files (standards, conventions, best practices, commands, debugging, env, glossary)
  - `Context/` — 5 files (project summary, stack, repo map, external services)
  - `Templates/` — 3 file templates
  - `Inbox/` — placeholder
- **doc:** Updated root `README.md` to reference the Obsidian vault

## 2026-06-15

- **refactor:** Renamed `CARTESIA_MAX_CONCURRENCY` to `MAX_CONCURRENCY` + env var fallback
- **refactor:** Migrated Cartesia TTS to LiveKit Inference, removed redundant API key management
- **feat:** Webhook-based call log storage, updated DB query schemas
- **feat:** Dynamic tone and style configurations for agent prompts

## 2026-05

- **feat:** Emotional tone optimization for Cartesia voice synthesis
- **feat:** `end_call` tool with graceful disconnect + safety net
- **feat:** Automated crash email notifications
- **feat:** Gemini LLM integration
- **feat:** DeepSeek LLM integration
- **feat:** Plivo India proxy routing
- **feat:** Redis queue-based dispatcher system
- **feat:** Dashboard with SSE real-time metrics
- **feat:** JWT authentication
- **refactor:** Bilingual STT (Deepgram Hindi model)
- **refactor:** Custom ColorFormatter for multi-process logging