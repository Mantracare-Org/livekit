# External Services

## LiveKit Cloud
- **Purpose:** WebRTC infrastructure + SIP trunking
- **Project:** `mantraassist-0ek43ife`
- **Agent ID:** `CA_duZ3ZGAvJvRr`
- **Auth:** API key + secret in `.env.local`
- **Docs:** https://docs.livekit.io

## Deepgram
- **Purpose:** Speech-to-Text (Nova-3)
- **Language:** `hi` (Hindi — better for Hinglish)
- **Auth:** API key in `.env.local`

## OpenAI
- **Purpose:** LLM (GPT-4o-mini)
- **Auth:** API key in `.env.local`

## Google AI
- **Purpose:** LLM (Gemini 2.5 Flash)
- **Auth:** API key in `.env.local`

## DeepSeek
- **Purpose:** LLM (DeepSeek v4 Flash)
- **Endpoint:** `https://api.deepseek.com`
- **Auth:** API key in `.env.local`

## Cartesia
- **Purpose:** TTS (Sonic-3)
- **Voices:** 8 configured in `VOICE_MAPPING`
- **Auth:** Up to 3 API keys for rate limit fallback

## Telephony Providers
- **Twilio** — US/primary SIP trunk
- **Plivo** — India routing (proxied)
- **Zadarma** — Default/fallback

## AWS
- **S3** — Recording storage
- **SNS** — Notifications (optional)

## MantraAssist Backend
- **Purpose:** CRM/webhook target for post-call data
- **Auth:** HMAC-SHA256 signing with shared secret

## PostgreSQL
- **Purpose:** Call log persistence
- **Managed:** External `lkdb` docker-compose
- **Default:** Localhost:5433 / Container:5432

## Redis
- **Purpose:** Queue, state, capacity tracking
- **Default:** Localhost:6379
