# Common Commands

## Development

```bash
# Install dependencies
uv sync

# Launch dev environment
./dev.sh

# Run agent only
uv run python -m mantra.agent dev

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
```

## Testing (Manual)

```bash
# Trigger test call
curl -X POST http://localhost:8081/dispatch-test \
  -H "Content-Type: application/json" \
  -d '{"client_name":"Test","prompt":"Hello"}'

# Trigger webhook call
curl -X POST http://localhost:8081/api/v1/webhooks/telephony \
  -H "Content-Type: application/json" \
  -d '{"client_phone":"+919876543210","client_country_code":"91","prompt":"Hi"}'

# Check health
curl http://localhost:8081/health
```
