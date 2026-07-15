# LKT Workspace

[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-blue)](https://livekit.io/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-black?logo=github)](https://github.com/Mantracare-Org/livekit)

Welcome to the [Mantracare-Org](https://github.com/Mantracare-Org/livekit) workspace, a collection of advanced voice AI agents and real-time communication tools optimized for high-performance telephony.

---

## 🤖 Projects

### Mantra Voice Agent (Bilingual)

A low-latency, human-like voice agent designed for professional care support and outbound follow-up calls.

#### 🛠 Optimized Tech Stack

- **STT:** [Deepgram Nova-3](https://www.deepgram.com/) (Configured for `hi` Multilingual support)
- **LLM:** [OpenAI GPT-4o-Mini](https://openai.com/) (Fast reasoning & process-driven responses)
- **TTS:** [Cartesia Sonic-3](https://cartesia.ai/) (Multilingual Native English/Hindi synthesis)
- **VAD & Turn Detection:** Silero VAD + Multilingual Turn Detection (Optimized with PyTorch)
- **Knowledge Base:** PostgreSQL + pgvector + OpenAI text-embedding-3-small (Semantic search, multi-KB isolation)

---

## 🚀 Deployment & Usage

### Webhook-Driven Outbound Calls

The agent is integrated with a SIP-based outbound system. Trigger calls by sending a POST request to:
`http://<your-ip>:8081/api/v1/webhooks/telephony`

### Local Development

1. **Install Dependencies:**

   ```bash
   uv sync
   ```

2. **Start the Agent and Server:**

   ```bash
   ./dev.sh
   ```

   _This script runs both the Voice Agent and the UI Server._

3. **Access the Interface:**
   Visit `http://localhost:8081` to monitor and trigger tests.

### Infrastructure & Logging

This project relies on an isolated database environment to store call logs, transcripts, and knowledge base data.

- **PostgreSQL (with pgvector extension):** Stores call logs and the knowledge base vectors.
  - **Required:** `pgvector` extension must be installed on the PostgreSQL server.
    - Ubuntu/Debian: `sudo apt-get install postgresql-16-pgvector`
    - Or compile from source: https://github.com/pgvector/pgvector
  - Run the KB migration once: `python mantra/migrations/001_kb_pages.py`
- **Redis:** Used for capacity management and connection state routing, running locally on port `6379`.
- **Logging Pipeline:** Call timelines, statuses, recording URLs, and detailed JSON payloads are automatically saved into the isolated `call_logs_db` after every call.

---

### Environment Variables (Knowledge Base)

| Variable | Description | Default |
|----------|-------------|---------|
| `EMBEDDING_MODEL` | OpenAI embedding model | `text-embedding-3-small` |
| `EMBEDDING_API_KEY` | API key for embeddings (falls back to OPENAI_API_KEY) | — |
| `KB_SIMILARITY_THRESHOLD` | Minimum cosine similarity for KB results | `0.7` |
| `KB_MAX_CHUNK_TOKENS` | Max tokens per chunk before sub-chunking | `2000` |
| `OPENAI_API_KEY` | Required for embeddings if EMBEDDING_API_KEY not set | — |

---

### ✨ Key Features

- **Native Bilingual Intelligence:** Flawlessly switches between English and Hindi based on the caller's preference.
- **Telephony-First VAD:** Tuned thresholds to filter background noise and cellular interference.
- **Romanized Stability:** Optimized for high-quality Cartesia synthesis using transliterated Hinglish.
- **Modern UI:** Premium glassmorphism dashboard with real-time transcript synchronization.
- **Dynamic Context:** Automatically ingests JSON metadata from SIP triggers to provide personalized care.
- **Vector Knowledge Base (New):** 
  - **Multi-KB Isolation:** Each agency gets its own `kb_id` — zero cross-KB leakage.
  - **Semantic Search:** OpenAI embeddings + pgvector for intent-based retrieval.
  - **3-Way Ingestion:** Upload PDF/TXT/MD, paste raw text, or fetch from URL — all via dashboard.
  - **Adaptive Chunking:** Auto-detects document structure (headings → paragraphs → sliding window).
  - **Payload-Routed Queries:** The call payload's `kb_id` field determines which KB the agent queries.

---

## 📊 Dashboard

The dashboard at `http://<host>:8081/dashboard` provides:

- **Real-time metrics:** Active calls, queue depth, capacity gauge
- **Call history:** Paginated, filterable table with recordings and summaries
- **Activity feed:** Live SSE stream of call events
- **Knowledge Base Management:** 
  - **Upload File** — PDF, TXT, or MD → auto-chunked & embedded
  - **Paste Text** — Raw text + optional title → indexed instantly
  - **From URL** — Fetch, extract readable content, embed
  - Each upload tagged with `kb_id` for agent routing

---

## 🐳 Docker

### Build

```bash
docker build -t lkt-mantra .
```

### Run

```bash
# Agent mode (default)
docker run --env-file .env.local lkt-mantra agent

# UI Server mode
docker run --env-file .env.local -p 8081:8081 lkt-mantra ui
```

### Required on PostgreSQL Server

The container connects to an external PostgreSQL. Ensure the server has:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Then run the migration:
```bash
docker run --env-file .env.local lkt-mantra python mantra/migrations/001_kb_pages.py
```

---

## 📁 Project Structure

```
lkt/
├── mantra/
│   ├── agent.py              # Voice agent (STT→LLM→TTS + KB tool)
│   ├── ui_server.py          # FastAPI dashboard + KB endpoints
│   ├── knowledge_base.py     # KB core: chunking, embeddings, vector search
│   ├── utils.py              # Recording, S3, DB logging, analysis
│   ├── dispatcher.py         # Redis queue → LiveKit dispatch
│   ├── email_alerts.py       # SMTP crash notifications
│   └── migrations/
│       └── 001_kb_pages.py   # KB schema + pgvector setup
├── static/
│   ├── dashboard.html        # Dashboard UI (with KB upload tabs)
│   ├── dashboard.js          # Dashboard logic
│   ├── index.html            # Test console
│   └── login.html            # Auth page
├── mcp/
│   └── server.py             # MCP Postgres server
├── pyproject.toml            # Python deps (uv)
├── uv.lock
├── Dockerfile
├── entrypoint.sh
├── dev.sh
└── README.md
```

---

## 📜 License

Proprietary — Mantracare-Org internal use.