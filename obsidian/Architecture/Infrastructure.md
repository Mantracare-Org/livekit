# Infrastructure

## LiveKit Cloud

- **Project:** `mantraassist-0ek43ife`
- **Agent ID:** `CA_duZ3ZGAvJvRr`
- **Config:** `livekit.toml`
- Production scaling: 1 instance, 1 replica
- SIP trunks configured for Twilio, Plivo, Zadarma

## Deployment

### Docker

Multi-stage build (`Dockerfile`):
1. `base` — uv + Python 3.12 slim
2. `build` — Compile dependencies
3. `production` — Runtime with appuser (UID 10001)

Models pre-cached during build (Silero VAD, HuggingFace, Torch).

### Entrypoint (`entrypoint.sh`)

Two modes:
- `agent` — `uv run python -m mantra.agent start`
- `ui` — `uv run python -m mantra.ui_server`

### Local Development (`dev.sh`)

Launches both agent + UI server, prints API endpoints.

## Infrastructure Dependencies

| Service | Purpose | Connection |
|---------|---------|------------|
| LiveKit Cloud | WebRTC + SIP trunking | API key/secret |
| PostgreSQL | Call log persistence | `lkdb` docker-compose (5433 local) |
| Redis | Queue + state + capacity | Local (6379) |
| AWS S3 | Recording storage | Bucket + credentials |
| SMTP (Gmail) | Crash email alerts | Gmail app password |
| Deepgram API | Speech-to-text | API key |
| OpenAI API | LLM (GPT-4o-mini) | API key |
| Google AI API | LLM (Gemini) | API key |
| DeepSeek API | LLM (DeepSeek) | API key |
| Cartesia API | Text-to-speech | API key(s) |
| MantraAssist Backend | CRM webhook target | HTTP + HMAC |
