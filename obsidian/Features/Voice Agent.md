# Voice Agent

**File:** `mantra/agent.py` (3,007 lines)

## Overview

The core real-time voice AI agent. Connects to LiveKit rooms, handles the full STT→LLM→TTS pipeline, and manages call lifecycle.

## Voice Pipeline

1. **STT:** Deepgram Nova-3 (`smart_format`, `punctuate`, `numerals`; `endpointing_ms=100` for code-switching) with dynamic locale routing — `en-IN` for Indian callers, `en-US` otherwise, dedicated `hi` model for Hindi, `multi` default — plus per-call keyterm memory (≤80 terms, seeded from call context, learns names/places per turn)
2. **LLM:** Selectable via metadata:
   - `openai` → GPT-4o-mini (default)
   - `gemini` → Gemini 2.5 Flash
   - `deepseek` → DeepSeek v4 Flash/Pro (via OpenAI-compatible API; 60s streaming read timeouts + `APIConnectOptions` hardening for TTFT)
3. **TTS:** LiveKit native sonic-3 (no Cartesia dependency)
4. **VAD:** Silero (`min_speech_duration=0.08s`, `min_silence_duration=0.25s`, `prefix_padding_duration=0.10s`)
5. **Turn Detection:** LiveKit `inference.TurnDetector` (server defaults) with dynamic endpointing (`min_delay: 0.10s`, `max_delay: 0.80s`), VAD barge-in (`min_words: 1`, `min_duration: 0.15s`, `discard_audio_if_uninterruptible: True`, `resume_false_interruption: True`), and preemptive TTS generation

## Language Matching

Hinglish telesales style (Roman-script, short 1-2 sentence turns, active listening, flat prosody, brand single-word pronunciation guards). `MantraMultilingualAgent.llm_node` aligns language synchronously every turn and rewrites the `<!-- LANGUAGE_DIRECTIVE_START/END -->` block so dynamic switches never force 100% Devanagari or 100% English:
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

- **Inactivity Monitor:** 15s nudge → 30s silence disconnect (greeting tracked strictly after agent finishes speaking, so line noise can't start the timer early)
- **Farewell Safety Net:** Detects goodbye without `end_call` → force disconnect after 10s warmup, 3s poll (directional — different phrases for inbound vs outbound)
- **Call Limiter:** 150s → farewell instructions; 180s → hard kill; outbound calls with positive intent extend to 270s / 300s (`mantra/call_duration.py` + `mantra/positive_intent.py`)
- **Crash Email:** `send_crash_email()` on entrypoint exceptions + `session.on("error")` pipeline alerts (LLM/STT/TTS, e.g. 402 balance), rate-limited to 1 per call per 5 min

## Agent Tools

Active set (`agent_tools` in `mantra/agent.py`): `end_call`, `search_knowledge_base`, `clarify_medical_department`, `check_doctor_availability`.

### `search_knowledge_base(query, specific_tag?)`
Function tool for RAG. Searches PostgreSQL hybrid (FTS + semantic RRF) across all kb_collections for the org + legacy fallback. Silent single-turn execution (no search narration, no sequential retries). Appointment/doctor-schedule questions are excluded — those route to MCP. Tracks accessed pages for post-call metadata extraction.

### `clarify_medical_department(symptom)`
Fetches the org's department list via `get_org_departments`, keeps names internal, and returns one exact department as LLM routing context. If discovery is unavailable, availability lookup still proceeds. Never asks the caller to choose aloud.

### `check_doctor_availability(date, doctor_name?, department?)`
Authoritative MCP tool for scheduling (`receive_doctor_availability`). Validates `department` exactly against the org list (rejects invented values like `Ophthalmology` with a retry directive), captures `provider_user_id` into `appointment_metadata`, and never falls back to KB.

### `end_call()`
Graceful disconnect. Triggers a 3s delay then force-disconnects the room. Guarded before greeting completion / user speech so turn-1 hallucinations can't kill the call. Required for LLM to end calls — safety net catches cases where LLM says goodbye without calling this.

### `recognize_client` (pre-greeting, not an LLM tool)
Bounded (3s) MCP call before the inbound greeting: resolves `org_id` from the dispatch DID, reads the caller number from LiveKit SIP participant metadata (never the routing DID), queries `GET /webhooks/mcp/lead?org_id=&phone=`. Accepts direct or nested names; null/timeout → anonymous, never blocks the call.

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
