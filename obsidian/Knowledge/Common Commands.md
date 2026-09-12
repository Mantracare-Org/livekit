# Common Commands

## Development

```bash
# Install dependencies
uv sync

# Launch dev environment (agent + UI together)
./dev.sh

# Run agent only
uv run python -m mantra.agent start

# Run UI server only
uv run python -m mantra.ui_server

# Run dispatcher
uv run python -m mantra.dispatcher

# Run MCP server
uv run python mcp/server.py
```

## Docker

```bash
# Build
docker build -t mantra-agent .

# Run agent
docker run --env-file .env.local mantra-agent agent

# Run UI
docker run --env-file .env.local -p 8081:8081 mantra-agent ui

# Run MCP
docker run --env-file .env.local mantra-agent mcp
```

## Testing (Manual)

```bash
# Trigger test outbound call
curl -X POST http://localhost:8081/dispatch-test \
  -H "Content-Type: application/json" \
  -d '{"client_name":"Test","prompt":"Hello"}'

# Trigger webhook call (outbound)
curl -X POST http://localhost:8081/v1/webhooks/telephony \
  -H "Content-Type: application/json" \
  -d '{"client_phone":"+919876543210","client_country_code":"91","prompt":"Hi","trunk_id":"ST_xxx"}'

# Trigger simulated inbound call
curl -X POST http://localhost:8081/v1/test/inbound-call \
  -H "Content-Type: application/json" \
  -d '{"phone":"+919876543210","trunk_id":"ST_xxx","direction":"inbound"}'

# Check health
curl http://localhost:8081/health
```
