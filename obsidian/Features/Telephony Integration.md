# Telephony Integration

**File:** `mantra/ui_server.py`

## Providers

| Provider | Routing | Outbound Endpoint | Notes |
|----------|---------|----------|-------|
| Twilio | Direct | `POST /v1/sip/trunks/outbound/twilio` | Default address `live-kit-mc.pstn.twilio.com` |
| Plivo | Proxied (India) | `POST /v1/sip/trunks/outbound/plivo` | On-the-fly trunk provisioning; `destination_country="in"`; Zentrunk for inbound |
| Zadarma | Direct | `POST /v1/sip/trunks/outbound/zadarma` | Backward-compatible with root endpoint |
| VoiceLink | Proxied | `POST /v1/sip/trunks/outbound/voice_link` | `destination_country="in"`; LiveKit-native provider |

## SIP Trunk Resolution

- `_get_provider_from_trunk(trunk_id)` → fetches trunk from LiveKit, infers provider from address (`twilio`/`plivo`/`zadarma`), falls back to `voicelink_client` for `voice_link`; returns `str | None` for unknown trunks
- No default provider — all providers resolved equally; non-None results cached in Redis `trunk:provider:{trunk_id}` (TTL 30d)
- Trunk ID from payload: `trunk_id` > `call_from_id` > `SIP_TRUNK_ID` env var

## Call Capacity Gating

Per-trunk concurrency limits guard dispatch (middleware, POST dispatch paths):

| Provider (per trunk) | Limit | Env override |
|----------|-------|--------------|
| Plivo | 2 | `PLIVO_MAX_CONCURRENCY` |
| Zadarma | 3 | `ZADARMA_MAX_CONCURRENCY` |
| VoiceLink | 5 | `VOICELINK_MAX_CONCURRENCY` |
| Twilio | 3 | `TWILIO_MAX_CONCURRENCY` |
| Global (agent pool) | 5 | `MAX_CONCURRENCY` |

- Trunk embedded in LiveKit room name — `call_{trunk_id}_{call_id}` (e.g. `call_ST_xxx_abc123`) — enables zero-Redis active-count via LiveKit room list; trunk→provider resolved via `_resolve_trunk_limit` (cache → LiveKit API → Redis fallback)
- Per-trunk gate (webhook path only) → empty `503` when the call's trunk is saturated; other trunks keep dispatching
- Global gate (all dispatch paths) → `503` when live `call_*` rooms ≥ `MAX_CALL_CONCURRENCY`
- Rejected calls → `call_logs` row with status `Busy`, reason `provider_at_concurrency_limit` (provider, active/max, trunk, phone)
- `/health` returns `{"healthy": false}` when any provider or the global pool is at capacity
- LiveKit errors in gate fail closed → `503` (no silent gate bypass)

## Phone Number Format

E.164 format: `+{country_code}{phone_number}`. Handles both `+`-prefixed and bare numbers.

## Error Handling

SIP failures are classified in `trigger_sip` (`ui_server.py`):
- `408`/timeout/no answer → `"No Answer"`
- `486`/busy/decline → `"Busy"`
- Other → `"Incomplete"`

The webhook awaits the SIP call and returns an empty `503` when it fails (matching the capacity gate); the room is deleted and the classification is written to Redis `sip_error_status:{call_id}` (TTL: 300s).

## Inbound Setup

`POST /v1/sip/inbound/setup` handles end-to-end provisioning:
1. Check for existing trunk/rule (Plivo: 409 + Zentrunk link verification)
2. Create/reuse LiveKit inbound trunk
3. Create/reuse LiveKit dispatch rule
4. Generate SIP URI from SIP domain
5. Configure provider forwarding (Zadarma API HMAC-SHA1, Twilio REST, Plivo Zentrunk, VoiceLink placement)
6. Store config in `org_configs` (only after provider forwarding succeeds)
