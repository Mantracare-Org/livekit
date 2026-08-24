# Mantra Voice Agent — Knowledge Base

> **Version:** 0.4.0  
> **Package:** `livekit-agent`  
> **Repository:** `git@github.com:FardeenSK004/livekit.git` (fork of Mantracare-Org/livekit)  
> **Last Updated:** 2026-08-03

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

| Metric             | Value                                  |
| ------------------ | -------------------------------------- |
| Python modules     | 7 (`mantra/`)                          |
| Frontend files     | 5 (`static/`)                          |
| MCP server         | 1 (`mcp/server.py`)                    |
| Total source lines | ~8,800                                 |
| Core agent file    | `mantra/agent.py` — 1,629 lines        |
| API server file    | `mantra/ui_server.py` — 3,613 lines    |
| MCP server file    | `mcp/server.py` — 1,073 lines          |
| KB module          | `mantra/knowledge_base.py` — 561 lines |

---

## Recent Changelog

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
