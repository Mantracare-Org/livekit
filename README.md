# LKT Workspace

[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-blue)](https://livekit.io/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
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

---

## 🚀 Deployment

This project is designed to be split across two services for production:

### 1. Agent Worker (LiveKit Cloud)
The agent worker handles the actual voice conversation logic. It is deployed as a managed agent on LiveKit Cloud.

**Deployment Steps:**
1. Install LiveKit CLI: `curl -sSL https://get.livekit.io/cli | bash`
2. Authenticate: `lk cloud auth`
3. Deploy: `lk cloud deploy`

**Required Environment Variables:**
- `OPENAI_API_KEY`
- `DEEPGRAM_API_KEY`
- `CARTESIA_API_KEY`

### 2. Trigger Service (Railway)
The trigger service provides a public HTTP endpoint to initiate outbound calls via SIP.

**Deployment Steps:**
1. Connect this repository to a new Railway project.
2. Railway will automatically detect the `trigger/Dockerfile`.
3. Set the **Start Command** to: `uv run uvicorn trigger.main:app --host 0.0.0.0 --port 8080` (if not using the Dockerfile).
4. Set the following environment variables in Railway.

**Required Environment Variables:**
- `LIVEKIT_URL`: Your LiveKit Cloud URL (e.g., `wss://project.livekit.cloud`)
- `LIVEKIT_API_KEY`: LiveKit API Key
- `LIVEKIT_API_SECRET`: LiveKit API Secret
- `LIVEKIT_SIP_TRUNK_ID`: The ID of your SIP trunk for outbound calls

**API Endpoint:**
- `POST /trigger-call`
- Body: `{ "phone_number": "+1234567890", "lead_id": "123", "client_name": "John", "prompt": "Your custom prompt" }`

---

### 💻 Local Development

---

### ✨ Key Features

- **Native Bilingual Intelligence:** Flawlessly switches between English and Hindi based on the caller's preference.
- **Telephony-First VAD:** Tuned thresholds to filter background noise and cellular interference.
- **Romanized Stability:** Optimized for high-quality Cartesia synthesis using transliterated Hinglish.
- **Modern UI:** Premium glassmorphism dashboard with real-time transcript synchronization.
- **Dynamic Context:** Automatically ingests JSON metadata from SIP triggers to provide personalized care.
