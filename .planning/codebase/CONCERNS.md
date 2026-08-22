# CONCERNS.md

## Technical Debt & Fragile Areas
- **Monolithic Agent File**: `mantra/agent.py` is quite large (approx 38KB), suggesting that a lot of custom logic, LLM prompts, and tool callbacks might be tangled together. Splitting out tool callbacks and prompts into a separate `skills/` or `prompts/` module would improve maintainability.
- **Zombie Calls**: The dispatcher includes a `cleanup_zombies` function that periodically polls the LiveKit API to resolve discrepancies between Redis state and LiveKit room state. This indicates that sudden disconnects or agent crashes might leave artifacts behind.
- **Post-Call Webhook Reliability**: The application relies on external webhooks (e.g., n8n) being reachable at the end of the call to log transcripts/summaries. If the external webhook fails, the data may only reside in application logs unless explicitly retried.
- **Lack of Automated Testing**: As noted in TESTING.md, the absence of an automated test suite increases regression risk for any future refactors.

## Performance
- The polling loop in `dispatcher.py` runs with a hardcoded `asyncio.sleep(0.5)`. While sufficient for low-to-medium scale, a Redis Pub/Sub or Streams implementation might offer more immediate dispatch without busy polling.

## Security
- API keys, database URLs, and LiveKit secrets are stored in `.env.local` and loaded directly. Ensure these are never committed or logged in production `traceback` blocks.
