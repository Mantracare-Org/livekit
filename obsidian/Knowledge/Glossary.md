# Glossary

| Term | Definition |
|------|------------|
| **Agent** | LiveKit voice agent (`mantra/agent.py`) that processes real-time voice conversations |
| **Cartesia** | TTS provider (Sonic-3 model) for voice synthesis |
| **Deepgram** | STT provider (Nova-3) for speech recognition |
| **Dispatcher** | Background process that dequeues calls from Redis and dispatches to LiveKit |
| **HMAC** | Hash-based Message Authentication Code used for webhook signing |
| **LiveKit** | WebRTC infrastructure platform for real-time audio/video |
| **MCP** | Model Context Protocol — exposes Postgres tools to AI agents |
| **OKF** | Open Knowledge Format — knowledge representation standard |
| **Plivo** | Telephony provider for India (requires proxy routing) |
| **Silero VAD** | Voice Activity Detection model (PyTorch) |
| **SIP Trunk** | SIP connection to telephony provider (Twilio, Plivo, Zadarma) |
| **SSE** | Server-Sent Events — used for real-time dashboard updates |
| **STT** | Speech-to-Text |
| **TTS** | Text-to-Speech |
| **Turn Detection** | Detects when a speaker has finished their turn |
| **Twilio** | Primary telephony provider (US) |
| **VAD** | Voice Activity Detection — detects when someone is speaking |
| **Zadarma** | Telephony provider (default fallback) |
