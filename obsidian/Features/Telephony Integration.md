# Telephony Integration

**File:** `mantra/ui_server.py`

## Providers

| Provider | Routing | Endpoint | Notes |
|----------|---------|----------|-------|
| Twilio | Direct | `POST /api/v1/sip/trunks/outbound/twilio` | Default address `live-kit-mc.pstn.twilio.com` |
| Plivo | Proxied (India) | `POST /api/v1/sip/trunks/outbound/plivo` | On-the-fly trunk provisioning; `destination_country="in"` |
| Zadarma | Direct | `POST /api/v1/sip/trunks/outbound/zadarma` | Backward-compatible with root endpoint |

## SIP Trunk Resolution

- `_get_provider_from_trunk(trunk_id)` → fetches trunk from LiveKit, infers provider from address
- Default provider: `zadarma`
- Trunk ID from payload: `trunk_id` > `call_from_id` > `SIP_TRUNK_ID` env var

## Phone Number Format

E.164 format: `+{country_code}{phone_number}`. Handles both `+`-prefixed and bare numbers.

## Error Handling

SIP failures are classified:
- `408`/timeout/no answer → `"No Answer"`
- `486`/busy/decline → `"Busy"`
- Other → `"Incomplete"`

Status written to Redis `sip_error_status:{call_id}` (TTL: 300s) for agent to read during post-call.

On SIP failure, the call log is also saved to PostgreSQL **immediately** via `save_call_log_to_db()` so the dashboard reflects it without waiting for the agent's post-call pipeline.
