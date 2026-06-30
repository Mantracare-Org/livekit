# Release Notes

## v0.1.0 (Current)

**Initial release** — Production-ready voice AI agent for outbound telephony.

### Core
- Real-time STT→LLM→TTS pipeline via LiveKit
- Bilingual English/Hindi support
- 3 LLM backends (OpenAI, Gemini, DeepSeek)
- 8 Cartesia voices with speed control
- Silero VAD + multilingual turn detection

### Telephony
- Twilio, Plivo, Zadarma SIP trunk support
- Plivo India proxy routing
- Webhook-driven outbound calls
- Redis queue with priority dispatch

### Operations
- FastAPI-based management server
- JWT authentication
- OpsCraft dashboard (dark theme)
- Real-time SSE metrics
- PostgreSQL call logging
- S3 recording storage
- HMAC-signed backend webhooks
- SMTP crash alerts with memes

### Known Limitations
- No automated test suite
- 3-minute maximum call duration
- Single admin user (hashed credentials)
- No human call transfer (code present but disabled)
