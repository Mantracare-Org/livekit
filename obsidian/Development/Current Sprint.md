# Current Sprint

> **Sprint:** N/A (no formal sprint process)  
> **Last Updated:** 2026-07-16  
> **Status:** Active maintenance + incremental features

## In Progress

- [ ] Migrate remaining `mantra/agent.py` tool callbacks to separate module
- [ ] Set up automated test suite (currently manual only)

## Recently Completed

- [x] IST timezone migration (UTC → +05:30) — 2026-07
- [x] Removed colorama dependency — 2026-07
- [x] Added `previous_stage_id` to call state logging — 2026-07
- [x] Stage tracking null-safety fix (`None` initialization) — 2026-07
- [x] DB connection timeout + dashboard error reporting — 2026-07
- [x] Cartesia TTS migration to LiveKit Inference — 2026-06
- [x] Dynamic tone/style configurations for agent prompts — 2026-06
- [x] `end_call` tool with graceful disconnect — 2026-05
- [x] Automated crash email notifications — 2026-05
- [x] Webhook-based call log storage — 2026-05

## Blocked

- Plivo proxy routing stability (India infra) — awaiting provider feedback
