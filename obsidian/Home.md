# Mantra Voice Agent — Knowledge Base

> **Version:** 0.5.0  
> **Package:** `livekit-agent`  
> **Repository:** `git@github.com:FardeenSK004/livekit.git` (fork of Mantracare-Org/livekit)  
> **Last Updated:** 2026-10-27

---

## Quick Links

| Area              | Document                                                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 🏛️ Architecture   | [[Architecture/Overview.md\|Overview]] · [[Architecture/MCP System Overview.md\|MCP Architecture]] · [[MCP Architecture.canvas\|MCP Interactive Canvas]] · [[Architecture/Components.md\|Components]] · [[Architecture/Data Flow.md\|Data Flow]] |
| 🌐 APIs           | [[Architecture/APIs.md\|API Reference]]                                                                                        |
| 🗄️ Database       | [[Architecture/Database.md\|Database Schema]]                                                                                  |
| ⚙️ Infrastructure | [[Architecture/Infrastructure.md\|Infrastructure]]                                                                             |
| 🎯 Features       | [[Features/Feature Index.md\|Feature Index]] · [[Features/MCP Integration Guide for Backend.md\|MCP Backend Guide]] · [[Features/Doctor Availability Tool.md\|Doctor Availability]] |
| 📋 Development    | [[Development/TODO.md\|TODO]] · [[Development/Changelog.md\|Changelog]] · [[Development/Bugs.md\|Bugs]]                        |
| 🧠 Knowledge      | [[Knowledge/Coding Standards.md\|Coding Standards]] · [[Knowledge/Conventions.md\|Conventions]]                                |
| 📖 Context        | [[Context/Project Summary.md\|Project Summary]] · [[Context/Stack.md\|Stack]] · [[Context/Repository Map.md\|Repository Map]]  |

---

## Project Identity

**Mantra Voice Agent** is a production-grade, low-latency bilingual (English/Hindi) voice AI agent for telephony. Built on [[Context/Stack.md#LiveKit\|LiveKit]], it orchestrates an STT → LLM → TTS pipeline for real-time voice conversations over SIP telephony trunks (Twilio, Plivo, Zadarma).

## Architecture Snapshot

```
Telephony Provider → Webhook → FastAPI → Agent Dispatch → LiveKit Cloud → Voice Agent
                        (with per-provider                │
                         capacity gating)          STT → LLM → TTS
                                                         │
                                                  Post-Call: S3 + Webhook + DB + TOS
```

## Key Stats

| Metric             | Value                                     |
| ------------------ | ----------------------------------------- |
| Python packages    | `mantra/` (incl. `core/`, `routers/`, `services/`, `dependencies/`, `migrations/`) |
| Frontend files     | 9 (`static/` incl. `dashboard.html`, `dashboard.js`, `index.html`, `kb_chat.html`) |
| MCP server         | 1 (`mcp/server.py`, legacy local) + remote `livekit-mcp` (SSE, OAuth) |
| Total source lines | ~14,000 across core engine and web services |
| Core agent shim    | `mantra/agent.py` — 901 lines (core logic modularized in `mantra/core/`) |
| API server shim    | `mantra/ui_server.py` — 225 lines (routes decomposed into `mantra/routers/`) |
| MCP server file    | `mcp/server.py` — 1,073 lines             |
| KB module          | `mantra/knowledge_base.py` — 1,120 lines  |

---

## Recent Changelog

- **2026-10-26:** Dashboard Call Log Details one-click Copy buttons (Summary, multi-turn formatted dialogue Transcript, raw PostgreSQL JSON Payload) with interactive SVG visual feedback and 2-second timeout state reset
- **2026-10-19:** Zero Cold-Start Latency & Precomputed Greeting (`tts_engine.prewarm()`, `stt_engine.prewarm()`, `llm_engine.prewarm()`, instant `participant_connected` event listener, background LLM greeting precomputation during phone ringing), ai-coustics Quail (`livekit-plugins-ai-coustics` `QUAIL_L` / `QUAIL_VF_S`) & native C++ Krisp inbound noise cancellation, VAD barge-in strictness reduction (`min_words=5`)
- **2026-10-12:** Prompt forwarding to `/v1/webhooks/n8n/summary`; bidirectional JEV appointment extraction & stage evaluation in MantraAssist API
- **2026-10-11:** End Call `StopResponse()` speech suppression, definitive `graceful_disconnect_after_speech()`, recipient `client_name` outbound prompt injection, opening prompt pause adherence, and farewell safety net elimination
- **2026-10-09:** Marathi (`mr`) language enablement with strict prompt guards and prosody directives
- **2026-10-06:** Zero-runtime outbound KB pre-fetching and upfront prompt injection during session warmup
- **2026-09-25 to 2026-09-29:** Natural voice prosody punctuation (em-dashes, semicolons), DeepSeek-V3 low-latency migration (`deepseek-chat`), Zadarma SIP domain fix, geopy hospital branch locator & products/services MCP tools
- **2026-09-12:** Monolith Split — `mantra/ui_server.py` and `mantra/agent.py` decomposed into modular packages (`mantra/core/`, `mantra/routers/`, `mantra/services/`, `mantra/dependencies/`, `mantra/prompts.py`)
- **2026-09-10:** Inbound SIP trunk lifecycle — explicit `provider` required (no default), rollback of newly-created trunk/dispatch-rule on provider-forwarding failure, cascading delete (dispatch rules → trunk → `org_configs` → Redis `trunk:provider`)
- **2026-09-09:** `clarify_medical_department` + availability MCP routing (KB excluded for scheduling, failed department discovery no longer blocks MCP), VAD barge-in (`vad` mode, 0.15s), inbound client recognition metadata (`ai_summaries` + `custom_fields`)
- **2026-09-08:** Inbound client recognition via LiveKit SIP participant caller number → `recognize_client` (`GET /webhooks/mcp/lead`); `/api/*` → `/v1/*` route prefix migration
- **2026-09-04/05:** Hinglish telesales prompt, `en-IN`/`hi`/Deepgram keyterm memory (per-call, 80 terms), endpointing 100ms, code-switching stability
- **2026-09-03:** Live process/stage context injection into agent instructions; outbound call extension to 5 min on positive intent
- **2026-09-02:** MCP Cloudflare WAF bypass (`NO_PROXY` + browser UA headers); AuthMiddleware public paths (`/sitemap.xml`, `/robots.txt`)
- **2026-09-01:** MCP SSE transport fix + `check_doctor_availability` re-enable
- **2026-08-03:** Trunk-based capacity gating (Plivo=2, Zadarma=3, VoiceLink=5, Twilio=3 per trunk), zombie room cleanup, DB migration for caller/called/trunk fields, kb_collections process/stage descriptions
- **2026-08-02:** Inbound webhook `org_id`/`process_id`/`new_stage_id` string→int coercion; language matching + STT `multi`
- **2026-08-01:** Per-provider call capacity gating (Plivo=2, Zadarma=3, VoiceLink=5, Twilio=2), Plivo Zentrunk trunk reuse & retry self-healing, SIP failure → 503 response, health gate per-provider rejection logging, Voicelink SIP inbound/outbound integration
- **2026-07-27:** TTS migration to LiveKit native `sonic-3` (removed Cartesia dependency), TOS telemetry refinement, directional farewell detection
- **2026-07-26:** TOS telemetry pipeline across agent/dispatcher/ui_server, health gate middleware, Redis dedup lock on webhooks
- **2026-07-29:** Multi-KB per org — KB collections (`kb_collections` table), inbound KB document metadata tracking
- **2026-06:** Dynamic tone/style configurations, `end_call` tool, crash emails, webhook-based call log storage

---

## Repository Status

- **Deployment:** LiveKit Cloud (`mantraassist-0ek43ife`)
- **Testing:** Manual only (no automated test suite)
- **Docs:** Obsidian vault at `obsidian/`
- **Planning:** `.planning/codebase/` contains pre-vault architecture docs
