# Voice Agent

**File:** `mantra/agent.py` (1,629 lines)

## Overview

The core real-time voice AI agent. Connects to LiveKit rooms, handles the full STT→LLM→TTS pipeline, and manages call lifecycle.

## Voice Pipeline

1. **STT:** Deepgram Nova-3 (`language="multi"` — English + Hindi/Hinglish)
2. **LLM:** Selectable via metadata:
   - `openai` → GPT-4o-mini (default)
   - `gemini` → Gemini 2.5 Flash
   - `deepseek` → DeepSeek v4 Flash (via OpenAI-compatible API)
3. **TTS:** LiveKit native sonic-3 (no Cartesia dependency)
4. **VAD:** Silero (`min_speech_duration=0.10s`, `min_silence_duration=0.25s`, `prefix_padding_duration=0.20s`)
5. **Turn Detection:** LiveKit `inference.TurnDetector` with fixed endpointing (`min_delay: 0.25s`, `max_delay: 1.50s`) and adaptive interruptions (`min_words: 2`, `min_duration: 0.40s`, `resume_false_interruption: True`)

## Language Matching

Agent instructions require turn-by-turn language matching:
- Default/open in English
- Reply in the caller's language each turn (English → English, Hindi → Hindi)
- One Hindi filler (`arre`, `yaar`, `ji`) in mostly-English speech does **not** switch the agent to Hindi
- If the caller switches back to English, agent switches back immediately

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
| sia | `4459a9a5-69d6-4680-b970-e13dc51845b6` |
| sneha | `6b02ffe5-e3cb-48c0-a023-c72f85953375` |
| kavita | `56e35e2d-6eb6-4226-ab8b-9776515a7094` |
| katie | `f786b574-daa5-4673-aa0c-cbe3e8534c02` |
| cathy | `e8e5fffb-252c-436d-b842-8879b84445b6` |

## Safety Systems

- **Inactivity Monitor:** 5s prompt → 10s no-response timeout → force disconnect
- **Farewell Safety Net:** Detects goodbye without `end_call` → force disconnect after 10s warmup, 3s poll (directional — different phrases for inbound vs outbound)
- **Call Limiter:** 2m30s → farewell instructions; 3m → hard kill
- **Crash Email:** `send_crash_email()` on entrypoint exceptions

## Agent Tools

### `search_knowledge_base(query, specific_tag?)`
Function tool for RAG. Searches PostgreSQL FTS across all kb_collections for the org + legacy fallback. Returns formatted results to LLM. Tracks accessed pages for post-call metadata extraction.

### `end_call()`
Graceful disconnect. Triggers a 3s delay then force-disconnects the room. Required for LLM to end calls — safety net catches cases where LLM says goodbye without calling this.

## Handoff to Human (`transfer_to_human`)

**Status: DISABLED** — Code preserved but commented out in `agent.py` (lines ~279-422).

The full implementation (when re-enabled): department-based transfer number resolution via `TRANSFER_NUMBERS` dict, SIP participant creation in same room, webhook notification, agent silence enforcement via `update_instructions()`, speech interruption via `session.interrupt()`. Known issue: race condition with tool return producing residual `"..."` utterance.

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

Inbound client recognition returns the following context when the caller is found:

```json
{
  "client_name": "Amit Bhati",
  "client_metadata": {
    "ai_summaries": [
      {"date": "2026-05-01", "summary": "No summary provided"}
    ],
    "custom_fields": [
      {"custom_field_name": "doctor", "custom_field_value": ""}
    ]
  }
}
```

The agent uses this metadata as private call context and does not disclose the recognition lookup to the caller.

## Post-Call Processing

See [[Post-Call Processing.md]].
