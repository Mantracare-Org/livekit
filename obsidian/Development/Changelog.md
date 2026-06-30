# Changelog

## 2026-06-30

- **feat:** Voicemail handling — agent detects answering machine, waits for beep, leaves message, then auto-disconnects via `end_call`
- **feat:** Tone/style system — 6 tones and 3 styles configurable via payload metadata
- **feat:** DB write path changed — agent now sends call log via HTTP to UI Server (`POST /api/v1/webhooks/call-logs`) instead of direct PostgreSQL write (bypasses cloud security groups)
- **feat:** SIP failure immediate DB logging — failed calls saved to dashboard immediately on SIP error
- **fix:** DB query schema updated — `call_duration_seconds` now nested under `call_log -> 'data'` to match new payload structure
- **fix:** Removed stale `goodbye_task` from agent cleanup loop
- **doc:** Created Post-merge hook (`scripts/vault-stale-check.sh`) — prints stale vault docs after merge
- **doc:** Created `obsidian/` — comprehensive Obsidian knowledge base (48 files)

## 2026-06-15

- **refactor:** Renamed `CARTESIA_MAX_CONCURRENCY` to `MAX_CONCURRENCY` + env var fallback
- **refactor:** Migrated Cartesia TTS to LiveKit Inference, removed redundant API key management
- **feat:** Webhook-based call log storage, updated DB query schemas
- **feat:** Dynamic tone and style configurations for agent prompts

## 2026-05

- **feat:** Emotional tone optimization for Cartesia voice synthesis
- **feat:** `end_call` tool with graceful disconnect + safety net
- **feat:** Automated crash email notifications
- **feat:** Gemini LLM integration
- **feat:** DeepSeek LLM integration
- **feat:** Plivo India proxy routing
- **feat:** Redis queue-based dispatcher system
- **feat:** Dashboard with SSE real-time metrics
- **feat:** JWT authentication
- **refactor:** Bilingual STT (Deepgram Hindi model)
- **refactor:** Custom ColorFormatter for multi-process logging
