# Outbound Call Walkthrough — Payload → Complete

> **Audience:** Developers onboarding to the outbound call pipeline.
> **Scope:** End-to-end trace of a single outbound call from webhook ingestion through post-call processing.
> **Files:** `ui_server.py:1934` → `agent.py:519` → `agent.py:1228` (`finalize()`) + `utils.py`

---

## Phase 0: Trigger — Payload Arrives

An external system sends a `POST /v1/webhooks/telephony` with this payload shape:

```json
{
  "event_name": "telephony_dispatch",
  "call_id": "12345",
  "client_phone": "+91......",
  "client_name": "........",
  "client_country_code": "91",
  "trunk_id": "ST_trunk_abc123",
  "prompt": "You are calling from MantraCare...",
  "lead_id": "lead_678",
  "model": "deepseek",
  "voice": "arushi",
  "metadata": {},
  "tos_task_id": "tos_999"
}
```

**Key fields:** `call_id`, `client_phone` (E.164 dial target), `trunk_id` (LiveKit SIP outbound trunk), `prompt` (LLM system prompt override), `model`/`voice` (LLM/TTS selection).

---

## Phase 1: Webhook Handler (`ui_server.py:1934`)

### 1b. Phone & Trunk Resolution

- Normalizes `client_phone` to E.164 (prepend `+{country_code}` if missing)
- Resolves `trunk_id` from payload (`trunk_id` -> `call_from_id` -> env `SIP_TRUNK_ID`)
- Detects provider from trunk address via `_get_provider_from_trunk()` (twilio / plivo / zadarma)
- Stamps `call_initiated_at` in metadata for post-call timing

### 1c. Agent Dispatch

```python
dispatch = await lk_client.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(
        room=room_name, agent_name="mantra-agent", metadata=json.dumps(payload)
    )
)
```

This tells LiveKit Cloud to spin up an agent worker process that connects to room `call_{call_id}`.

### 1d. SIP Call (Background Task)

The handler returns `HTTP 200` immediately to the caller, then fires a background task that:

```python
sip_part = await sip_client.sip.create_sip_participant(
    api.CreateSIPParticipantRequest(
        sip_trunk_id=trunk_id,
        sip_call_to=phone_number,
        sip_number=sip_number,
        room_name=room_name,
        participant_identity=f"sip_{call_id}",
        wait_until_answered=True,
        play_ringtone=False,
    )
)
```

- Uses **plivo_client** (proxied for India routing) for Plivo trunks, **lk_client** (direct) otherwise
- `wait_until_answered=True` means the SIP participant only joins the room when the phone is picked up
- On SIP failure (timeout, busy, declined), writes status to Redis `sip_error_status:{call_id}` and deletes the room

### 1e. Monitor Token

Returns a `monitor_` JWT token allowing dashboard users to subscribe to the room (listen only) without publishing.

**Response shape:**

```json
{
  "status": "success",
  "room": "call_12345",
  "token": "...",
  "url": "wss://..."
}
```

---

## Phase 2: Agent Entrypoint (`agent.py:519`)

LiveKit Cloud schedules the agent dispatch. The `entrypoint()` coroutine runs inside an agent worker subprocess.

### 2a. Room Connection

```python
@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    await ctx.connect()
```

The agent joins the LiveKit room. Audio tracks are not yet flowing because the SIP participant hasn’t answered yet.

### 2b. Call State Initialization

```python
call_state = {"user_joined": False, "agent_joined_at": ..., "timeline": [...]}
tos_task_id = ctx.job.metadata.get("tos_task_id")
```

Tracks lifecycle events for the post-call timeline.

### 2c. Metadata Parsing & Instruction Assembly

The agent parses `ctx.job.metadata` and builds the LLM `initial_instructions` string:

- Injects the **prompt** field as system instructions
- Extracts **client_name**, **call context** fields (every key becomes a bullet)
- Injects **IVR context** if present (account_number, call_reason, language, etc.)
- Appends **overriding rules** (no repetition, no push, transfer_to_human on request)
- For outbound calls: includes **farewell phrases** in safety net config

### 2d. LLM / TTS / STT Selection

```python
model_name = payload.get("model") or "openai"
voice_id = VOICE_MAPPING.get(payload.get("voice", "arushi"))
voice_speed = payload.get("voice_speed") or 1.0

if model_name == "gemini":
    llm_engine = google.LLM(model="gemini-2.5-flash")
elif model_name == "deepseek":
    llm_engine = openai.LLM(model="deepseek-v4-flash", ...)
else:
    llm_engine = openai.LLM(model="gpt-4o-mini")

stt = deepgram.STT(model="nova-3", language="multi", smart_format=True)
tts = inference.TTS(model="sonic-3", voice=voice_id, ...)
vad = silero.VAD.load(...)
```

8 voices available (arushi, gemma, alistair, sunny, tyler, vikas, camila, renata).

### 2e. Agent Initialization

```python
session = AgentSession(turn_handling=..., vad=vad, stt=stt, llm=llm_engine, tts=tts_engine)
agent_tools = [fnc_ctx.end_call, fnc_ctx.search_knowledge_base, fnc_ctx.transfer_to_human]
agent = Agent(instructions=initial_instructions, tools=agent_tools)
await session.start(agent=agent, room=ctx.room)
```

Tools available to the LLM:

- `end_call()`: Graceful disconnect with 3-second delay
- `search_knowledge_base(query, specific_tag)`: PostgreSQL FTS search against KB pages
- `transfer_to_human(reason, department)`: Dials a human agent into the room via SIP, silences the AI

---

## Phase 3: Conversation (`agent.py:1141-1203`)

### 3a. Wait for Remote Participant

```python
if ctx.room.name.startswith("test_"):
    call_state["user_joined"] = True  # Skip wait for test rooms
else:
    while not list(ctx.room.remote_participants.values()):
        await asyncio.sleep(0.5)
        if elapsed > 60.0:
            await _force_disconnect_room(ctx)
            return  # No answer -> cleanup
```

The agent blocks here until the SIP call is answered and the remote participant appears. 60-second timeout.

### 3b. Greeting Generation

```python
session.generate_reply(
    instructions=f"Greet the user named {client_name} and follow the opening script.")
```

The LLM generates the first utterance, which is spoken via TTS. The conversation loop is now live.

### 3c. STT → LLM → TTS Loop

Controlled by `AgentSession` internally:

- **VAD** (Silero): Detects when the user starts/stops speaking
- **Turn Detection** (MultilingualModel): Manages barge-in, endpointing, interruptions
- **STT** (Deepgram Nova-3): Transcribes user speech (`language=multi` for EN/HI/Hinglish)
- **LLM** (selected model): Generates response text
- **TTS** (LiveKit native sonic-3): Synthesizes speech, played to the room
- **Preemptive TTS**: Starts synthesizing before LLM completes for lower latency

### 3d. Background Monitors (3 parallel tasks)

**Inactivity Monitor** (`inactivity_monitor()`):

- 5s without speech: generates "Are you still there?" prompt
- 10s without speech: force-disconnects the room

**Farewell Safety Net** (`farewell_safety_net()`):

- Every 3s, checks the last LLM message for farewell phrases (`goodbye`, `take care`, etc.)
- If the LLM said goodbye without calling `end_call`, force-disconnects after 3s delay
- Outbound phrase set includes `thanks for calling`, `thank you for calling`

**Call Limiter** (`call_limiter()`):

- At t+150s: Updates agent instructions to inject farewell prompt
- At t+180s: Hard force-disconnect (room deleted via LiveKit API)
- Cancelled if call ends naturally before limits

---

## Phase 4: Call End (`agent.py:1228-1478`)

The call ends via one of:

1. LLM calls `end_call` tool -> 3s delay -> room disconnect
2. Remote participant hangs up -> `participant_disconnected` event -> force disconnect
3. Inactivity timeout (10s) -> force disconnect
4. Farewell safety net -> force disconnect
5. Hard limiter (180s) -> force disconnect

All paths converge to the `finally` block of `entrypoint()`, which calls `asyncio.shield(finalize())`.

---

## Phase 5: Post-Call (`agent.py:1247-1478`, `finalize()`)

### 5a. Cancel Background Tasks

```python
for task_name in ["limiter_task", "inactivity_task", "goodbye_task", "safety_net_task"]:
    task = locals().get(task_name)
    if task and not task.done():
        task.cancel()
```

Stops all monitors before post-call processing.

### 5b. Capture History Snapshot

```python
history_snapshot = list(session.history.messages())  # Transcript source
```

Captured before the session cleans up, used for transcript building and LLM analysis.

### 5c. Determine Call Status

| Condition                                 | Status      |
| ----------------------------------------- | ----------- |
| Participant never joined, ring_time >= 30s | `No Answer` |
| Participant never joined, 3s <= ring < 30s | `Busy`      |
| Participant never joined, ring_time < 3s   | `Failed`    |
| Joined but no user messages (outbound only) | `No Answer` |
| User spoke at least once (or inbound call)  | `Completed` |

> Inbound calls (`direction == "inbound"`) are always treated as `user_joined = True`, so they resolve to `Completed` and run post-call LLM analysis even when no user utterance was captured.

### 5d. Recording → S3

```python
await recorder.stop_recording()
if call_status == "Completed":
    mp3_bytes = recorder.get_combined_mp3_bytes()
    # Mixed agent + user audio, silence-trimmed, exported as 128k MP3
    recording_url = await upload_to_s3(mp3_bytes, f"recordings/{call_id}.mp3")
```

- `SessionRecorder` captures raw PCM frames per track as numpy arrays
- On stop: mixes all tracks, trims leading silence via pydub, exports MP3 to in-memory buffer
- Uploads to S3 bucket with public-read ACL
- Skips entirely if status is Busy/No Answer

### 5e. Build Transcript

```python
transcript_data = SessionRecorder.build_transcript(list(history_snapshot))
# Output: JSON array of {bot: "..."}, {user: "..."} objects
```

Filters out system messages. Labels roles as `bot` / `user`.

### 5f. LLM Analysis

Runs when `call_status == "Completed"` and a transcript (snapshot) exists. Because inbound calls are always treated as joined, this runs for every connected inbound call; for outbound calls it is skipped when `user_spoke` is false. The engine is selected as `target_llm = post_call_llm or llm_engine` — the dedicated post-call model (`build_post_call_llm()`, default `deepseek-v4-pro`, fallback `gpt-4o-mini`) is preferred over the live-call engine for more reliable transcript analysis.

```python
analysis = await SessionRecorder.analyze_call(
    llm_engine=target_llm,
    history=list(history_snapshot),
    current_stage_id=current_stage_id,
    stage_details=stage_details,
    duration=duration,
    client_country_code=...,
    process_stage_data=kb_process_stage_data,
)
```

The LLM receives:

1. **Stage details** / **Process stage data** from the payload
2. **Full transcript**
3. **Current time** (for scheduling `next_call_on`)

Returns a JSON object with:

- `summary`: One-paragraph call summary
- `process_id` / `new_stage_id`: CRM stage transition
- `next_call_on`: Follow-up datetime in IST
- `appointment_date_time`, `doctor`, `hospital_location`: Extracted entities
- `sentiment_score`: 0.0-1.0

If analysis times out or fails, `ai_summary` is still guaranteed non-empty: `summary_text` falls back to a transcript snippet (`"Call completed ({duration}s). Transcript snippet: ..."`) or `"Call completed."`.

### 5g. Build & Send Webhook

```python
event_name = "CALL_RETRY" if call_status in ["No Answer", "Busy", "Incomplete", "Failed"] else "CALL_DATA_UPDATE"
webhook_payload = {
    "event": event_name,
    "data": {
        "call_transcript": transcript_data,
        "ai_summary": summary_text or "",
        "recording_url": recording_url,
        "call_duration_seconds": duration,
        "next_call_on": ...,
        "called_on": ...,
        "process_id": ...,
        "new_stage_id": ...,
    }
}

# Save to PostgreSQL call_logs table (local)
await save_call_log_to_db(call_id, ..., status, recording_url)

# Send to MantraAssist backend with HMAC-SHA256 signing
await send_to_backend(webhook_payload)
```

The backend webhook includes:

- `x-timestamp`: Unix timestamp
- `x-timestamp-iso`: ISO 8601 timestamp
- `x-signature`: HMAC-SHA256 of `payload.timestamp`
- Up to 3 retries with exponential backoff (1s, 2s, 4s)

### 5h. TOS Telemetry

Sends final telemetry event: `call_complete - status=<status>, duration=<N>s`

### 5i. Free Redis Capacity

The active call count in Redis (`calls:active`) is decremented when the room is deleted by `_force_disconnect_room()`.

---

## Phase 6: Room Cleanup (`agent.py:1481-1498`)

```python
async def _force_disconnect_room(ctx: JobContext):
    # Try LiveKit API room deletion first
    await lk_api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
    # Fall back to local disconnect
    await ctx.room.disconnect()
```

Called from: inactivity timeout, participant disconnect, safety net, call limiter, end_call tool, or SIP failure cleanup.
Also removes the Redis `calls:active` entry (handled by dispatcher periodic zombie cleanup as fallback).

---

## Sequence Diagram (Simplified)

```
External        UI Server       LiveKit Cloud    Agent Worker    SIP Trunk      Redis        S3       MantraAssist
  |                 |               |               |              |             |         |            |
  |--POST /webhook-->|               |               |              |             |         |            |
  |                 |--dispatch----->|               |              |             |         |            |
  |                 |--create_sip_participant------->|              |             |         |            |
  |<---HTTP 200------|               |               |              |             |         |            |
  |                 |               |--spawn-------->|              |             |         |            |
  |                 |               |               |--connect----->|             |         |            |
  |                 |               |               |wait for user  |             |         |            |
  |                 |               |               |<--SIP answered|             |         |            |
  |                 |               |               |--greeting---->|             |         |            |
  |                 |               |               |==STT/LLM/TTS=>|             |         |            |
  |                 |               |               |  (conversation)|             |         |            |
  |                 |               |               |--end_call----->|             |         |            |
  |                 |               |<--room delete----------------->|             |         |            |
  |                 |               |               |--S3 upload------------------------>|         |            |
  |                 |               |               |--webhook POST------------------------------------->|
  |                 |               |               |--DB save------->|  call_logs|         |            |
  |                 |               |               |--telemetry----->|  TOS      |         |            |
```

---

## Key Design Properties

| Property                | Mechanism                                                                |
| ----------------------- | ------------------------------------------------------------------------ |
| **Dedup**               | None — duplicate `call_id` requests processed immediately (no Redis lock) |
| **Capacity**            | Dispatcher checks `calls:active` hash length against env limits          |
| **No-answer handling**  | 60s wait in agent + SIP failure stored to Redis -> status=No Answer/Busy |
| **Recording**           | In-memory PCM -> silence trim -> 128k MP3 -> S3                          |
| **Call duration**       | 180s hard kill via `call_limiter`                                        |
| **Graceful goodbye**    | 150s farewell instruction + end_call tool + safety net                   |
| **State transition**    | LLM analysis selects new_stage_id from CRM stages or process_stage_data  |
| **Backoff**             | 3 retries with 1s/2s/4s exponential backoff for backend webhook          |
| **SIP provider choice** | Provider detected from trunk address; Plivo uses proxied client          |
| **Auth**                | HMAC-SHA256 signed webhook payload to MantraAssist backend               |

---

## Failure Modes

| Failure                 | Effect                                                      | Recovery                                |
| ----------------------- | ----------------------------------------------------------- | --------------------------------------- |
| Duplicate payload       | Ignored by Redis lock                                       | N/A                                     |
| SIP timeout (408)       | `sip_error_status`=No Answer, room deleted                  | Agent never connects, post-call skipped |
| SIP busy (486)          | `sip_error_status`=Busy, room deleted                       | Same as timeout                         |
| Agent dispatch failure  | HTTP 500 returned to caller                                 | Caller must retry                       |
| Agent crash mid-call    | Crash email sent, `finally` block runs                      | Post-call still executes                |
| S3 upload failure       | `recording_url`=None in webhook                             | Call data still sent to backend         |
| Backend webhook failure | 3 retries w/ backoff, logged                                | Data saved to local PostgreSQL          |
| Redis unavailable       | Skip Redis operations (queue, capacity, SIP error status) | Degraded but functional                 |
