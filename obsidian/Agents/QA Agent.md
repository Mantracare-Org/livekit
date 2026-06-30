# QA Agent

## Current State

**No automated test suite exists.** All testing is manual.

## Manual Test Checklist

### Agent
- [ ] Connect to test room via console (`POST /dispatch-test`)
- [ ] Verify STT → LLM → TTS pipeline
- [ ] Test bilingual switching (English → Hindi)
- [ ] Test `end_call` tool
- [ ] Verify 3-minute call limiter

### Webhook
- [ ] POST valid payload → receive room and token
- [ ] POST without phone → 400 error
- [ ] POST with invalid trunk → 500 error

### SIP Trunks
- [ ] Create Twilio trunk → verify in LiveKit
- [ ] Create Plivo trunk → verify with proxy
- [ ] List trunks → verify count
- [ ] Delete trunk → verify removal

### Dashboard
- [ ] Login with credentials → receive JWT
- [ ] Load metrics → verify numbers
- [ ] Active calls → verify live updates
- [ ] Call history → verify pagination

## Future Testing

- pytest for `utils.py` (recording, analysis, webhooks)
- Integration test for webhook → dispatch → agent flow
- LiveKit test rooms for automated agent testing
