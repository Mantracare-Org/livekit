# Mantra Voice Agent — Knowledge Base

> **Version:** 0.1.0  
> **Package:** `livekit-agent`  
> **Repository:** `git@github.com:FardeenSK004/livekit.git` (fork of Mantracare-Org/livekit)  
> **Last Updated:** 2026-06-30

---

## Quick Links

| Area | Document |
|------|----------|
| 🏛️ Architecture | [[Architecture/Overview.md\|Overview]] · [[Architecture/Components.md\|Components]] · [[Architecture/Data Flow.md\|Data Flow]] |
| 🌐 APIs | [[Architecture/APIs.md\|API Reference]] |
| 🗄️ Database | [[Architecture/Database.md\|Database Schema]] |
| ⚙️ Infrastructure | [[Architecture/Infrastructure.md\|Infrastructure]] |
| 🎯 Features | [[Features/Feature Index.md\|Feature Index]] |
| 📋 Development | [[Development/TODO.md\|TODO]] · [[Development/Changelog.md\|Changelog]] · [[Development/Bugs.md\|Bugs]] |
| 🧠 Knowledge | [[Knowledge/Coding Standards.md\|Coding Standards]] · [[Knowledge/Conventions.md\|Conventions]] |
| 📖 Context | [[Context/Project Summary.md\|Project Summary]] · [[Context/Stack.md\|Stack]] · [[Context/Repository Map.md\|Repository Map]] |

---

## Project Identity

**Mantra Voice Agent** is a production-grade, low-latency bilingual (English/Hindi) voice AI agent for telephony. Built on [[Context/Stack.md#LiveKit\|LiveKit]], it orchestrates an STT → LLM → TTS pipeline for real-time voice conversations over SIP telephony trunks (Twilio, Plivo, Zadarma).

## Architecture Snapshot

```
Telephony Provider → Webhook → FastAPI → Redis Queue → Dispatcher → LiveKit Cloud → Voice Agent
                                                                                       │
                                                                                  STT → LLM → TTS
                                                                                       │
                                                                                  Post-Call: S3 + Webhook + DB
```

## Key Stats

| Metric | Value |
|--------|-------|
| Python modules | 6 (`mantra/`) |
| Frontend files | 5 (`static/`) |
| MCP server | 1 (`mcp/server.py`) |
| Total source lines | ~4,724 |
| Core agent file | `mantra/agent.py` — 995 lines |
| API server file | `mantra/ui_server.py` — 933 lines |

---

## Recent Changelog

- **2026-06-30:** Cartesia TTS migrated to LiveKit Inference, removed redundant API key management, added env var fallbacks for MAX_CONCURRENCY
- **2026-06:** Dynamic tone/style configurations for agent prompts, emotional tone optimization for Cartesia
- **2026-05:** `end_call` tool with graceful disconnect, automated crash email notifications, webhook-based call log storage

---

## Repository Status

- **Deployment:** LiveKit Cloud (`mantraassist-0ek43ife`)
- **Testing:** Manual only (no automated test suite)
- **Docs:** Obsidian vault at `obsidian/`
- **Planning:** `.planning/codebase/` contains pre-vault architecture docs
