# LKT Workspace — Mantra Voice Agent & Telephony Engine

[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-blue)](https://livekit.io/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-black?logo=github)](https://github.com/Mantracare-Org/livekit)

Production-grade real-time conversational AI voice agent and telephony orchestrator for inbound and outbound healthcare coordination, appointment management, and patient care workflows.

---

## 🤖 Architecture & Tech Stack

```
   ┌─────────────────────────────────────────────────────────────┐
   │                       SIP / Telephony                       │
   │               (Twilio / Zadarma / Plivo / IVR)              │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                     LiveKit Cloud Room                      │
   │          (8kHz Telephony Audio & WebRTC Transport)          │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
      ┌───────────────────────────┼───────────────────────────┐
      │                           │                           │
      ▼                           ▼                           ▼
┌──────────────┐          ┌──────────────┐            ┌──────────────┐
│ Deepgram STT │          │ DeepSeek LLM │            │ Cartesia TTS │
│  (Nova-3 /   │ ───────► │ (Reasoning / │ ─────────► │  (Sonic-3 /  │
│  Dynamic     │          │ Single-Turn  │            │  Bilingual   │
│  Locale)     │          │ Tool Calling)│            │  Voices)     │
└──────────────┘          └───────┬──────┘            └──────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │    Hybrid Knowledge Base      │
                  │  PostgreSQL FTS + pgvector    │
                  │  + Google Gemini Embeddings   │
                  └───────────────────────────────┘
```

- **Speech-to-Text (STT):** [Deepgram Nova-3](https://www.deepgram.com/)
  - **Dynamic International Locale Routing:** Resolves caller phone number / country prefix (`+91` ➔ `en-IN`, `+1` ➔ `en-US`, `+44` ➔ `en-GB`, `+61` ➔ `en-AU`) with language override support (`hi`, `es`, `fr`).
  - **Neural Optimization:** `smart_format=True`, `punctuate=True`, `numerals=True` for Inverse Text Normalization (ITN), Named Entity Recognition (NER), and accurate proper noun capture.
  - **Direct Neural Acoustic Pipeline:** Telephony audio streams directly to Deepgram's native acoustic model for clean, un-distorted speech recognition.
- **Large Language Model (LLM):** [DeepSeek V3](https://deepseek.com/) / [OpenAI GPT-4o-mini](https://openai.com/) / Gemini fallback.
  - **Single-Turn Execution:** Enforces single-turn tool calling without sequential retry loops over the wire.
  - **Silent Background Lookups:** Prohibits conversational search filler narration (*"Let me check that for you"*) to deliver immediate factual answers.
- **Text-to-Speech (TTS):** [Cartesia Sonic-3](https://cartesia.ai/)
  - Low-latency streaming multilingual voice synthesis with 13 customized voices (Arushi, Vikas, Sia, Sneha, Kavita, Katie, Cathy, etc.).
- **VAD & Turn Detection:** Silero VAD + LiveKit Server-Side Inference Turn Detector.
- **Hybrid Knowledge Base (KB):**
  - **FTS + Semantic Fusion:** Combines PostgreSQL full-text search with Google Gemini (`gemini-embedding-2`) 1536-dimensional vector embeddings blended via Reciprocal Rank Fusion (RRF).
  - **Fast-Race Embedding Guard:** Concurrently executes FTS and vector embedding queries with automatic instant FTS fallback if the external embedding API lags.
  - **Zero-Latency In-Memory Caching:** Pre-fetches the session's KB pages on room connect into memory (`_org_pages_cache`), eliminating repeat DB queries.
- **Pipeline Observability & Automated Alerting:** Real-time error monitoring on `session.on("error")` to automatically dispatch diagnostic crash emails upon API credit exhaustion or provider outages.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16+ with `pgvector` extension
- Redis 7+

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Mantracare-Org/livekit.git
cd livekit

# Install dependencies with uv
uv sync
```

### 2. Environment Configuration

Create `.env.local` in the project root:

```env
# LiveKit Cloud
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# AI Providers
DEEPSEEK_API_KEY=your_deepseek_api_key
OPENAI_API_KEY=your_openai_api_key
DEEPGRAM_API_KEY=your_deepgram_api_key
CARTESIA_API_KEY=your_cartesia_api_key
GOOGLE_API_KEY=your_google_gemini_api_key

# PostgreSQL (Call Logs & Hybrid Knowledge Base)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_DB=call_logs_db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Alerts & Email (Optional)
ALERT_EMAIL_RECIPIENT=alerts@mantracare.org
SENDGRID_API_KEY=your_sendgrid_api_key
```

### 3. Local Development

Run the full local development stack (Voice Agent worker + UI dashboard):

```bash
./dev.sh
```

- **Telephony Webhook:** `http://<local-ip>:8081/v1/webhooks/telephony`
- **Dashboard & Test Console:** `http://localhost:8081/dashboard`
- **SIP Trunk Management:** `http://localhost:8081/v1/sip/trunks/outbound`

---

## 📚 Key Features & Capabilities

### 1. Bilingual Conversational Intelligence (English & Hindi)
- Automatically tracks conversation language and switches STT, TTS, and prompt directives dynamically between English and Hindi (`SUPPORTED_LANGUAGES = {"en", "hi"}`).

### 2. Hybrid Retrieval-Augmented Generation (RAG)
- **Multi-Tenant Isolation:** Documents partitioned cleanly by `kb_id` and `org_id`.
- **Flexible Document Ingestion:** Supports PDF, TXT, Markdown, raw text, and web scraping via dashboard.
- **Adaptive Chunking:** Auto-segments text by structure (headings, paragraphs, sliding window tokens).

### 3. Automated Error Monitoring & Resilience
- Traps pipeline errors during calls (LLM credit limits, STT disconnections, TTS timeouts) and dispatches contextual diagnostics to engineering without crashing worker threads.

---

## 📁 Project Structure

```
lkt/
├── mantra/
│   ├── agent.py              # Voice agent orchestrator (STT → LLM → TTS + KB tools)
│   ├── ui_server.py          # FastAPI dashboard, SIP endpoints & KB management
│   ├── knowledge_base.py     # Hybrid KB: PostgreSQL FTS, pgvector & RRF fusion
│   ├── gemini_embeddings.py  # Google Gemini embedding client & LRU query cache
│   ├── language_manager.py   # STT locale resolution & multilingual hysteresis tracker
│   ├── retriever.py          # Multi-tier KB retrieval & session memory pre-fetching
│   ├── email_alerts.py       # Pipeline error & credit exhaustion email notifications
│   ├── utils.py              # Call logging, S3 audio recording & post-call LLM analysis
│   └── migrations/           # Database schemas and pgvector setup
├── static/
│   ├── dashboard.html        # Glassmorphism call analytics & KB dashboard
│   ├── dashboard.js          # Dashboard frontend logic
│   └── index.html            # Test & manual dispatch console
├── obsidian/                 # Agentic Knowledge Base & architecture specifications
├── pyproject.toml            # Project dependencies & tool configurations
├── Dockerfile                # Production container specification
├── dev.sh                    # Development startup script
└── README.md
```

---

## 🐳 Docker Deployment

```bash
# Build the container image
docker build -t lkt-mantra .

# Run Voice Agent worker
docker run --env-file .env.local lkt-mantra agent

# Run UI / Telephony Server
docker run --env-file .env.local -p 8081:8081 lkt-mantra ui
```

---

## 📜 License

Proprietary — Mantracare-Org internal use.