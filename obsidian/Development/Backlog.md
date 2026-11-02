# Backlog

## Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| Monolithic `agent.py` & `ui_server.py` | Done | Refactored on 2026-09-12 into modular `mantra/core/`, `routers/`, `services/`, and `prompts.py` |
| Monolithic prompt management | Done | Extracted into `mantra/prompts.py` with dynamic rules |
| Farewell phrase scanning | Done | Deprecated on 2026-10-11 in favor of `StopResponse()` and `graceful_disconnect_after_speech()` |
| MCP SSE transport fix | Done | Shipped 2026-09-01; `check_doctor_availability` re-enabled |
| No automated tests | High | Unit tests added for multilingual/STT, but full call flow integration test pending |
| Dispatcher uses 0.5s polling | Medium | Replace with Redis Pub/Sub |
| Post-call webhook reliability | Medium | Ledger & replay outbox added; ngrok 404 monitoring |
| S3 bucket not configured | High | Recordings silently dropped when `AWS_S3_BUCKET_NAME` unset |
| Handoff TTS glitch | High | Race condition with tool return |

## Feature Requests

| Feature | Priority | Notes |
|---------|----------|-------|
| Call log modal one-click copy buttons | Done | Shipped 2026-10-26 (Summary, multi-turn Transcript, JSON Payload) |
| Zero cold-start latency & precomputed greeting | Done | Shipped 2026-10-19 (TTS/STT/LLM prewarming + instant greeting) |
| Inbound noise cancellation | Done | Shipped 2026-10-19 (ai-coustics Quail & C++ Krisp NC) |
| KB vector/embedding search | Done | Shipped 2026-08-09/24 (pgvector + hybrid RRF + Fast-Race guard) |
| KB upfront prompt injection | Done | Shipped 2026-10-06 (injected during warmup for 0ms RAG) |
| Re-enable call transfer to human | Medium | Code exists but commented out; needs TTS glitch fix |
| WebSocket transcript streaming | Low | Would reduce SSE reliance |
| Multiple admin users | Low | Currently single user |
| Call recording download from dashboard | Low | S3 URL already stored |
