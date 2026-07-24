# Repository Map

```
/home/assassinsk004/livekit/
├── mantra/                        # Core Python application package
│   ├── __init__.py               # Version string
│   ├── agent.py                  # LiveKit voice agent (1513 lines) ★
│   ├── ui_server.py              # FastAPI web/API server (2143 lines) ★
│   ├── dispatcher.py             # Redis queue-based call dispatcher (203 lines)
│   ├── knowledge_base.py         # Postgres FTS knowledge base (461 lines) ★
│   ├── retriever.py              # KB retriever with session cache (50 lines)
│   ├── utils.py                  # S3, DB, recording, analysis helpers (534 lines)
│   └── email_alerts.py           # SMTP crash alerts with memes (244 lines)
│
├── mcp/                          # Model Context Protocol server
│   ├── server.py                 # Postgres tools (1058 lines)
│   └── README.md                 # MCP server docs
│
├── static/                       # Frontend (no build step)
│   ├── index.html                # Test console (475 lines)
│   ├── app.js                    # WebRTC client (253 lines)
│   ├── dashboard.html            # Operations dashboard (577 lines)
│   ├── dashboard.js              # Dashboard client (206 lines)
│   └── login.html                # Auth page (259 lines)
│
├── obsidian/                     # This knowledge base ★
├── .planning/                    # Pre-vault internal planning docs
├── Dockerfile                    # Multi-stage Docker build
├── entrypoint.sh                 # agent|ui mode selector
├── dev.sh                        # Local development launcher
├── pyproject.toml                # Python project config
├── livekit.toml                  # LiveKit Cloud project config
├── uv.lock                       # Dependency lockfile
├── .env.local                    # Active secrets (gitignored)
├── .env                          # Template env (commented)
├── .gitignore
├── .python-version               # 3.11
└── README.md
```

## Key Architecture: Line Count

| File | Lines | % of Codebase |
|------|-------|---------------|
| `mantra/ui_server.py` | 2143 | 21% |
| `mantra/agent.py` | 1513 | 15% |
| `mcp/server.py` | 1058 | 10% |
| `static/dashboard.html` | 577 | 6% |
| `mantra/utils.py` | 534 | 5% |
| `static/index.html` | 475 | 5% |
| `mantra/knowledge_base.py` | 461 | 5% |
| `static/login.html` | 259 | 3% |
| `static/app.js` | 253 | 3% |
| `mantra/email_alerts.py` | 244 | 2% |
| `static/dashboard.js` | 206 | 2% |
| `mantra/dispatcher.py` | 203 | 2% |
| `mcp/README.md` | 180 | 2% |
| `mantra/retriever.py` | 50 | <1% |

## Git Branches

Active branches (48 total):
- `master` — Main development
- `feat/inbound-calls` — Inbound call support
- `feat/plivo-proxy-routing` — Plivo India routing
- `feature/call-terminate` — Call termination logic
- `integration/gemini`, `integration/deepseek` — LLM integrations
- Various feature/fix branches
