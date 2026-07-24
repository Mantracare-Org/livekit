# Design Decisions

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | **Redis for queue + state** | Lightweight, no external dependency for coordination; sorted sets enable priority queuing | 2025 |
| 2 | **Two LiveKit API clients** | Plivo India routing requires proxy; Twilio/Zadarma use direct connection | 2025 |
| 3 | **In-memory audio recording** | Avoids disk I/O in container; mixed via numpy → pydub → MP3 in-memory | 2025 |
| 4 | **HMAC-signed webhooks** | Ensures authenticity of post-call data to MantraAssist backend | 2025 |
| 5 | **Fallback TTS (multi-key)** | Cartesia rate limits (429) handled by key cycling; production robustness | 2025 |
| 6 | **Hindi STT for Hinglish** | Deepgram Nova-3 Hindi model better catches Indian English + Hinglish code-switching | 2025 |
| 7 | **Standard logging format** | `ColorFormatter` removed — switched to plain `logging.Formatter` for simpler cross-platform compatibility | 2026-07 |
| 8 | **3-minute call limiter** | Prevents runaway costs; soft farewell at 2m30s, hard kill at 3m | 2025 |
| 9 | **Farewell safety net** | LLMs sometimes say goodbye without calling `end_call`; async monitor catches this | 2025 |
| 10 | **SIP error status in Redis** | UI server detects SIP failures and writes status; agent reads it for accurate call outcome | 2025 |
| 11 | **Automatic crash emails with memes** | Admin recipients get humorous memes with crash alerts (low-priority but morale-boosting) | 2025 |
| 12 | **OpenTelemetry suppressed** | Prevents 429 errors from OTEL collectors; metrics export disabled in env | 2025 |
| 13 | **Proxy env vars stripped** | boto3 S3 uploads fail with proxy vars; temporarily removed during upload | 2025 |
| 14 | **LLM model selection from metadata** | Payload-driven model/voice/speed selection without redeployment | 2026-06 |
| 15 | **IST timezone for all timestamps** | All timeline events and `normalize_to_iso8601` use `+05:30` instead of UTC `Z` for alignment with India operations | 2026-07 |
| 16 | **Null-safe stage tracking** | Stage IDs initialized to `None` and filtered; prevents erroneous next-call scheduling when stage details are missing | 2026-07 |
| 17 | **`previous_stage_id` in call state** | Tracks stage transitions across the call lifecycle for better analytics | 2026-07 |
