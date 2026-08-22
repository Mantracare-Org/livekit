# INTEGRATIONS.md

## External APIs
- **LiveKit Server**: Real-time WebRTC communications. The system connects via WebSocket and HTTP API to manage rooms, dispatch agents, and trigger SIP calls.
- **Cartesia**: Text-to-Speech (TTS) provider used via LiveKit plugins.
- **OpenAI**: Large Language Model provider for the agent's brain.
- **Deepgram / AssemblyAI**: Speech-to-Text (STT) providers.
- **Telnyx / Plivo**: SIP trunking providers for inbound and outbound PSTN calls (referenced in conversation history and webhook handling).

## Databases & Caching
- **Redis**: Critical state management and queueing layer. Maintains a priority queue (`queue:pending`), active call hashes (`calls:active`), and status keys (`calls:status:{id}`).
- **PostgreSQL**: Relational database integrated via the Model Context Protocol (MCP) server located in `mcp/server.py`.

## Cloud Storage
- **AWS S3**: Used via `boto3` for storing call recordings after the agent session ends.

## Webhooks
- **n8n / External CRMs**: The agent delivers post-call artifacts (transcripts, summaries, recording URLs) to an external webhook pipeline like n8n.
