# Debugging

## Common Issues

### Agent not connecting to room
- Check LiveKit API key/secret in `.env.local`
- Verify `LIVEKIT_URL` is reachable
- Check agent dispatch in LiveKit dashboard

### SIP call failing
- Verify trunk ID is valid
- Check provider-specific routing (Plivo needs proxy)
- Look for `sip_error_status` in Redis
- Agent will report Busy/No Answer in post-call

### No audio / TTS issues
- Cloud: TTS is LiveKit native `sonic-3` — no Cartesia API key needed (LiveKit inference gateway)
- Self-hosted (`ws://localhost:7880`): `inference.TTS` 401s against the cloud gateway — `engines.py` uses the direct `livekit-plugins-cartesia` plugin; requires a valid `CARTESIA_API_KEY` (test with `POST https://api.cartesia.ai/tts/bytes`)
- Verify voice ID is valid in `VOICE_MAPPING`
- Check voice_speed is within 0.1–2.0 range
- Verify the TTS provider (cloud inference or Cartesia direct) is reachable

### Redis connection issues
- Verify `REDIS_URL` in `.env.local`
- Check if Redis is running (`redis.ping()` in logs)

### Webhook not reaching backend
- Check `MANTRAASSIST_BACKEND_URL` and `MANTRAASSIST_WEBHOOK_SECRET`
- Verify HMAC signature generation matches server expectation
- Check webhook delivery logs in agent output

## Logs

- Agent: colored terminal output with `mantra.agent` logger
- UI Server: `mantra.ui_server` logger with timing info
- Dispatcher: `mantra.dispatcher` logger
- All logs: ISO timestamps, PID, process type (Main Worker / Inference Subprocess)

## Redis Inspection

```bash
# Connect to Redis
redis-cli

# Check queue
ZCARD queue:pending

# Check active calls
HGETALL calls:active

# Check call status
GET calls:status:{call_id}

# Check SIP errors
GET sip_error_status:{call_id}
```
