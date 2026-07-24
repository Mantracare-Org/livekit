# Changelog

## 2026-07-16

- **refactor:** Removed colorama dependency and `ColorFormatter` — switched to plain `logging.Formatter` for cross-platform logging
- **refactor:** Migrated all timeline timestamps from UTC (`Z`) to IST (`+05:30`) — `normalize_to_iso8601` now returns `+05:30` format
- **feat:** Added `previous_stage_id` to call state logging payload for better stage transition tracking
- **fix:** Initialized stage tracking variables (`not_answering_id`, etc.) to `None` — filtered `target_stage_ids` prevents erroneous next-call scheduling
- **fix:** Added connection timeout to database client and improved error reporting in dashboard metrics
- **ops:** Updated agent ID in `livekit.toml` configuration
- **chore:** Removed test files

## 2026-06-30

- **doc:** Created `obsidian/` — comprehensive Obsidian knowledge base (48 files)
  - `Architecture/` — 8 files (overview, components, data flow, APIs, DB, infra, decisions, deps)
  - `Features/` — 10 files (index + 9 feature pages covering all modules)
  - `Development/` — 7 files (sprint, TODO, backlog, bugs, changelog, releases, roadmap)
  - `Agents/` — 6 files (master, backend, frontend, devops, docs, QA agent guides)
  - `Knowledge/` — 7 files (standards, conventions, best practices, commands, debugging, env, glossary)
  - `Context/` — 5 files (project summary, stack, repo map, external services)
  - `Templates/` — 3 file templates
  - `Inbox/` — placeholder
- **doc:** Updated root `README.md` to reference the Obsidian vault

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
