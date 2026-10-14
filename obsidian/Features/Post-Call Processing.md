# Post-Call Processing

**Files:** `mantra/agent.py`, `mantra/core/finalize.py`, `mantra/utils.py`

## Pipeline (in `finalize.py` `finalize()`)

1. **Cancel background tasks** — Limiter, inactivity monitor, safety net, transcript logger
2. **Capture history snapshot** — Copy chat messages before session cleanup
3. **Determine connected call status** — Connected calls delegate status decision (`call_status = None`) to JEV AI Pro in Mantra Assist API. Failed/unanswered calls retain `"No Answer"`, `"Busy"`, or `"Failed"`.
4. **Stop recording & upload to S3** — Mix tracks → trim silence → MP3 → S3
5. **Build transcript** — JSON array of `{bot/user: message}`
6. **Generate structured summary** — Calls `SessionRecorder.generate_summary(post_call_llm, list(history_snapshot))` to produce a 4-part domain-agnostic summary (Reason for Call/Intent, Key Discussion Details & Requirements, Action & Booking Metadata, Outcome & Resolution). Heavy post-call LLM decision-making (`analyze_call()`) is bypassed, moving 100% of stage transition, status classification, and appointment metadata extraction to JEV AI Pro.
7. **Build clean webhook payload** — Direction-aware: `CALL_DATA_INBOUND_UPDATE` (inbound), `CALL_DATA_UPDATE` (outbound connected), or **`CALL_RETRY`** (outbound failed/unanswered). All null, non-existent, and empty fields (`new_stage_id`, `user_intent`, `call_intent`, `next_call_on`, `appointment_metadata`, empty custom field objects) are stripped from connected call payloads to keep payload weight minimal.
8. **Save to local DB** — `save_call_log_to_db()` upsert
9. **Queue to UI Server via Redis** — Delivered to MantraAssist backend (`/v1/webhooks/n8n/summary`), which enqueues `{ call_id }` into BullMQ `CallSummaryQueue` for JEV evaluation and database updates (`call_Logs`, `clientProcesses`, `appointments`).
10. **TOS telemetry** — Post-call summary with call_status, duration, S3 status, transcript flag

## SessionRecorder (`utils.py`)

In-memory audio recording system and structured summary generator:
- `start_recording(track, label)` — Async consumer per audio track
- `stop_recording()` — Cancel all consumers
- `get_combined_mp3_bytes()` — Mix tracks via numpy, trim silence via pydub, export 128k MP3
- `generate_summary(llm_engine, history)` — Fast LLM-driven summary generator that outputs a domain-agnostic 4-part narrative summary covering Intent, Key Discussion Details, Action & Booking Metadata, and Outcome & Resolution.

## Webhook Delivery & Payloads

- HMAC-SHA256 signed (`x-signature` header) with 3 retries and replay protection (`x-timestamp`)
- **`CALL_DATA_INBOUND_UPDATE`**: `org_id` (int), `call_recording`, `client_name`, `client_phone_number`, `call_duration`, `call_transcript`, `ai_summary`, `called_on`, `meta_data`
- **`CALL_DATA_UPDATE`**: `client_id`, `call_id`, `call_transcript`, `recording_url`, `call_duration_seconds`, `called_on`, `ai_call_id`, `process_id`, `stage_id`, `ai_summary`
- **`CALL_RETRY`**: `call_id`, `called_on`, `call_status`, `ai_call_id`
- **Lightweight Payload guarantee**: All null/empty decision fields are omitted from connected call payloads so decision processing is strictly handled downstream by JEV AI Pro.
