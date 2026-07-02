# Feature Index

| Feature | Module | Description |
|---------|--------|-------------|
| [[Voice Agent.md\|Voice Agent]] | `mantra/agent.py` | Real-time STT→LLM→TTS voice conversation |
| [[API Server.md\|API Server]] | `mantra/ui_server.py` | FastAPI HTTP server for webhooks, SIP, dashboard |
| [[Dispatcher.md\|Dispatcher]] | `mantra/dispatcher.py` | Background Redis queue consumer |
| [[MCP Server.md\|MCP Server]] | `mcp/server.py` | PostgreSQL Model Context Protocol server |
| [[Dashboard.md\|Dashboard]] | `static/dashboard.html` + `dashboard.js` | Operations monitoring UI |
| [[Telephony Integration.md\|Telephony Integration]] | `mantra/ui_server.py` | SIP trunk provisioning (Twilio, Plivo, Zadarma) |
| [[Post-Call Processing.md\|Post-Call Processing]] | `mantra/agent.py` + `utils.py` | Recording, analysis, webhook, DB storage |
| [[Crash Alerts.md\|Crash Alerts]] | `mantra/email_alerts.py` | SMTP crash notifications with meme support |
| [[Test Console.md\|Test Console]] | `static/index.html` + `app.js` | Manual agent testing via WebRTC |
| [[Knowledge Base.md\|Knowledge Base]] | `mantra/knowledge_base.py` | Vectorless KB with LLM keyword extraction + Postgres FTS |
