# Bugs

## Open

| ID | Description | Module | Severity | Status |
|----|-------------|--------|----------|--------|
| — | **MCP SSE transport** — was stdio-only, unreachable over HTTP | `mcp/` | Blocker | **Fixed 2026-09-01** (`transport="sse"`, port 8000) |
| — | **Handoff TTS glitch** — `"..."` residual utterance after `transfer_to_human` causes traceback | `agent.py` | High | Open (tool disabled as workaround) |
| — | **Post-call webhook 404** — n8n endpoint missing on ngrok backend | `utils.py` | High | Open |
| — | **S3 not configured** — `AWS_S3_BUCKET_NAME` not set, recordings silently dropped | Infra | High | Open |
| — | **No KB data for org 66** — `kb_pages` has zero rows for this org | KB | Blocker | Open |
| — | **KB retrieval missed data (org 77)** — `diagnostic codes` returned nothing while page contained `diagnostic code` (simple FTS config, no stemming) | KB | High | **Fixed 2026-08-09** (english config + tiered fallback + vector; migration 006, see Changelog) |

## Known Issues

| Issue | Impact | Notes |
|-------|--------|-------|
| Zombie calls | Medium | Dispatcher has cleanup but race conditions possible |
| Webhook reliability | Medium | External n8n may be unreachable |
| Monolithic agent.py | Medium | 3,007 lines, hard to maintain |
| No automated tests | High | Regression risk |
| OpenTelemetry suppression | Low | Hides metrics but prevents 429 errors |
| Dispatcher 0.5s polling | Low | Replace with Redis Pub/Sub |
