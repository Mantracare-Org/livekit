# Call Orchestration System

This document outlines the architecture, constraints, and operational logic of the Call Orchestration System implemented in the `mantra` codebase to safely manage high volumes of inbound and outbound LiveKit calls without exhausting Cartesia API concurrency or LiveKit room limits.

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Redis Schema & Queue Design](#redis-schema--queue-design)
3. [Environment Configuration](#environment-configuration)
4. [Dispatcher Logic](#dispatcher-logic)
5. [Future Upgrades (Priority Queueing)](#future-upgrades)

---

## Architecture Overview

To handle large bursts of incoming calls, the system utilizes a **Producer-Consumer** pattern backed by **Redis**.

- **Producer (`ui_server.py`)**: When webhooks (e.g., from Plivo) hit the server, instead of immediately spinning up a LiveKit agent dispatch and SIP participant, the API constructs the full call payload, assigns a `call_id` and `room_name`, and pushes it to a Redis sorted set (`queue:pending`).
- **Source of Truth (Redis)**: Redis tracks pending calls, the number of currently active calls (`calls:active`), and the lifecycle status of every specific call ID.
- **Consumer (`dispatcher.py`)**: A standalone background worker that continuously checks Redis to monitor real-time capacity. When capacity opens up, it dequeues the next call, updates Redis state, and securely passes the payload to LiveKit's `agent_dispatch` API.
- **Agent Finalization (`agent.py`)**: When the LiveKit session natively closes (or the human drops), the Python agent worker sends its analytics to the backend, and as a final step, removes itself from the `calls:active` Redis hash, instantly freeing the slot for the next queued call.

---

## Redis Schema & Queue Design

We employ the following Redis structures:

1. `queue:pending` **(Sorted Set)**
   - Acts as the main FIFO queue. 
   - **Key:** `queue:pending`
   - **Member:** JSON string of the call payload.
   - **Score:** Unix timestamp. This inherently acts as a First-In-First-Out mechanism but allows for future Priority Queueing (by setting scores to `1` for high priority, etc.).
   - Dequeuing occurs via `ZPOPMIN`, which retrieves the item with the lowest score (oldest timestamp or highest priority).

2. `calls:active` **(Hash)**
   - Tracks concurrent, active LiveKit rooms and agents.
   - **Field:** `call_id`
   - **Value:** `room_name`
   - The size of this hash (`HLEN`) dictates the current load. The dispatcher subtracts this from the environment constraints to determine available slots.

3. `calls:status:<call_id>` **(String)**
   - Detailed state tracking for debugging.
   - Example states: `queued`, `dispatching`, `in_progress`, `completed`, `failed_dispatch_requeued`, `completed_or_failed_zombie`.

---

## Environment Configuration

The orchestration system relies on parameters defined in `.env.local`:

```ini
# ORCHESTRATION LIMITS
REDIS_URL=redis://localhost:6379
CARTESIA_MAX_CONCURRENCY=5
LIVEKIT_MAX_ROOMS=5
AGENT_MAX_WORKERS=5
```

The dispatcher calculates its availability by evaluating the minimum of all three constraints against the `HLEN calls:active` metric.

---

## Dispatcher Logic

The `dispatcher.py` operates as an asynchronous `while True` loop:

1. **Zombie Sweeping (Every 60s)**: It pulls all active rooms from LiveKit (`lk_client.room.list_rooms()`). Any `room_name` present in `calls:active` that is NOT found in the LiveKit server is treated as a ghost/zombie call (e.g., the agent crashed). The slot is forcefully purged from Redis.
2. **Capacity Check**: Evaluates if `min(Limits) - HLEN(calls:active) > 0`.
3. **Dequeue**: `ZPOPMIN` pops the highest priority call.
4. **State Transition**: The call is immediately marked as active in the Redis Hash.
5. **Execution**: The LiveKit Cloud API is invoked for both the Agent Dispatch and the SIP Participant creation.
   - *Failure Path*: If LiveKit rejects the dispatch (e.g., rate limits, networking errors), the call is removed from `calls:active` and optionally re-queued into `queue:pending`.

---

## Future Upgrades (Priority Queueing)

Because the system was designed with a Redis Sorted Set (`zadd`), migrating to Priority Queueing requires zero schema changes.

**Implementation Plan for Priority Queue:**
1. In `ui_server.py`, extract the priority level from the incoming webhook.
2. Map priorities to numeric scores:
   - `High Priority` = `score 10`
   - `Medium Priority` = `score 20`
   - `Low Priority` = `score 30`
3. Modify the `zadd` call to use these static scores instead of the `time.time()` timestamp.
4. (Optional) For high-throughput scenarios where multiple calls share the exact same priority score, you can encode the score as `priority + (timestamp / 1e10)` to maintain FIFO ordering *within* identical priority tiers.
