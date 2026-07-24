# Backlog

## Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| `agent.py` is 1,032 lines, monolithic | High | Extract tools, prompts, config |
| No automated tests | High | Regression risk on refactors |
| Dispatcher uses 0.5s polling | Medium | Replace with Redis Pub/Sub |
| Post-call webhook reliability | Medium | No retry after 3 attempts |
| Duplicate `agent_dispatch` creation in dispatcher + webhook | Medium | Two paths do similar work |

## Feature Requests

| Feature | Priority | Notes |
|---------|----------|-------|
| Call transfer to human | Medium | Code exists but commented out in agent.py |
| WebSocket transcript streaming | Low | Would reduce SSE reliance |
| Multiple admin users | Low | Currently single user |
| Call recording download from dashboard | Low | S3 URL already stored |
