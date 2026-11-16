---
title: "Skill: prod-deployment"
type: antigravity-skill
scope: project
project: "assist-ai"
workspace_dir: "/home/fardeen/assist-ai"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: prod-deployment (Proyecto: assist-ai)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `prod-deployment`
> - **Proyecto**: `assist-ai`
> - **Carpeta Local**: `/home/fardeen/assist-ai`
> - **Archivo Fuente**: `/home/fardeen/assist-ai/.agents/skills/prod-deployment/SKILL.md`
> - **Descripción**: Production deployment guide for Docker Compose, container ports, EC2 environment (54.164.161.207), bunker registry, and environment variables.

## Instrucciones del Agente

# Production Deployment & Operations Skill

Use this skill whenever you prepare, build, configure, deploy, or debug production containers for `assist-ai` (API, AI Receptionist Kiosk UI, PostgreSQL, Redis) on the EC2 production instance.

---

## 1. Production Host Environment

- **Host IP**: `54.164.161.207` (AWS EC2, Private IP: `172.31.14.128`)
- **Container Registry**: `bunker.mantracare.com/mantra-ai/`
- **Host Context**: The EC2 instance hosts existing production systems (`dograh-*` containers, `nginx-proxy-manager`).

### ⚠️ Strict Host Port Reservations
**NEVER allocate or bind these host ports in any `assist-ai` compose file:**
- `8000`: Occupied by `dograh-api-1`
- `5432`: Occupied by `dograh-postgres-1`
- `6379`: Occupied by `dograh-redis-1`
- `6380`: Occupied by `redis`
- `5444`: Occupied by `lk_postgres_logs`
- `80`, `81`, `443`: Occupied by `nginx-proxy-manager`

### Allocated Host Ports for Assist AI
- **`8008` ➡️ `8000`**: Assist AI API backend (`assist_ai_api`)
- **`3003` ➡️ `3000`**: AI Receptionist Next.js Kiosk UI (`assist_ai_reception_ui`)
- **`5440` ➡️ `5432`**: Isolated PostgreSQL + pgvector (`assist_ai_postgres`)
- **`6390` ➡️ `6379`**: Isolated Redis Cache & Queue (`assist_ai_redis`)

---

## 2. Docker Compose Architecture (`docker-compose.yml`)

The authoritative production compose specification runs 4 coordinated services within the default bridge network on EC2 host `54.164.161.207` (`ip-172-31-14-128`).
Reference file saved at: [references/docker-compose.prod.yml](file:///home/lakshay/assist-ai/.agents/skills/prod-deployment/references/docker-compose.prod.yml)

Secrets must never appear inline. Follow `secrets-hygiene`. Authoritative compose uses `${VAR}` interpolation backed by the host's `/home/admin/assist-ai/.env`:

```yaml
services:
  # ============================================================================
  # 1. Assist AI Unified Backend (FastAPI + Reception + JEV)
  # ============================================================================
  assist-ai-api:
    image: bunker.mantracare.com/mantra-ai/assist-ai-api:1.1.4
    container_name: assist_ai_api
    restart: unless-stopped
    env_file:
      - path: .env
        required: false
    ports:
      - "8008:8000"
    environment:
      # Network & Server
      - HOST=0.0.0.0
      - PORT=8000
      - ENVIRONMENT=production
      - CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:3003,http://127.0.0.1:3003,http://localhost:3000,https://reception.mantraassist.com}
      - DATABASE_URL=${DATABASE_URL:-postgresql+asyncpg://postgres:password@postgres:5432/assist_ai}
      - SYNC_DATABASE_URL=${SYNC_DATABASE_URL:-postgresql://postgres:password@postgres:5432/assist_ai}
      - REDIS_URL=${REDIS_URL:-redis://redis:6379/0}
      - OTEL_SDK_DISABLED=${OTEL_SDK_DISABLED:-true}
      - OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-assist-ai}
      - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-}

      # Security & JWT Secrets
      - SECRET_KEY=${SECRET_KEY}
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - JWT_ACCESS_SECRET=${JWT_ACCESS_SECRET}
      - JWT_ALGORITHM=${JWT_ALGORITHM:-HS256}
      - MA_SERVICE_SECRET=${MA_SERVICE_SECRET}
      - HMAC_SECRET=${HMAC_SECRET}

      # Mantra Assist API Integration
      - ASSIST_ENABLED=${ASSIST_ENABLED:-true}
      - ASSIST_API_URL=${ASSIST_API_URL:-http://host.docker.internal:5500/v1}
      - ASSIST_API_TOKEN=${ASSIST_API_TOKEN}
      - ASSIST_ORG_ID=${ASSIST_ORG_ID:-2}
      - MA_BACKEND_URL=${MA_BACKEND_URL:-http://host.docker.internal:5500}
      - MA_BACKEND_TIMEOUT=${MA_BACKEND_TIMEOUT:-15}
      - RECEPTION_LOCAL_FALLBACK=${RECEPTION_LOCAL_FALLBACK:-false}

      # LLM Providers & API Keys
      - JEV_AI_API_KEY=${JEV_AI_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o-mini}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - DEFAULT_MODEL=${DEFAULT_MODEL:-openai/gpt-4o-mini}
      - ASSEMBLYAI_API_KEY=${ASSEMBLYAI_API_KEY}

      # Voice & Audio
      - STT_PROVIDER=${STT_PROVIDER:-openai}
      - TTS_PROVIDER=${TTS_PROVIDER:-openai}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY:-}

      # Reception & OTP Configuration
      - OTP_PROVIDER=${OTP_PROVIDER:-msg91}
      - OTP_EXPIRY_MINUTES=${OTP_EXPIRY_MINUTES:-5}
      - OTP_MAX_ATTEMPTS=${OTP_MAX_ATTEMPTS:-5}
      - OTP_RESEND_SECONDS=${OTP_RESEND_SECONDS:-30}
      - VISITOR_SESSION_EXPIRE_MINUTES=${VISITOR_SESSION_EXPIRE_MINUTES:-30}
      - DEFAULT_LOCALE=${DEFAULT_LOCALE:-en-IN}
      - DEFAULT_TIMEZONE=${DEFAULT_TIMEZONE:-Asia/Kolkata}
      - OFFLINE_CACHE_TTL_SECONDS=${OFFLINE_CACHE_TTL_SECONDS:-86400}

      # Face Recognition
      - FACE_MATCH_THRESHOLD=${FACE_MATCH_THRESHOLD:-0.65}
      - DETECTOR_BACKEND=${DETECTOR_BACKEND:-opencv}
      - FACE_MODEL_NAME=${FACE_MODEL_NAME:-Facenet512}

      # Twilio (SMS / Verification / Telephony)
      - TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID:-}
      - TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN:-}
      - TWILIO_PHONE_NUMBER=${TWILIO_PHONE_NUMBER:-}
      - TWILIO_VERIFY_SERVICE_SID=${TWILIO_VERIFY_SERVICE_SID:-}

      # Interakt WhatsApp
      - INTERAKT_API_KEY=${INTERAKT_API_KEY:-}
      - INTERAKT_API_URL=${INTERAKT_API_URL:-https://api.interakt.ai/v1/public/message/}
      - INTERAKT_OTP_TEMPLATE_NAME=${INTERAKT_OTP_TEMPLATE_NAME:-mantracare_otp_dn}

      # MSG91
      - MSG91_AUTH_KEY=${MSG91_AUTH_KEY:-}
      - MSG91_TEMPLATE_ID=${MSG91_TEMPLATE_ID:-}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test:
        - CMD-SHELL
        - curl -f http://localhost:8000/health || exit 1
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 35s
    extra_hosts:
      - host.docker.internal:host-gateway

  # ============================================================================
  # 2. AI Receptionist Kiosk Frontend (Next.js 16 Standalone)
  # ============================================================================
  reception-ui:
    image: bunker.mantracare.com/mantra-ai/reception-ui:1.0.0
    container_name: assist_ai_reception_ui
    restart: unless-stopped
    ports:
      - "3003:3000"
    environment:
      - NODE_ENV=production
      - BACKEND_URL=http://assist-ai-api:8000
      - NEXT_PUBLIC_BACKEND_URL=http://assist-ai-api:8000
    depends_on:
      assist-ai-api:
        condition: service_healthy

  # ============================================================================
  # 3. Isolated PostgreSQL + pgvector (For Embeddings & Session Store)
  # ============================================================================
  postgres:
    image: pgvector/pgvector:pg16
    container_name: assist_ai_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-password}
      POSTGRES_DB: ${POSTGRES_DB:-assist_ai}
    ports:
      - "127.0.0.1:5440:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test:
        - CMD-SHELL
        - pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB
      interval: 5s
      timeout: 5s
      retries: 5

  # ============================================================================
  # 4. Isolated Redis (For State Caching & Queue)
  # ============================================================================
  redis:
    image: redis:7-alpine
    container_name: assist_ai_redis
    restart: unless-stopped
    ports:
      - "127.0.0.1:6390:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test:
        - CMD
        - redis-cli
        - ping
      interval: 5s
      timeout: 5s
      retries: 5

# ==============================================================================
# Persistent Storage Volumes
# ==============================================================================
volumes:
  postgres_data:
  redis_data:
```

---

## 3. Production Environment Variables Contract (`.env`)

Every deployment must supply a `.env` file in `~/assist-ai/` on the host. Never commit actual secret keys or API tokens.
Reference template saved at: [references/.env.prod.template](file:///home/lakshay/assist-ai/.agents/skills/prod-deployment/references/.env.prod.template)

```env
# 1. Server & Network
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=production
CORS_ORIGINS=http://localhost:3003,http://127.0.0.1:3003,http://localhost:3000,https://reception.mantraassist.com

# 2. Database & Redis (Docker internal container network addresses)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<strong_postgres_password>
POSTGRES_DB=assist_ai
DATABASE_URL=postgresql+asyncpg://postgres:<strong_postgres_password>@postgres:5432/assist_ai
SYNC_DATABASE_URL=postgresql://postgres:<strong_postgres_password>@postgres:5432/assist_ai
REDIS_URL=redis://redis:6379/0

# 3. Security & JWT Secrets
SECRET_KEY=<random_32_chars>
JWT_SECRET_KEY=<random_32_chars>
JWT_ACCESS_SECRET=<random_32_chars>
JWT_ALGORITHM=HS256
MA_SERVICE_SECRET=<ma_internal_shared_secret>
HMAC_SECRET=<webhook_verification_secret>

# 4. Mantra Assist Integration
ASSIST_ENABLED=true
ASSIST_API_URL=http://host.docker.internal:5500/v1
ASSIST_API_TOKEN=
ASSIST_ORG_ID=2
MA_BACKEND_URL=http://host.docker.internal:5500
MA_BACKEND_TIMEOUT=15
RECEPTION_LOCAL_FALLBACK=false

# 5. LLM Providers & API Keys
JEV_AI_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
DEFAULT_MODEL=openai/gpt-4o-mini
ASSEMBLYAI_API_KEY=

# 6. Voice & Audio
STT_PROVIDER=openai
TTS_PROVIDER=openai
ELEVENLABS_API_KEY=

# 7. Reception & OTP Configuration
OTP_PROVIDER=msg91
OTP_EXPIRY_MINUTES=5
OTP_MAX_ATTEMPTS=5
OTP_RESEND_SECONDS=30
VISITOR_SESSION_EXPIRE_MINUTES=30
DEFAULT_LOCALE=en-IN
DEFAULT_TIMEZONE=Asia/Kolkata
OFFLINE_CACHE_TTL_SECONDS=86400

# 8. Face Recognition
FACE_MATCH_THRESHOLD=0.65
DETECTOR_BACKEND=opencv
FACE_MODEL_NAME=Facenet512

# 9. SMS & Messaging Providers
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_VERIFY_SERVICE_SID=
INTERAKT_API_KEY=
INTERAKT_API_URL=https://api.interakt.ai/v1/public/message/
INTERAKT_OTP_TEMPLATE_NAME=mantracare_otp_dn
MSG91_AUTH_KEY=
MSG91_TEMPLATE_ID=

# 10. OpenTelemetry (Disabled in prod by default to suppress log noise)
OTEL_SDK_DISABLED=true
OTEL_SERVICE_NAME=assist-ai
OTEL_EXPORTER_OTLP_ENDPOINT=

# 11. Next.js Reception UI
NODE_ENV=production
BACKEND_URL=http://assist-ai-api:8000
NEXT_PUBLIC_BACKEND_URL=http://assist-ai-api:8000
```

---

## 4. Build & Push Protocols

### 4.1 Building Backend Image
Always run tests before building:
```bash
uv run pytest
docker build -t bunker.mantracare.com/mantra-ai/assist-ai-api:<tag> -f backend/Dockerfile .
docker push bunker.mantracare.com/mantra-ai/assist-ai-api:<tag>
```

### 4.2 Building AI Receptionist UI Image
```bash
cd ui/reception
npm run build # local validation
docker build -f Dockerfile -t bunker.mantracare.com/mantra-ai/reception-ui:<tag> .
docker push bunker.mantracare.com/mantra-ai/reception-ui:<tag>
```

---

## 5. Deployment Commands on EC2 Host

```bash
# 1. SSH into production host
ssh admin@54.164.161.207
cd ~/assist-ai

# 2. Pull code and images
git pull origin main
docker compose pull

# 3. Start or update containers
docker compose up -d

# 4. Inspect container health & logs
docker compose ps
docker compose logs -f assist-ai-api
docker compose logs -f reception-ui

# 5. Run health check
curl http://localhost:8008/health
curl -I http://localhost:3003
```

---

## 6. Critical Operational Gotchas

1. **Next.js Standalone Rewrites**:
   The frontend runs inside Docker. For server-side proxy rewrites to work (`/api/v1/*` and `/detect-face`), `BACKEND_URL` must point to `http://assist-ai-api:8000`, NOT `http://localhost:8000` (which would hit the Next.js container itself).
2. **OpenCV System Libraries in Backend**:
   The base `python:3.12-slim` image requires native packages (`libgl1`, `libxcb1`, `libglib2.0-0`, `libxext6`, `libxrender1`, `libsm6`). Never remove these from `backend/Dockerfile`.
3. **Database Lifespan Auto-Migrations**:
   When `assist-ai-api` boots with a reachable PostgreSQL database, it automatically ensures `CREATE EXTENSION IF NOT EXISTS vector` and invokes `Base.metadata.create_all(bind=engine)`. If Postgres is down, JEV evaluation continues to work statelessly, but reception endpoints will fail until Postgres is healthy.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]
