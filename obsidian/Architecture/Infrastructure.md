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

## Observability

### Prometheus Metrics (`mantra/telemetry.py`)

Available at `GET /metrics` on the UI server (port 9090).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mantra_app_info` | Info | name, version | Application metadata |
| `mantra_http_requests_total` | Counter | method, path, status | Total HTTP requests |
| `mantra_http_request_duration_seconds` | Histogram | method, path | HTTP request latency |
| `mantra_http_requests_in_flight` | Gauge | method | Concurrent HTTP requests |
| `mantra_calls_total` | Counter | status, model | Total calls processed |
| `mantra_calls_in_progress` | Gauge | — | Current active calls |
| `mantra_call_duration_seconds` | Histogram | status, model | Call duration |
| `mantra_queue_depth` | Gauge | — | Pending calls in Redis queue |
| `mantra_dispatch_attempts_total` | Counter | status | Dispatch attempts |
| `mantra_dispatches_in_flight` | Gauge | — | Current dispatches |
| `mantra_pipeline_duration_seconds` | Histogram | stage | Pipeline stage latency |
| `mantra_pipeline_errors_total` | Counter | stage | Pipeline errors |
| `mantra_sip_calls_total` | Counter | provider, status | SIP call attempts |
| `mantra_crash_total` | Counter | service | Crash/exception count |

### Structured Logging (`mantra/log_config.py`)

All modules emit JSON logs by default (`LOG_FORMAT=json`). Set `LOG_FORMAT=text` for plain text output. Log level controlled via `LOG_LEVEL` (default `INFO`).

Fields: `timestamp`, `level`, `name`, `message`, plus extra context per event.

### Grafana Setup

1. Add `http://<host>:8081/metrics` as a Prometheus data source
2. Add Loki as a log data source (scrape stdout JSON logs via Promtail)
3. Import or create dashboards using the metrics above
