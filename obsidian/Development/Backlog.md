# Backlog

## Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| `agent.py` is 3,007 lines, monolithic | High | Extract tools, prompts, config |
| No automated tests | High | Regression risk on refactors |
| Dispatcher uses 0.5s polling | Medium | Replace with Redis Pub/Sub |
| Post-call webhook reliability | Medium | No retry after 3 attempts |
| MCP SSE transport fix shipped 2026-09-01 | Done | Was stdio-only; `check_doctor_availability` re-enabled |
| S3 bucket not configured | High | Recordings silently dropped |
| Handoff TTS glitch | High | Race condition with tool return |

## Feature Requests

| Feature | Priority | Notes |
|---------|----------|-------|
| Re-enable call transfer to human | Medium | Code exists but commented out; needs TTS glitch fix |
| KB vector/embedding search | High | Add pgvector, generate embeddings, hybrid search |
| KB upfront prompt injection | High | For small KBs, inject content into system prompt |
| WebSocket transcript streaming | Low | Would reduce SSE reliance |
| Multiple admin users | Low | Currently single user |
| Call recording download from dashboard | Low | S3 URL already stored |
