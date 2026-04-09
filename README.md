# LKT Workspace

[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-blue)](https://livekit.io/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-black?logo=github)](https://github.com/FardeenSK004/livekit)

Welcome to the [LKT-LiveKit](https://github.com/FardeenSK004/livekit) workspace, a collection of advanced voice AI agents and real-time communication tools.

---

## 🤖 Projects

### [massist](./massist)

**The Intelligent Voice Assistant**

A state-of-the-art voice assistant built on the LiveKit Agents framework. It features a fully reactive voice pipeline with sub-second latency and high-fidelity audio processing.

#### 🛠 Technology Stack

- **STT:** [Deepgram Nova-3](https://developers.deepgram.com/) (Multilingual)
- **LLM:** [OpenAI GPT-5.2 Chat](https://openai.com/)
- **TTS:** [Cartesia Sonic-3](https://cartesia.ai/)
- **Audio:** `ai-coustics` enhancement & intelligent noise cancellation
- **VAD:** Silero & Multilingual turn detection

#### 🚀 Quick Start

```bash
# Navigate to the agent
cd massist

# Install with UV (recommended)
uv sync

# Initialize models
uv run python src/agent.py download-files

# Start the interactive console
uv run python src/agent.py console
```
