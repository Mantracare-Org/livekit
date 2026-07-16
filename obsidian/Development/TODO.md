# TODO

## High Priority

- [ ] Extract LLM prompts from `agent.py` into separate config/prompts module
- [ ] Add automated tests (pytest for utils, integration test for call flow)
- [ ] Add input validation to all webhook endpoints
- [ ] Implement Redis Pub/Sub for dispatcher (replace 0.5s polling)

## Medium Priority

- [ ] Add graceful shutdown handler for agent.py
- [ ] Rate limit dispatch-test endpoint
- [ ] Add request ID tracing across webhook → dispatch → agent
- [ ] Improve Plivo proxy error handling (retry with backoff)
- [ ] Add call transfer to human feature (currently commented out)

## Low Priority

- [ ] Migrate frontend to a framework (React/Vue) for maintainability
- [ ] Add WebSocket logging stream for real-time agent transcript
- [ ] Create admin user management (currently single hashed user)
- [ ] Add Prometheus metrics endpoint
- [ ] Replace hardcoded `FAREWELL_PHRASES` with configurable list

## Completed

- [x] Remove colorama dependency (plain logging.Formatter) — 2026-07
- [x] Migrate all timestamps to IST (+05:30) — 2026-07
- [x] Add `previous_stage_id` to call state logging — 2026-07
- [x] Null-safe stage tracking (None init + filter) — 2026-07
- [x] Add DB connection timeout + dashboard error handling — 2026-07
