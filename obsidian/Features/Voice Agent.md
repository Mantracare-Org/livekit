# Voice Agent

**File:** `mantra/agent.py` (995 lines)

## Overview

The core real-time voice AI agent. Connects to LiveKit rooms, handles the full STT→LLM→TTS pipeline, and manages call lifecycle.

## Voice Pipeline

1. **STT:** Deepgram Nova-3 (configured with `language="hi"` for Hinglish)
2. **LLM:** Selectable via metadata:
   - `openai` → GPT-4o-mini (default)
   - `gemini` → Gemini 2.5 Flash
   - `deepseek` → DeepSeek v4 Flash (via OpenAI-compatible API)
3. **TTS:** Cartesia Sonic-3 with FallbackAdapter (multiple API keys for rate limit cycling)
4. **VAD:** Silero (`min_speech_duration=0.08`, `min_silence_duration=0.15`)
5. **Turn Detection:** MultilingualModel

## Voice Mapping

| Key | Voice ID |
|-----|----------|
| arushi | `95d51f79-c397-46f9-b49a-23763d3eaa2d` |
| gemma | `62ae83ad-4f6a-430b-af41-a9bede9286ca` |
| alistair | `c8f7835e-28a3-4f0c-80d7-c1302ac62aae` |
| sunny | `156fb8d2-335b-4950-9cb3-a2d33befec77` |
| tyler | `820a3788-2b37-4d21-847a-b65d8a68c99a` |
| vikas | `adf97b9d-905c-41de-9fe9-afb387116d06` |
| camila | `bef2ba57-5c10-433b-b215-3bef35110a81` |
| renata | `d3793b7b-4996-409c-9d59-96dd09f47717` |

## Safety Systems

- **Inactivity Monitor:** 10s no-response timeout → force disconnect
- **Farewell Safety Net:** Detects goodbye without `end_call` → force disconnect after 10s warmup, 3s poll
- **Call Limiter:** 2m30s → farewell instructions; 3m → hard kill
- **Crash Email:** `send_crash_email()` on entrypoint exceptions

## Configuration via Metadata Payload

```json
{
  "prompt": "Custom agent instructions...",
  "client_name": "Anurag",
  "call_id": "abc-123",
  "lead_id": "lead-456",
  "ai_payload": {
    "ai_model": "openai",
    "voice_id": "arushi",
    "voice_speed": 1.05
  },
  "stage_id": 1,
  "stageDetails": [...],
  "client_custom_fields": {},
  "client_phone": "+919876543210"
}
```

## Handoff to Human (`transfer_to_human`)

**File:** `mantra/agent.py:279-378`

An LLM-registered function tool. When the agent cannot resolve an issue or the user requests a human:

1. **Guard** — `handoff_triggered` flag prevents duplicate calls
2. **Resolve target** — Looks up `TRANSFER_NUMBERS` dict by department (`refund`/`support`/`billing`/`general`), falls back to `TRANSFER_DEFAULT_NUMBER`
3. **Create SIP participant** — Dial the human agent into the **same LiveKit room** so AI + human + caller are all present
4. **Webhook** — Sends `HANDOFF_REQUESTED` event to `MANTRAASSIST_BACKEND_URL/webhooks/n8n`
5. **Silence enforcement** — `agent.update_instructions("You are SILENT...")` + `session.interrupt()` to stop TTS
6. **Returns** `"TRANSFER_COMPLETE. Do not speak."`

**Known issue:** Race condition between tool return and silence instructions — the LLM may produce a brief residual utterance (`"..."`) that fails TTS. See TODO.

## Post-Call Processing

See [[Post-Call Processing.md]].
