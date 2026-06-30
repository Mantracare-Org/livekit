# Post-Call Processing

**Files:** `mantra/agent.py` (lines 711-951) + `mantra/utils.py`

## Pipeline (in `agent.py` `finalize()`)

1. **Capture history snapshot** — Copy chat messages before session cleanup
2. **Determine call status** — Priority: Redis SIP error > user_joined + wait time > user_spoke
3. **Stop recording & upload to S3** — Mix tracks → trim silence → MP3 → S3
4. **Build transcript** — JSON array of `{user/bot: message}`
5. **LLM analysis** — `analyze_call()` generates summary, stage transition, sentiment, appointment data
6. **Build webhook payload** — Full call data envelope
7. **Send to backend** — HMAC-signed POST to MantraAssist `/webhooks/n8n`
8. **Save to PostgreSQL** — `save_call_log_to_db()` upsert
9. **Free Redis capacity** — Remove from `calls:active`, set status to `completed`

## SessionRecorder (`utils.py`)

In-memory audio recording system:
- `start_recording(track, label)` — Async consumer per audio track
- `stop_recording()` — Cancel all consumers
- `get_combined_mp3_bytes()` — Mix tracks via numpy, trim silence via pydub, export MP3

## Analyze Call (`utils.py:analyze_call()`)

LLM-driven call analysis:
- Generates summary paragraph
- Determines CRM stage transition from stageDetails
- Extracts: appointment_date_time, doctor, hospital_location, sentiment_score
- Falls back to heuristics on LLM failure

## Webhook Delivery

- HMAC-SHA256 signed (`x-signature` header)
- 3 retries with exponential backoff (2^N seconds)
- Timestamp-based replay protection (`x-timestamp`)
