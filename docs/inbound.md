# Inbound Call Support — Reference

## Overview

Inbound calls allow patients/customers to call a phone number and get connected to the `mantra-agent` voice bot. LiveKit Cloud handles the entire routing automatically — no webhook needed.

---

## Architecture

```
Caller dials +911234567890
        │
        ▼
┌───────────────────────────────────────────┐
│ Plivo / Twilio / Zadarma                   │
│ (Provider handles PSTN → SIP conversion)   │
└────────────────┬──────────────────────────┘
                 │ SIP (sip:ST_xxx@livekit.cloud)
                 ▼
┌───────────────────────────────────────────┐
│ LiveKit Cloud                              │
│                                            │
│ Inbound Trunk (ST_9crXjawUyeJp)            │
│  - owns the phone number                   │
│  - authenticates the SIP request            │
│         │                                  │
│ Dispatch Rule (SR_xxxxx)                   │
│  - matches inbound calls to this trunk     │
│  - creates individual room per caller      │
│  - auto-dispatches mantra-agent            │
│  - bridges SIP caller into the room        │
│         │                                  │
│ Room: inbound_<random>                     │
│  ├── Agent: mantra-agent                   │
│  └── Participant: SIP Caller (you)         │
└───────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────┐
│ mantra-agent (agent.py)                    │
│  - Detects direction: "inbound"           │
│  - Greets caller proactively              │
│  - Handles call via STT/LLM/TTS pipeline  │
│  - On hangup: recording → S3 → analysis   │
│    → backend webhook                      │
└───────────────────────────────────────────┘
```

## vs Outbound

| Aspect | Outbound | Inbound |
|--------|----------|---------|
| Trigger | Webhook call | Phone ringing on a DID number |
| Room creation | Explicit via `agent_dispatch.create_dispatch()` | Auto by LiveKit via dispatch rule |
| SIP participant | Server calls `create_sip_participant()` to dial out | Auto-bridged by the dispatch rule |
| Agent dispatch | Explicit API call | Auto via `RoomConfiguration.agents[]` |
| Greeting | "Hello [name], I'm calling about..." | "Hello, this is MantraCare. How can I help?" |
| Direction metadata | `direction: "outbound"` (or absent) | `direction: "inbound"` |

---

## API Endpoints

### Inbound Trunks

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/v1/sip/trunks/inbound` | Create inbound trunk |
| `PATCH` | `/api/v1/sip/trunks/inbound/{trunk_id}` | Partial-update inbound trunk fields |
| `GET` | `/api/v1/sip/trunks/inbound` | List all inbound trunks |
| `DELETE` | `/api/v1/sip/trunks/inbound/{trunk_id}` | Delete inbound trunk |

#### Create Inbound Trunk

```json
POST /api/v1/sip/trunks/inbound
{
  "name": "plivo-inbound-trunk",
  "numbers": ["+911234567890"],
  "auth_username": "MANTA2NJKXNKNKXX",
  "auth_password": "MjEwYjYxNTg0OTM2ZDdjNTMyYjVhY2E5YWIyY2Rj",
  "metadata": {
    "provider": "plivo"
  },
  "headers_to_attributes": {
    "X-Account-Number": "account_number",
    "X-Call-Reason": "call_reason",
    "X-Department": "department",
    "X-Language": "language",
    "X-User-ID": "user_id"
  },
  "include_headers": "all",
  "allowed_addresses": ["203.0.113.0/24"],
  "allowed_numbers": ["+919876543210"]
}
```

**Required fields:** `name`, `numbers`, `auth_username`, `auth_password`
**Optional:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `metadata` | object/string | `{}` | Arbitrary metadata JSON |
| `allowed_addresses` | string[] | `[]` | IP/CIDR whitelist for inbound SIP |
| `allowed_numbers` | string[] | `[]` | Phone numbers allowed to call in |
| `include_headers` | string | `"x_headers"` | SIP headers to forward: `"none"`, `"x_headers"`, or `"all"` |
| `headers_to_attributes` | object | `{}` | Map SIP header → room attribute key for IVR context passthrough |

> **IVR Integration:** When `headers_to_attributes` is set, the external IVR's custom SIP
> headers (e.g., `X-Account-Number`, `X-Call-Reason`) are automatically extracted into room
> attributes and forwarded to the agent as LLM context. Set `include_headers: "all"` to
> preserve all SIP headers for debugging.

#### Update Inbound Trunk (PATCH)

```json
PATCH /api/v1/sip/trunks/inbound/ST_9crXjawUyeJp
{
  "allowed_addresses": ["203.0.113.0/24"],
  "name": "updated-trunk-name"
}
```

Only provided fields are updated. Supported: `name`, `numbers`, `allowed_addresses`,
`allowed_numbers`, `auth_username`, `auth_password`, `metadata`.

#### Response

```json
{
  "status": "success",
  "sip_trunk_id": "ST_9crXjawUyeJp",
  "name": "plivo-inbound-trunk",
  "numbers": ["+911234567890"]
}
```

---

### Dispatch Rules

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/v1/sip/dispatch-rules` | Create dispatch rule with auto-agent |
| `PATCH` | `/api/v1/sip/dispatch-rules/{rule_id}` | Partial-update dispatch rule fields |
| `GET` | `/api/v1/sip/dispatch-rules` | List all dispatch rules |
| `DELETE` | `/api/v1/sip/dispatch-rules/{rule_id}` | Delete dispatch rule |

#### Create Dispatch Rule

```json
POST /api/v1/sip/dispatch-rules
{
  "name": "plivo-inbound-rule",
  "trunk_id": "ST_9crXjawUyeJp",
  "room_prefix": "inbound_",
  "inbound_numbers": ["+911234567890", "+911234567891"],
  "prompt": "You are a healthcare assistant at MantraCare. A patient is calling you. Greet them warmly and ask how you can help.",
  "voice": "arushi",
  "model": "openai",
  "pin": "1234",
  "no_randomness": false,
  "attributes": {
    "source": "ivr",
    "department": "billing"
  }
}
```

**Required fields:** `trunk_id`
**Optional:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"inbound-agent-rule"` | Rule name |
| `room_prefix` | string | `"inbound_"` | Room name prefix |
| `inbound_numbers` | string[] | — | **DNIS routing:** only match calls to these DID numbers |
| `prompt` | string | Healthcare prompt | Agent system prompt |
| `voice` | string | `"arushi"` | TTS voice name |
| `model` | string | `"openai"` | LLM model (`openai`, `gemini`, `deepseek`) |
| `pin` | string | — | Require caller to enter this PIN before entering room |
| `no_randomness` | bool | `false` | Room name = `{prefix}{inbound_number}` (no random suffix) |
| `attributes` | object | `{}` | Additional room/participant attributes (merged with `direction: inbound`) |
| `metadata` | object/string | `{}` | Arbitrary metadata JSON |

> **DNIS Routing:** Use `inbound_numbers` to route calls from different DID numbers
> to different dispatch rules / prompts. For example, one number can route to a
> sales agent while another routes to support.

> **PIN Protection:** The `pin` field requires callers to enter a DTMF PIN before
> they are connected to the room. Useful for secure IVR handoffs.

#### What it does under the hood

- Creates a `SIPDispatchRuleInfo` with `SIPDispatchRuleIndividual` (one room per caller)
- Sets `room_prefix` so rooms are named `inbound_<random>` (or `inbound_<number>` when `no_randomness=true`)
- Injects `direction: inbound` plus any custom `attributes` into room/participant attributes
- SIP headers extracted by the inbound trunk's `headers_to_attributes` are also available as room attributes
- Sets `RoomConfiguration` with `RoomAgentDispatch` for `mantra-agent`
- The agent is auto-dispatched with metadata containing the prompt, voice, model, and any known IVR context keys

#### Update Dispatch Rule (PATCH)

```json
PATCH /api/v1/sip/dispatch-rules/SR_xxxxx
{
  "name": "updated-rule-name",
  "attributes": {
    "department": "support"
  }
}
```

Supported fields: `name`, `metadata`, `attributes`, `trunk_ids`, `rule` (room_prefix, pin, no_randomness).

---

### Test Endpoint

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/v1/test/inbound-call` | Simulate inbound by calling your phone |

#### Call Yourself (Quick Test)

```bash
curl -X POST http://localhost:8081/api/v1/test/inbound-call \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "1234567890",
    "country_code": "91",
    "trunk_id": "ST_38m9KdLjcPxW",
    "prompt": "You are a healthcare assistant at MantraCare. Greet the caller warmly and ask how you can help.",
    "voice": "arushi",
    "model": "openai"
  }'
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `phone` | ✅ | — | Your phone number |
| `country_code` | No | `91` | Country code |
| `trunk_id` | ✅ | — | **Outbound** trunk to place the call |
| `prompt` | No | Healthcare prompt | Agent instructions |
| `voice` | No | `arushi` | TTS voice |
| `model` | No | `openai` | LLM model |

---

## Agent Behavior (agent.py)

The agent detects inbound calls via metadata and adjusts its behavior:

### Detection
```python
is_inbound = payload.get("direction") == "inbound"
```

### Instructions
For inbound calls, context is added:
```
--- INBOUND CALL CONTEXT ---
- This is an INBOUND call. The caller reached out to you.
- Greet warmly and ask how you can help.
- Do not assume you know why they are calling. Let them explain.
- Identify yourself: 'MantraCare' or as instructed in your prompt.
```

### Greeting
```
Inbound:  "Greet the caller warmly. Introduce yourself and ask how you can help them today."
Outbound: "Greet the user named {client_name} and follow the opening script in your instructions."
```

### IVR Context Passthrough
For inbound calls originating from an external IVR system, the agent automatically extracts pre-screened context from the room metadata/payload (forwarded from custom SIP headers) and injects it into the prompt:

```python
ivr_keys = {"account_number", "call_reason", "department", "language", "user_id", "caller_choice"}
ivr_block = ""
for key in payload:
    if key in ivr_keys and payload[key]:
        readable = key.replace("_", " ").title()
        ivr_block += f"- {readable}: {payload[key]}\n"
if ivr_block:
    initial_instructions += "\n\n--- IVR CONTEXT (from external system) ---\n"
    initial_instructions += "This caller was pre-screened by an external IVR system:\n"
    initial_instructions += ivr_block
    if "call_reason" in payload:
        initial_instructions += "- Address their stated reason for calling directly.\n"
```

---

## Setup Checklist

- [ ] **Create inbound trunk** — `POST /api/v1/sip/trunks/inbound`
- [ ] **Create dispatch rule** — `POST /api/v1/sip/dispatch-rules` with the trunk ID
- [ ] **Configure provider** — Set Plivo/Twilio number's SIP endpoint to `sip:ST_xxx@<livekit-domain>`
- [ ] **Restart agent** if it was already running (to pick up code changes)
- [ ] **Test** by calling the number
- [ ] **Verify logs** — agent should log `Inbound call detected`

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `SIPDispatchRuleInfo has no "dispatch_rule" field` | Old server code | Restart the UI server after pulling changes |
| `object cannot be found (404)` | Using inbound trunk for outbound call | Use an outbound trunk ID for `create_sip_participant` |
| `429 Too Many Requests` (telemetry) | LiveKit Cloud rate limit | Harmless — call still works |
| Call rings but no one answers | Agent not running or dispatch rule not set | Check `mantra-agent start` is running; verify dispatch rule `trunk_ids` matches |
| Call doesn't reach LiveKit | Plivo SIP endpoint wrong | Verify format: `sip:ST_xxx@project.livekit.cloud` |

---

## File Changes

| File | Lines | What Changed |
|------|-------|-------------|
| `mantra/ui_server.py` | +380 | 8 endpoints: inbound trunks + dispatch rules CRUD, PATCH endpoints, & test endpoint |
| `mantra/agent.py` | +40 | Inbound detection, context instructions, IVR context parsing, different greeting |
| `docs/inbound.md` | — | This document (reference) |
| `docs/inbound-testing.md` | — | Step-by-step testing guide |
| `docs/test-payloads.http` | — | Curl-ready payloads (including PATCH testing) |

