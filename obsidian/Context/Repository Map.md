# Repository Map

```
/home/fardeen/lkt/
├── mantra/                        # Core Python application package
│   ├── __init__.py               # Version string
│   ├── agent.py                  # LiveKit voice agent (995 lines) ★
│   ├── ui_server.py              # FastAPI web/API server (933 lines) ★
│   ├── dispatcher.py             # Redis queue-based call dispatcher (172 lines)
│   ├── utils.py                  # S3, DB, recording, analysis helpers (476 lines)
│   └── email_alerts.py           # SMTP crash alerts with memes (201 lines)
│
├── mcp/                          # Model Context Protocol server
│   ├── server.py                 # Postgres tools (177 lines)
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
| `mantra/agent.py` | 995 | 21% |
| `mantra/ui_server.py` | 933 | 20% |
| `static/dashboard.html` | 577 | 12% |
| `mantra/utils.py` | 476 | 10% |
| `static/index.html` | 475 | 10% |
| `mantra/dispatcher.py` | 172 | 4% |
| `mcp/server.py` | 177 | 4% |
| `mantra/email_alerts.py` | 201 | 4% |
| Other | ~718 | 15% |

## Git Branches

Active branches (48 total):
- `master` — Main development
- `feat/inbound-calls` — Inbound call support
- `feat/plivo-proxy-routing` — Plivo India routing
- `feature/call-terminate` — Call termination logic
- `integration/gemini`, `integration/deepseek` — LLM integrations
- Various feature/fix branches
