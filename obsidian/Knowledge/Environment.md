# Environment

## Required Variables

### LiveKit
```
LIVEKIT_URL=wss://mantraassist-0ek43ife.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
SIP_TRUNK_ID=
LIVEKIT_SIP_DOMAIN=    # Optional: override auto-detected SIP domain
```

### AI Services
```
DEEPGRAM_API_KEY=       # STT (Nova-3, language=multi)
OPENAI_API_KEY=         # LLM (GPT-4o-mini)
GOOGLE_API_KEY=         # LLM (Gemini 2.5 Flash)
DEEPSEEK_API_KEY=       # LLM (DeepSeek v4 Flash)
CARTESIA_API_KEY=       # TTS — ONLY required on the self-hosted path (direct livekit-plugins-cartesia in engines.py)
```

**Note:** LiveKit Cloud inferencing (native `sonic-3`) needs **no** Cartesia key. **Self-hosted** LiveKit (`ws://localhost:7880`) cannot authenticate to the cloud inference gateway (HTTP 401 → silent calls), so `engines.py` builds a direct `livekit-plugins-cartesia` `cartesia.TTS` and requires a **valid** `CARTESIA_API_KEY` (invalid keys return 401 from `api.cartesia.ai`).

### Database
```
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=call_logs_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
DATABASE_URL=           # Alternative to individual vars (preferred if set)
```

### Redis
```
REDIS_URL=redis://localhost:6379/0
```

### AWS (S3)
```
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_S3_BUCKET_NAME=
AWS_REGION=ap-south-1
```

### Auth
```
JWT_SECRET=
ADMIN_USERNAME_HASH=<sha256 of username>
ADMIN_PASSWORD_HASH=<sha256 of password>
```

### SMTP (Crash Alerts)
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
ALERT_EMAIL_IDS=
ADMIN_MAIL_ID=
```

### Backend Webhook
```
MANTRAASSIST_BACKEND_URL=
MANTRAASSIST_WEBHOOK_SECRET=    # HMAC-SHA256 signing key
```

### TOS Telemetry
```
TOS_ENDPOINT=
TOS_SERVICE_SECRET=
```

### Capacity
```
MAX_CONCURRENCY=5              # Global max calls (fallback: CARTESIA_MAX_CONCURRENCY)
PLIVO_MAX_CONCURRENCY=2        # Per-provider override
ZADARMA_MAX_CONCURRENCY=3
VOICELINK_MAX_CONCURRENCY=5
TWILIO_MAX_CONCURRENCY=2
LIVEKIT_MAX_ROOMS=20
AGENT_MAX_WORKERS=20
```

### Provider Credentials
```
# Plivo
PLIVO_AUTH_ID=
PLIVO_AUTH_TOKEN=
PLIVO_PROXY=               # HTTP proxy URL for Plivo API calls

# Zadarma
ZADARMA_API_KEY=           # Preferred (was ZADARMA_KEY)
ZADARMA_API_SECRET=        # Preferred (was ZADARMA_SECRET)

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=

# VoiceLink
VOICELINK_PROXY=           # Optional: proxy for VoiceLink API calls
```

### Transfer/Handoff (when enabled)
```
TRANSFER_NUMBERS=          # JSON: {"refund": "+911234567890", ...}
TRANSFER_DEFAULT_NUMBER=
TRANSFER_SIP_TRUNK_ID=
```

### Health
```
BYPASS_HEALTH_CHECKS=     # Set to "1" to skip all health checks
```

## File Locations

- `.env.local` — Active secrets (gitignored)
- `.env` — Template (safe to commit, all values commented out)
