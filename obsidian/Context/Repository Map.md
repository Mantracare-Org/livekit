# Repository Map

```
/home/fardeen/lkt/
├── mantra/                        # Core Python application package
│   ├── __init__.py               # Version string
│   ├── agent.py                  # LiveKit voice agent orchestration shim (901 lines) ★
│   ├── ui_server.py              # FastAPI app assembly shim (225 lines) ★
│   ├── prompts.py                # Telephony prompts, prosody directives & business rules (208 lines)
│   ├── language_manager.py       # Multilingual STT arbitration & turn hysteresis (678 lines)
│   ├── mcp_client.py             # SSE JSON-RPC 2.0 client for remote livekit-mcp (177 lines)
│   ├── knowledge_base.py         # PostgreSQL hybrid search (FTS + semantic pgvector) (1,120 lines) ★
│   ├── gemini_embeddings.py      # Google Gemini vector embeddings + in-memory cache (121 lines)
│   ├── retriever.py              # KB retriever with session cache (121 lines)
│   ├── utils.py                  # S3, DB, recording, analysis, webhook delivery ledger (1,377 lines) ★
│   ├── resend_webhooks.py        # Outbox replay worker for failed webhook deliveries (344 lines)
│   ├── dispatcher.py             # Redis queue-based call dispatcher (252 lines)
│   ├── email_alerts.py           # SMTP crash alerts with pipeline rate-limiting (244 lines)
│   ├── call_duration.py          # Telephony call duration enforcement (65 lines)
│   ├── positive_intent.py        # Positive intent call extension heuristics (70 lines)
│   ├── amd.py                    # Answering machine detection (87 lines)
│   ├── logging_config.py         # Structured logging configuration (59 lines)
│   ├── core/                     # Modular voice agent subsystems
│   │   ├── assistant_functions.py # LiveKit agent tool functions (679 lines)
│   │   ├── call_monitors.py      # Inactivity, silence, and duration monitors (398 lines)
│   │   ├── finalize.py           # Post-call teardown, S3 upload, webhook payload assembly (437 lines)
│   │   ├── inbound.py            # Inbound SIP context resolution & client recognition (361 lines)
│   │   ├── engines.py            # STT, TTS, and LLM engine factory & pre-warming (257 lines)
│   │   ├── room_control.py       # Graceful disconnect & LiveKit room lifecycle (140 lines)
│   │   ├── live_agent.py         # MantraMultilingualAgent subclass & turn alignment (79 lines)
│   │   └── common.py             # Shared agent type definitions & states (67 lines)
│   ├── routers/                  # Decomposed FastAPI HTTP route handlers
│   │   ├── sip.py                # Inbound/outbound SIP trunks & dispatch rules (1,191 lines)
│   │   ├── knowledge.py          # Knowledge base ingestion & query endpoints (668 lines)
│   │   ├── telephony.py          # Primary outbound call trigger webhooks (461 lines)
│   │   ├── dashboard.py          # Metrics, active calls, call logs APIs (254 lines)
│   │   ├── redis_api.py          # Redis inspection APIs (202 lines)
│   │   ├── org_configs.py        # Org configuration CRUD (162 lines)
│   │   ├── pages.py              # HTML static page rendering (51 lines)
│   │   └── auth.py               # JWT authentication router (38 lines)
│   ├── services/                 # Business logic and external integration services
│   │   ├── telephony.py          # SIP participant & call routing orchestrator (833 lines)
│   │   ├── health.py             # Health check & capacity gating logic (273 lines)
│   │   ├── clients.py            # LiveKit API client pool (direct, Plivo, Voicelink) (104 lines)
│   │   ├── webhook_queue.py      # Async webhook delivery queue (101 lines)
│   │   └── capacity.py           # Per-trunk & provider concurrency limits (79 lines)
│   ├── dependencies/             # FastAPI dependency injection providers
│   │   ├── auth.py               # JWT bearer verification (30 lines)
│   │   └── database.py           # Database connection providers (19 lines)
│   └── migrations/               # PostgreSQL schema migrations (001 to 008)
│
├── mcp/                          # Model Context Protocol server (legacy local stdio/SSE)
│   ├── server.py                 # Postgres tools for patients, doctors, appointments (1,073 lines)
│   └── README.md                 # MCP server docs
│
├── static/                       # Frontend web applications (Vanilla JS + dark theme)
│   ├── network.html              # Network monitoring & ping page (1,088 lines)
│   ├── dashboard.html            # Operations dashboard with copy buttons & timeline (942 lines)
│   ├── dashboard.js              # Real-time dashboard client & SSE consumer (871 lines)
│   ├── redis.html                # Redis key-value & stream inspector (781 lines)
│   ├── index.html                # Telephony test console (566 lines)
│   ├── kb_chat.html              # Knowledge base test chat (486 lines)
│   ├── app.js                    # WebRTC test client (377 lines)
│   └── login.html                # JWT login page (260 lines)
│
├── obsidian/                     # Technical Second Brain & Knowledge Base ★
├── Dockerfile                    # Multi-stage production container build
├── dev.sh                        # Local development launcher
├── entrypoint.sh                 # Runtime mode selector (agent | ui | mcp)
├── pyproject.toml                # Project configuration & dependencies
├── livekit.toml                  # LiveKit Cloud configuration
├── uv.lock                       # uv dependency lockfile
├── AGENTS.md                     # Knowledge base directives for AI agents
└── README.md                     # Project documentation
```

## Key Architecture: Line Count

| Module / Component | Lines | Role |
|---|---|---|
| `mantra/utils.py` | 1,377 | Telemetry, post-call DB persistence, delivery ledger |
| `mantra/routers/sip.py` | 1,191 | SIP trunk CRUD & LiveKit dispatch rules |
| `mantra/knowledge_base.py` | 1,120 | PostgreSQL hybrid search & pgvector embeddings |
| `mcp/server.py` | 1,073 | Local MCP server tools & database schema |
| `static/dashboard.html` | 942 | Operations dashboard UI & Call Log Details modal |
| `mantra/agent.py` | 901 | LiveKit voice agent orchestration entrypoint |
| `static/dashboard.js` | 871 | Dashboard real-time client & clipboard handlers |
| `mantra/services/telephony.py` | 833 | SIP call provisioning & dispatch lifecycle |
| `mantra/core/assistant_functions.py` | 679 | Voice agent function tools (KB, Doctor, End Call) |
| `mantra/language_manager.py` | 678 | Multilingual STT arbitration & turn alignment |
| `mantra/routers/knowledge.py` | 668 | Knowledge base ingestion & HTTP APIs |
| `mantra/core/finalize.py` | 437 | Post-call audio mix, S3 upload, webhook dispatch |
| `mantra/core/call_monitors.py` | 398 | Silence, inactivity, and duration monitors |
| `mantra/core/inbound.py` | 361 | Inbound DID resolution & client recognition |
| `mantra/resend_webhooks.py` | 344 | Webhook retry outbox replay worker |
| `mantra/dispatcher.py` | 252 | Redis queue-based call dispatcher |
| `mantra/core/engines.py` | 257 | STT, TTS, and LLM engine prewarming & setup |
| `mantra/ui_server.py` | 225 | FastAPI assembly & application router mounts |
