# TODO

## High Priority

- [ ] Extract LLM prompts from `agent.py` into separate config/prompts module
- [ ] Add integration test for actual call flow (end-to-end with mocked LiveKit)
- [ ] Add input validation to all webhook endpoints
- [ ] Implement Redis Pub/Sub for dispatcher (replace 0.5s polling)
- [ ] **KB: Implement vector/embedding search** — Add `pgvector` extension, OpenAI embedding generation, hybrid search fallback
- [ ] **KB: Add upfront prompt injection mode** — For small KBs, inject content into system prompt for zero-latency access

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
