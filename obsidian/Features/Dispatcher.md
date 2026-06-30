# Dispatcher

**File:** `mantra/dispatcher.py` (172 lines)

## Overview

Background worker that dequeues calls from Redis and dispatches them to LiveKit. Runs as a standalone process.

## Flow

```
Loop (0.5s interval):
  1. Zombie cleanup (every 60s)
  2. Check capacity (min of CARTESIA_MAX_CONCURRENCY, AGENT_MAX_WORKERS, LIVEKIT_MAX_ROOMS)
  3. Pop lowest-score entry from queue:pending (sorted set)
  4. Create agent dispatch → SIP participant
  5. On failure: re-queue with score+10, mark failed_dispatch_requeued
```

## Zombie Cleanup

Periodically queries LiveKit's `list_rooms()` and compares against Redis `calls:active`. Removes entries for rooms that no longer exist, marking them `completed_or_failed_zombie`.

## Capacity Limits

| Variable | Description |
|----------|-------------|
| `CARTESIA_MAX_CONCURRENCY` | Max concurrent TTS streams |
| `AGENT_MAX_WORKERS` | Max LiveKit agent workers |
| `LIVEKIT_MAX_ROOMS` | Max concurrent rooms |
