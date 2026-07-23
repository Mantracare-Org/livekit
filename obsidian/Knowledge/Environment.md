# Environment

## Required Variables

### LiveKit
```
LIVEKIT_URL=wss://mantraassist-0ek43ife.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
SIP_TRUNK_ID=
```

### AI Services
```
DEEPGRAM_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
DEEPSEEK_API_KEY=
CARTESIA_API_KEY=
CARTESIA_API_KEY_2=        # Optional: fallback
CARTESIA_API_KEY_3=        # Optional: fallback
```

### Database
```
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=call_logs_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
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
MANTRAASSIST_WEBHOOK_SECRET=
```

### Capacity
```
CARTESIA_MAX_CONCURRENCY=20
LIVEKIT_MAX_ROOMS=20
AGENT_MAX_WORKERS=20
```

## File Locations

- `.env.local` — Active secrets (gitignored)
- `.env` — Template (safe to commit, all values commented out)
