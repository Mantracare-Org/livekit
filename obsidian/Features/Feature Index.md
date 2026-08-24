# Feature Index

| Feature | Module | Description |
|---------|--------|-------------|
| Voice Agent | `mantra/agent.py` | Real-time STT→LLM→TTS voice pipeline with KB search and end_call tools |
| API Server | `mantra/ui_server.py` | FastAPI HTTP server: webhooks, SIP trunks, per-provider capacity gating, KB ingestion, dashboard |
| Dispatcher | `mantra/dispatcher.py` | Redis queue consumer for legacy call dispatch |
| MCP Server | `mcp/server.py` | 13 PostgreSQL tools: patients, doctors, hospitals, appointments, call logs |
| Dashboard | `static/dashboard.html` + `dashboard.js` | OpsCraft dark theme operations dashboard with SSE |
| Telephony Integration | `mantra/ui_server.py` | 4-provider SIP support: Twilio, Plivo (Zentrunk), Zadarma, VoiceLink |
| Post-Call Processing | `mantra/agent.py` + `utils.py` | Recording → S3 → LLM analysis → webhook → DB |
| Crash Alerts | `mantra/email_alerts.py` | SMTP crash notifications with meme images for admin |
| Test Console | `static/index.html` + `app.js` | Manual agent testing via WebRTC |
| Knowledge Base | `mantra/knowledge_base.py` + `retriever.py` | PostgreSQL FTS with adaptive chunking, multi-KB collections per org |
| Doctor Availability Tool | `mantra/agent.py` | Mid-call dynamic doctor availability lookup via `livekit-mcp` with international timezone detection |
| MCP Backend Guide | `obsidian/Features/MCP Integration Guide for Backend.md` | Complete Node.js / TypeScript integration guide and code samples for backend developers |
| KB Chat | `static/kb_chat.html` | Text-based KB testing UI |
| Network Monitor | `static/network.html` | Network monitoring page |
