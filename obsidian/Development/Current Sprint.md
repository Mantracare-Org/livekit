# Current Sprint

> **Sprint:** N/A (no formal sprint process)  
> **Last Updated:** 2026-06-30  
> **Status:** Active maintenance + incremental features

## In Progress

- [ ] Migrate remaining `mantra/agent.py` tool callbacks to separate module
- [ ] Set up automated test suite (currently manual only)

## Recently Completed

- [x] Race condition fix v2: lock TTL 30→600s + duplicate room guard + agent Redis trust fix — 2026-07-24
- [x] Race condition fix v1: Redis dedup lock + null safety + logger fix — 2026-07-24
- [x] Cartesia TTS migration to LiveKit Inference — 2026-06
- [x] Dynamic tone/style configurations for agent prompts — 2026-06
- [x] `end_call` tool with graceful disconnect — 2026-05
- [x] Automated crash email notifications — 2026-05
- [x] Webhook-based call log storage — 2026-05

## Blocked

- Plivo proxy routing stability (India infra) — awaiting provider feedback
