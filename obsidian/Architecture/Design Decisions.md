# Design Decisions

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | **Redis for queue + state** | Lightweight, no external dependency for coordination; sorted sets enable priority queuing | 2025 |
| 2 | **Three LiveKit API clients** | Plivo India + VoiceLink need proxy routing; Twilio/Zadarma use direct connection | 2025 |
| 3 | **In-memory audio recording** | Avoids disk I/O in container; mixed via numpy → pydub → MP3 in-memory | 2025 |
| 4 | **HMAC-signed webhooks** | Ensures authenticity of post-call data to MantraAssist backend | 2025 |
| 5 | **LiveKit native sonic-3 TTS** | Removed Cartesia dependency entirely — TTS runs natively via LiveKit Inference, zero external API keys needed | 2026-07 |
| 6 | **Multilingual STT + language matching** | Deepgram Nova-3 dynamic locale (`en-IN`/`en-US`/`hi`/`multi`) + per-call keyterm memory + Hinglish prompt directives so agent mirrors caller language each turn | 2026-09 |
| 7 | **Per-trunk capacity gating** | Plivo=2, Zadarma=3, VoiceLink=5, Twilio=3 with global cap=5; trunk embedded in room name (`call_{trunk_id}_{call_id}`); explicit provider required on inbound setup with rollback + cascading delete | 2026-08/09 |
| 8 | **Call limiter + outbound extension** | Base 150s farewell / 180s hard kill; outbound positive-intent extends to 270s / 300s (`call_duration.py` + `positive_intent.py`) | 2026-09 |
| 9 | **Farewell safety net** | LLMs sometimes say goodbye without calling `end_call`; async monitor catches this | 2025 |
| 10 | **SIP error status in Redis** | UI server detects SIP failures and writes status; agent reads it for accurate call outcome | 2025 |
| 11 | **Automatic crash emails with memes** | Admin recipients get humorous memes with crash alerts (low-priority but morale-boosting) | 2025 |
| 12 | **OpenTelemetry suppressed** | Prevents 429 errors from OTEL collectors; metrics export disabled in env | 2025 |
| 13 | **Proxy env vars stripped** | boto3 S3 uploads fail with proxy vars; temporarily removed during upload | 2025 |
| 14 | **LLM model selection from metadata** | Payload-driven model/voice/speed selection without redeployment | 2026-06 |
| 15 | **Multi-KB collections per org** | Each document = one `kb_collection`; agent searches all collections for the org + legacy fallback | 2026-07 |
| 16 | **Direct dispatch from webhook** | Webhook handler now dispatches agent + SIP call directly (dispatcher.py kept for legacy queue path) | 2026-07 |
| 17 | **TOS telemetry pipeline** | Fire-and-forget telemetry posts to TOS endpoint across all three services (agent, dispatcher, UI server) | 2026-07 |
