# Bugs

## Open

| ID | Description | Module | Severity | Status |
|----|-------------|--------|----------|--------|
| — | **Handoff TTS glitch** — `"..."` residual utterance after `transfer_to_human` causes traceback | `agent.py` | High | Open (tool disabled as workaround) |
| — | **Post-call webhook 404** — n8n endpoint missing on ngrok backend | `utils.py` | High | Open |
| — | **S3 not configured** — `AWS_S3_BUCKET_NAME` not set, recordings silently dropped | Infra | High | Open |
| — | **No KB data for org 66** — `kb_pages` has zero rows for this org | KB | Blocker | Open |

## Resolved

| ID | Description | Module | Severity | Resolution |
|----|-------------|--------|----------|------------|
| — | **MCP SSE transport** — was stdio-only, unreachable over HTTP | `mcp/` | Blocker | Fixed 2026-09-01 (`transport="sse"`, port 8000) |
| — | **KB retrieval missed data (org 77)** — `diagnostic codes` returned nothing while page contained `diagnostic code` | KB | High | Fixed 2026-08-09 (english config + tiered fallback + vector; migration 006) |
| — | **Premature disconnects & reconnect loops** — farewell phrase scanning and reconnect aborts | `mantra/core/` | High | Fixed 2026-10-11 (`StopResponse()` + definitive `graceful_disconnect_after_speech()`) |
| — | **First response cold-start latency (2–3s)** — engine initialization delays | `mantra/` | High | Fixed 2026-10-19 (TTS/STT/LLM parallel pre-warm + greeting precomputation) |

## Known Issues

| Issue | Impact | Notes |
|-------|--------|-------|
| Zombie calls | Medium | Dispatcher has cleanup but race conditions possible |
| Webhook reliability | Medium | External n8n may be unreachable; delivery ledger outbox active |
| No comprehensive automated tests | High | Unit tests for multilingual/STT exist, but full telephony call flow test pending |
| OpenTelemetry suppression | Low | Hides metrics but prevents 429 errors |
| Dispatcher 0.5s polling | Low | Replace with Redis Pub/Sub |
