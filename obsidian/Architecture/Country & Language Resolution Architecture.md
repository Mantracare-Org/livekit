---
title: "Country & Language Resolution Architecture"
type: architecture-spec
status: active
project: lkt
updated: 2026-11-18
tags:
  - architecture
  - backend
  - prisma
  - telephony
---

# Country & Language Resolution Architecture (Backend & MCP Contract)

[[00 Antigravity Hub]] | [[Features/Multi-Country & Multilingual Telephony]] | [[Architecture/MCP System Overview]]

## 1. Backend Database Schema (assist-monorepo)

```prisma
model CountryLanguageConfig {
  id               String   @id @default(uuid())
  countryCode      String   @unique // ISO 3166-1 alpha-2 (e.g., "MX", "DE", "AE")
  countryName      String
  dialPrefix       String   // E.164 prefix without '+' (e.g., "52", "49", "971")
  primaryLanguage  String   // ISO 639-1 (e.g., "es", "de", "ar", "ja")
  sttLocale        String   // Deepgram Nova-3 locale (e.g., "es-419", "de", "ar")
  cartesiaVoiceId  String   // High-quality Cartesia default voice ID
  timezone         String   // Primary IANA timezone (e.g., "America/Mexico_City")
  isActive         Boolean  @default(true)
  createdAt        DateTime @default(now())
  updatedAt        DateTime @updatedAt

  @@index([dialPrefix])
  @@index([primaryLanguage])
  @@map("country_language_configs")
}
```

## 2. API Contract

### MCP to Backend Endpoint
`GET /v1/webhooks/mcp/country-language?phone={e164_phone}&country={iso_code}`

**Response (200 OK):**
```json
{
  "success": true,
  "data": {
    "country_code": "MX",
    "country_name": "Mexico",
    "dial_prefix": "52",
    "primary_language": "es",
    "stt_locale": "es-419",
    "cartesia_voice_id": "0352ff21-c529-411a-b605-c419be28ca87",
    "timezone": "America/Mexico_City"
  }
}
```

## 3. Supported Markets (33 Countries)
- **North America:** United States (`US`), Canada (`CA`), Mexico (`MX`).
- **Western & Central Europe:** United Kingdom (`GB`), Germany (`DE`), France (`FR`), Italy (`IT`), Spain (`ES`), Netherlands (`NL`), Belgium (`BE`), Switzerland (`CH`), Austria (`AT`), Ireland (`IE`), Portugal (`PT`).
- **Northern & Eastern Europe:** Poland (`PL`), Sweden (`SE`), Norway (`NO`), Denmark (`DK`), Finland (`FI`), Czech Republic (`CZ`), Romania (`RO`).
- **Middle East:** United Arab Emirates (`AE`), Saudi Arabia (`SA`), Qatar (`QA`).
- **Asia-Pacific:** Japan (`JP`), Singapore (`SG`), Australia (`AU`), New Zealand (`NZ`), Malaysia (`MY`), Philippines (`PH`), South Korea (`KR`), Indonesia (`ID`).
- **Latin America:** Brazil (`BR`).
