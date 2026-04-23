# LKT Workspace

[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-blue)](https://livekit.io/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-black?logo=github)](https://github.com/FardeenSK004/livekit)

Welcome to the [LKT-LiveKit](https://github.com/FardeenSK004/livekit) workspace, a collection of advanced voice AI agents and real-time communication tools optimized for high-performance telephony.

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
`http://<your-ip>:5000/webhook/<event_name>`

### Local Development
To run the full stack locally:

1. **Start the Integrated Dev Environment:**
   ```bash
   ./dev.sh
   ```
   *This automatically starts both the Agent Worker and the UI Server.*

2. **Access the Interface:**
   Visit `http://localhost:5000` to monitor logs and interact manually.

---

### ✨ Key Features
- **Native Bilingual Intelligence:** Flawlessly switches between English and Hindi based on the caller's preference.
- **Telephony-First VAD:** Tuned thresholds to filter background noise and cellular interference.
- **Romanized Stability:** Optimized for high-quality Cartesia synthesis using transliterated Hinglish.
- **Modern UI:** Premium glassmorphism dashboard with real-time transcript synchronization.
- **Dynamic Context:** Automatically ingests JSON metadata from SIP triggers to provide personalized care.

