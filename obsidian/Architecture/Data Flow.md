# Data Flow

## Outbound Call Flow

```
Step 1: TRIGGER
  External system → POST /api/v1/webhooks/telephony
  Payload: { client_phone, prompt, client_name, call_id, lead_id, trunk_id, ... }

Step 2: WEBHOOK HANDLER (ui_server.py)
  ├── Parse payload, resolve phone number (E.164)
  ├── Resolve SIP trunk ID & provider (Twilio/Plivo/Zadarma)
  ├── Create agent dispatch via LiveKit API → agent spawned in room
  ├── Create SIP participant (background task) → dials phone number
  └── Return { room, token, url } to caller

Step 3: VOICE AGENT (agent.py)
  ├── Connect to room
  ├── Wait for remote participant (60s timeout)
  ├── Generate greeting via LLM
  ├── STT → LLM → TTS loop (real-time conversation)
  │   ├── STT: Deepgram Nova-3 (Hindi model for Hinglish)
  │   ├── LLM: GPT-4o-mini / Gemini / DeepSeek
  │   ├── TTS: Cartesia Sonic-3 with fallback keys
  │   └── VAD: Silero + Multilingual Turn Detection
  ├── end_call tool → graceful disconnect
  └── Post-call processing

Step 4: POST-CALL (agent.py, finally block)
  ├── Cancel background tasks (limiter, inactivity, safety net)
  ├── Capture history snapshot (transcript)
  ├── Stop recording → mix audio → upload MP3 to S3
  ├── LLM analysis → summary, stage transition, sentiment
  ├── Build webhook payload → send to MantraAssist backend
  ├── Save call log to PostgreSQL
  ├── Free capacity slot in Redis (calls:active)
  └── Log completion summary
```

## Inbound Call Flow

```
Step 1: Agent auto-starts when LiveKit detects an inbound SIP room
Step 2: Resolve KB scope
  ├── Parse metadata — extract phone_number
  ├── POST /api/v1/telephony/resolve-inbound-call → backend
  └── Returns: org_id, kb_id, kb_tags, prompt, voice, model
Step 3: Build KB scope from resolved context
  ├── org_id always appended as kb_id
  ├── kb_id/kb_ids from payload appended
  └── kb_tags from payload appended
Step 4: Entrypoint detects missing Redis tracking → creates calls:active entry
Step 5: Build agent instructions + register search_knowledge_base tool
Step 6: Same voice pipeline as outbound
Step 7: Same post-call processing
```

## Queue-Based Dispatch (deprecated path via dispatcher.py)

```
Webhook → Redis sorted set (queue:pending) → Dispatcher (0.5s poll)
                                                ├── Check capacity limits
                                                ├── Pop highest-priority call
                                                ├── Create agent dispatch
                                                ├── Create SIP participant
                                                └── On failure: re-queue
```

## Redis Data Structures

| Key | Type | Purpose |
|-----|------|---------|
| `queue:pending` | Sorted Set | Call queue (score = priority) |
| `calls:active` | Hash | `call_id → room_name` mapping |
| `calls:status:{call_id}` | String | Current call status |
| `sip_error_status:{call_id}` | String | SIP error from UI server (TTL 300s) |
