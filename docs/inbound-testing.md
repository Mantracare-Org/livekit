# Inbound Call Testing Guide

## Prerequisites

- UI Server running: `http://localhost:8081`
- Agent running separately (for end-to-end call test)
- Plivo SIP trunk credentials (Auth ID + Auth Token + Phone Number)

---

## Quick Test: Call Yourself (No Plivo Setup Needed)

The fastest way to test inbound behavior — the system calls your phone and the agent treats it as an inbound call.

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

> Use an **outbound** trunk ID (not the inbound trunk). Existing outbound trunks for India: `ST_38m9KdLjcPxW` (Plivo India), `ST_fqoni9kLPkCz` (EM-Plivo), `ST_maHQuSjpJXNZ` (MC-B2C-Plivo).

You'll receive a call. Answer it — the agent will greet you as an inbound caller.

**Endpoint details:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `phone` | ✅ | Your phone number |
| `country_code` | No | Defaults to `91` (India) |
| `trunk_id` | ✅ | Outbound SIP trunk to place the call |
| `prompt` | No | Agent instructions |
| `voice` | No | TTS voice (default: `arushi`) |
| `model` | No | LLM model (default: `openai`) |

---

## Step 1: Create an Inbound SIP Trunk  ✅ DONE

Created trunk `plivo-inbound-trunk` with ID `ST_9crXjawUyeJp`.

**Endpoint:** `POST /api/v1/sip/trunks/inbound`

```json
{
  "name": "plivo-inbound-trunk",
  "numbers": ["+911234567890"],
  "auth_username": "MANTA2NJKXNKNKXX",
  "auth_password": "MjEwYjYxNTg0OTM2ZDdjNTMyYjVhY2E5YWIyY2Rj",
  "metadata": {
    "provider": "plivo"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "sip_trunk_id": "ST_9crXjawUyeJp",
  "name": "plivo-inbound-trunk",
  "numbers": ["+911234567890"]
}
```

---

## Step 2: Create a Dispatch Rule

Links the inbound trunk to the agent. When a call arrives, LiveKit auto-creates a room and dispatches `mantra-agent`.

**Endpoint:** `POST /api/v1/sip/dispatch-rules`

```json
{
  "name": "plivo-inbound-rule",
  "trunk_id": "ST_9crXjawUyeJp",
  "room_prefix": "inbound_",
  "prompt": "You are a healthcare assistant at MantraCare. A patient is calling you. Greet them warmly in English or Hindi and help them with their health-related queries.",
  "voice": "arushi",
  "model": "openai"
}
```

**Expected Response:**
```json
{
  "status": "success",
  "sip_dispatch_rule_id": "SR_xxxxx",
  "name": "plivo-inbound-rule",
  "trunk_ids": ["ST_9crXjawUyeJp"],
  "room_prefix": "inbound_"
}
```

---

## Step 3: Configure Plivo for Inbound Calls

In the **Plivo Console** (`https://console.plivo.com`):

1. Go to **Phone Numbers → Your Number**
2. Under **Voice**, set **Answer Mode** to `SIP`
3. Set **SIP Endpoint** to: `sip:ST_xxxxx@sip.livekit.cloud`
   - Replace `ST_xxxxx` with your trunk ID
   - The domain is your LiveKit Cloud SIP domain
4. Save

Alternatively, use Plivo's **Answer URL** pointing to a proxy that forwards to LiveKit's SIP inbound endpoint.

**Your trunk ID:** `ST_9crXjawUyeJp`

---

## Step 4: Verify Setup

### List inbound trunks:
```
GET /api/v1/sip/trunks/inbound
```

### List dispatch rules:
```
GET /api/v1/sip/dispatch-rules
```

### Delete (if needed):
```
DELETE /api/v1/sip/trunks/inbound/ST_xxxxx
DELETE /api/v1/sip/dispatch-rules/SR_xxxxx
```

---

## Step 5: End-to-End Test

1. Ensure the agent is running:
   ```bash
   mantra-agent start
   ```

2. Call the Plivo phone number from your mobile

3. Expected flow:
   - Plivo routes the call to LiveKit SIP
   - Dispatch rule creates room `inbound_<random>`
   - `mantra-agent` is auto-dispatched to the room
   - SIP caller (you) is bridged into the room
   - Agent greets: *"Hello, this is MantraCare. How can I help you today?"*

4. Monitor logs:
   ```
   INFO (mantra.agent): Entrypoint reached for room: inbound_xxxxx
   INFO (mantra.agent): Inbound call detected — will greet proactively
   ```

---

## Troubleshooting

| Issue | Likely Cause | Fix |
|-------|-------------|-----|
| Call not reaching LiveKit | Plivo SIP endpoint wrong | Verify SIP endpoint format: `sip:ST_xxx@sip.livekit.cloud` |
| Agent not answering | Agent not running | Start agent with `mantra-agent start` |
| Dispatch rule not triggering | Trunk ID mismatch | Verify rule's `trunk_ids` matches trunk `sip_trunk_id` |
| 403 on API calls | Proxy env vars interfering | Check `PLIVO_PROXY` — trunk/dispatch rules use direct `lk_client`, not proxy |
