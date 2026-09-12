# Post-Call Processing

**Files:** `mantra/agent.py`, `mantra/utils.py`

## Pipeline (in `agent.py` `finalize()`)

1. **Cancel background tasks** — Limiter, inactivity monitor, safety net, transcript logger
2. **Capture history snapshot** — Copy chat messages before session cleanup
3. **Determine call status** — Priority: user_joined + ring time > user_spoke; yields No Answer, Busy, Incomplete, Failed, or Completed. **Inbound calls are always treated as `user_joined = True`** so post-call analysis runs even when no user message is captured; the `user_spoke` "No Answer" branch applies to outbound calls only.
4. **Stop recording & upload to S3** — Mix tracks → trim silence → MP3 → S3
5. **Build transcript** — JSON array of `{bot/user: message}`
6. **LLM analysis** — `analyze_call()` generates summary, process_id, stage transition, sentiment, appointment data (with IST timezone conversion). Uses KB-tracked `process_stage_data` for process-aware analysis. Runs for `Completed` status (all inbound connected calls, outbound only when the user spoke); skipped for Busy/No Answer/Failed. Engine resolves as `target_llm = post_call_llm or llm_engine`, so the dedicated post-call model (DeepSeek Pro) is preferred, falling back to the live-call engine or `gpt-4o-mini` when no key is set. `ai_summary` is guaranteed non-empty via a transcript-snippet fallback (else `"Call completed."`).
7. **Build webhook payload** — Direction-aware: `CALL_DATA_INBOUND_UPDATE` (inbound) or `CALL_DATA_UPDATE` (outbound), with **`CALL_RETRY`** override when `call_status` is `No Answer`, `Busy`, `Incomplete`, or `Failed` (same payload, different event). Inbound numeric fields (`org_id`, `process_id`, `new_stage_id`) are coerced string→int via `_as_int()`; missing values stay `null`.
8. **Save to PostgreSQL** — `save_call_log_to_db()` upsert
9. **Send to backend** — HMAC-signed POST to MantraAssist `/v1/webhooks/n8n` with 3 retries
10. **TOS telemetry** — Post-call summary with call_status, duration, S3 status, transcript flag

## SessionRecorder (`utils.py`)

In-memory audio recording system:
- `start_recording(track, label)` — Async consumer per audio track
- `stop_recording()` — Cancel all consumers
- `get_combined_mp3_bytes()` — Mix tracks via numpy, trim silence via pydub, export 128k MP3

## Analyze Call (`utils.py:analyze_call()`)

LLM-driven call analysis with process-aware staging:
- Generates summary paragraph
- Determines process_id from KB-tracked process_stage_data
- Determines CRM stage transition
- Extracts: appointment_date_time (converted to IST), next_call_on, doctor, hospital_location, sentiment_score
- Runs on the dedicated post-call engine (`build_post_call_llm()`, default `deepseek-v4-pro`), falling back to `gpt-4o-mini` when `DEEPSEEK_API_KEY` is missing
- Guarantees a non-empty `ai_summary` (transcript-snippet fallback) so the webhook payload never ships an empty summary
- Auto-sets next_call_on = +24h for follow-up/callback stages when missing

## Webhook Delivery

- HMAC-SHA256 signed (`x-signature` header)
- 3 retries with exponential backoff (2^N seconds)
- Timestamp-based replay protection (`x-timestamp`)
- Inbound payloads carry: `org_id` (int), `call_recording`, `process_id` (int|null, from KB when searched), `new_stage_id` (int|null), `client_phone_number`, `next_call_on`, `called_on`
- Outbound payloads carry: `client_id`, `call_id`, `call_status`, `ai_summary`, `recording_url`, `call_duration_seconds`, `new_stage_id`, `client_custom_fields`, `next_call_on` (null when none)

## KB Document Tracking

`KnowledgeRetriever` tracks `accessed_pages_meta` from every KB search during the call. `AssistantFunctions.used_kb_process_ids` extracts unique `process_id` values from accessed pages. For inbound calls, the first unique process_id is injected into the webhook's `process_id` field.
