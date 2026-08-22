# ARCHITECTURE.md

## Architectural Pattern
The application follows a modular, asynchronous, multi-process architecture based on Python `asyncio`.
It is divided into distinct operational roles:
1. **Agent Worker** (`mantra/agent.py`): Connects to specific LiveKit rooms to handle real-time voice AI tasks (STT -> LLM -> TTS).
2. **Dispatcher** (`mantra/dispatcher.py`): A continuous loop worker that pops calls from a Redis queue, checks concurrency limits, and triggers LiveKit `agent_dispatch` and `sip_participant` creations.
3. **UI/API Server** (`mantra/ui_server.py`): A FastAPI application handling HTTP endpoints (webhooks from telephony providers, UI routes) and enqueuing tasks into Redis.
4. **MCP Server** (`mcp/server.py`): Exposes Postgres queries/tools using the Model Context Protocol.

## Data Flow (Outbound Call Example)
1. **Trigger**: An API request hits the `ui_server.py` webhook.
2. **Queue**: The server parses the request and adds a JSON payload to the Redis `queue:pending` sorted set.
3. **Dequeue**: The `dispatcher.py` checks capacity (Cartesia, LiveKit rooms, Agent workers limits) and pops from the Redis queue.
4. **Dispatch**: The dispatcher calls the LiveKit API to create an `agent_dispatch` (spawning a worker) and creates a `sip_participant` to dial the external number.
5. **Execution**: The agent (`agent.py`) handles the call logic until termination.
6. **Post-Call**: Call recordings are uploaded to S3 (`mantra/utils.py`) and summary webhooks are sent to external systems.

## Entry Points
- `mantra-agent`: Starts the `agent.py` worker via LiveKit CLI.
- `mantra-ui`: Starts the FastAPI server (`ui_server.py`).
- `python mantra/dispatcher.py`: Starts the background dispatcher.
