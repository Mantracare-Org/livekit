# Architecture Overview

## Pattern

Modular, asynchronous, multi-process architecture based on Python `asyncio`.

## Processes

| Process | File | Role |
|---------|------|------|
| Agent Worker | `mantra/agent.py` (~3,007 lines) | Connects to LiveKit rooms, handles real-time STT→LLM→TTS voice pipeline + TOS telemetry + client recognition + department routing |
| Dispatcher | `mantra/dispatcher.py` (~252 lines) | Background loop: pops Redis queue → checks capacity → dispatches to LiveKit + TOS telemetry |
| UI/API Server | `mantra/ui_server.py` (~4,489 lines) | FastAPI HTTP server: webhooks, SIP trunk management, per-trunk capacity gating, health gate, dashboard APIs, KB ingestion, org config CRUD |
| MCP Server | `mcp/server.py` (~1,073 lines, legacy local) + remote `livekit-mcp` (SSE/OAuth) | Local: PostgreSQL tools (patients, doctors, appointments, call logs). Live: `receive_doctor_availability`, `get_org_departments`, `fetch_org_processes`, `recognize_client` via `MantraMCPClient` (`mantra/mcp_client.py`) |
| Call Duration | `mantra/call_duration.py` | Base limits 150s farewell / 180s hard; outbound positive-intent extension to 270s / 300s |
| Positive Intent | `mantra/positive_intent.py` | Heuristic outbound monitor that triggers the 3m → 5m extension |

## System Diagram

```
┌─────────────┐     HTTP POST     ┌───────────────┐   Redis Queue    ┌──────────────┐
│   External   │ ─── webhook ──>  │  UI/API Server │ ── queue:pending ─>  Dispatcher  │
│  Telephony   │                  │  (FastAPI)     │                  │  (background)│
│  (Twilio/    │ <── SIP call ──  │  :8081         │                  └──────┬───────┘
│   Plivo/     │                  └───────┬───────┘                         │
│   Zadarma/   │                          │                                 │
│   VoiceLink) │                          │                                 │
└─────────────┘                            │                          ┌──────┴──────┐
                                    ┌───────┴───────┐                  │ LiveKit     │
                                    │  Static Files  │                  │ Cloud API   │
                                    │  (HTML/JS/CSS) │                  └──────┬──────┘
                                    └───────┬───────┘                         │
                                            │                          ┌──────┴──────┐
                                    ┌───────┴───────┐                  │ Voice Agent │
                                    │  PostgreSQL   │                  │ agent.py    │
                                    │  (call_logs   │                  │             │
                                    │   + kb_pages) │                  │ STT→LLM→TTS│
                                    └───────────────┘                  └──┬──────┬───┘
                                                                          │      │
                             ┌────────────────────┐               ┌──────┘      └──────┐
                             │  AWS S3 (recordings)│               │  Human Agent       │
                             └────────────────────┘               │  (SIP Participant  │
                                                                   │   dialed into room)│
                                                                   └────────────────────┘
```

## Key Architecture Decisions

1. **Redis as coordination layer** — Queue, active call state, capacity tracking, trunk→provider cache (`trunk:provider:{id}`) all live in Redis
2. **Three LiveKit API clients** — Direct (Twilio/Zadarma) + Proxied (Plivo for India routing) + Proxied (VoiceLink)
3. **In-memory session recording** — `SessionRecorder` holds audio as numpy arrays, never touches disk
4. **HMAC-signed webhooks** — Post-call data sent to MantraAssist backend with SHA-256 signing
5. **LiveKit native `sonic-3` TTS** — No external TTS dependency (Cartesia removed); 13 voices in `VOICE_MAPPING`
6. **Handoff via co-room SIP** — Human agent dialed into the same LiveKit room as AI + caller, then AI silenced via `update_instructions` (currently disabled, code preserved)
7. **Trunk-based capacity + lifecycle** — Room names `call_{trunk_id}_{call_id}`; each trunk has an independent limit; inbound setup requires explicit `provider` (no default), rolls back LiveKit resources on provider-forwarding failure, and trunk delete cascades to dispatch rules → `org_configs` → Redis
8. **MCP over SSE with dynamic OAuth** — `MantraMCPClient` acquires tokens via `OAUTH_CLIENT_ID/SECRET`, sends browser UA + `ngrok-skip-browser-warning` headers, bypasses proxy via `NO_PROXY`; availability/department/process/recognition tools live in remote `livekit-mcp`
9. **Fast VAD barge-in** — `interruption.mode="vad"` (0.15s, `discard_audio_if_uninterruptible`) + dynamic endpointing (0.10s–0.80s) so callers interrupt TTS promptly
10. **Department-gated availability** — `clarify_medical_department` resolves broad symptoms to one exact org department; `check_doctor_availability` rejects invented departments and never uses KB for scheduling
11. **Inbound client recognition** — Pre-greeting `recognize_client` (3s bounded) resolves caller number from LiveKit SIP participant metadata, injects `client_name` + `ai_summaries` + `custom_fields`; null/timeout → anonymous, never blocks the call

See [[Architecture/Design Decisions.md]] for the full decision log.
