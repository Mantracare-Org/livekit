# DevOps Agent

## Deployment

- Docker multi-stage build (`Dockerfile`)
- Entrypoint: `entrypoint.sh {agent|ui}`
- LiveKit Cloud project: `mantraassist-0ek43ife`

## Infrastructure

| Component | Management |
|-----------|------------|
| PostgreSQL | External (`lkdb` docker-compose) |
| Redis | External, localhost:6379 |
| S3 | AWS bucket |
| LiveKit | Cloud-managed |
| SMTP | Gmail app password |

## Docker Build

```bash
docker build -t mantra-agent .
docker run -e ... mantra-agent agent  # or "ui"
```

## Env Files

- `.env.local` — Active secrets (gitignored)
- `.env` — Template/commented (safe for repo)
