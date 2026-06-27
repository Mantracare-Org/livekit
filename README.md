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

---

## 🚀 Deployment & Usage

### Webhook-Driven Outbound Calls

The agent is integrated with a SIP-based outbound system. Trigger calls by sending a POST request to:
`http://<your-ip>:8081/webhook/<event_name>`

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

This project relies on an isolated database environment (`lkdb`) to store call logs and transcripts. 
- **PostgreSQL & Adminer:** Are managed independently in the `lkdb` directory via its own `docker-compose.yml`.
- **Redis:** Used for capacity management and connection state routing, running locally on port `6379`.
- **Logging Pipeline:** Call timelines, statuses, recording URLs, and detailed JSON payloads are automatically saved into the isolated `call_logs_db` after every call.

---

### ✨ Key Features

- **Native Bilingual Intelligence:** Flawlessly switches between English and Hindi based on the caller's preference.
- **Telephony-First VAD:** Tuned thresholds to filter background noise and cellular interference.
- **Romanized Stability:** Optimized for high-quality Cartesia synthesis using transliterated Hinglish.
- **Modern UI:** Premium glassmorphism dashboard with real-time transcript synchronization.
- **Dynamic Context:** Automatically ingests JSON metadata from SIP triggers to provide personalized care.
