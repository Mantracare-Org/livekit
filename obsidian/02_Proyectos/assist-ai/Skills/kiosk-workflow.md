---
title: "Skill: kiosk-workflow"
type: antigravity-skill
scope: project
project: "assist-ai"
workspace_dir: "/home/fardeen/assist-ai"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: kiosk-workflow (Proyecto: assist-ai)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `kiosk-workflow`
> - **Proyecto**: `assist-ai`
> - **Carpeta Local**: `/home/fardeen/assist-ai`
> - **Archivo Fuente**: `/home/fardeen/assist-ai/.agents/skills/kiosk-workflow/SKILL.md`
> - **Descripción**: Complete guide for the AI Receptionist Kiosk check-in flow, visitor tokens, face recognition, and speech loop.

## Instrucciones del Agente

# Kiosk & Reception Workflow Skill

Use this skill when modifying, debugging, or extending the AI Receptionist backend (`backend/app/api/reception.py`) or Kiosk frontends (`ui/reception`).

## 1. Architectural Boundary
- **Mantra Assist (`assist-monorepo`)**: Single source of truth for EHR domain data (patient records/leads, care processes, doctor directories, slots, services) and initial terminal admin auth (`POST /auth/login` + `POST /auth/verify`). Mantra Assist maintains its own separate internal auth OTP service for clinic staff.
- **Assist-AI (`assist-ai`)**: Manages all local edge kiosk operations:
  - **Patient Phone Check-In OTP**: Managed 100% locally in Assist-AI via MSG91 (primary for India), Interakt (WhatsApp), Twilio (SMS), or demo fallback. (Completely decoupled from Mantra Assist).
  - **Biometric Face Recognition**: Local camera frames, Facenet512 vector embeddings, and cosine similarity in PostgreSQL `pgvector`. Raw biometric images are never transmitted to the cloud.
  - **Conversational Speech Loop**: Whisper STT, LangGraph reasoning agent, OpenAI / ElevenLabs TTS.
  - **Queue Tokens & Displays**: Sequential ticket generation (`W-848`) and live queue broadcasting over WebSockets (`WS /api/v1/kiosk/queue/stream`).

## 2. API Router Standardization: Canonical `/reception/*`
All front-desk endpoints are canonically defined in `backend/app/api/reception.py` with `/reception/*` prefix (and backward-compatibility `/kiosk/*` aliases via `backend/app/api/kiosk.py`):
- `POST /reception/auth/login`: Admin credentials check against MA Cloud; triggers MFA.
- `POST /reception/auth/verify-otp`: Validates MFA with MA; stores active org session in local `reception_settings`.
- `GET /reception/auth/status`: Verifies terminal provisioning status.
- `GET /reception/setup-config`: Retrieves clinic branding, business hours, and enabled features.
- `GET /reception/patients/lookup`: Composite phone/ID lookup returning patient, care stage, and today's visits.
- `POST /reception/walkin`: Fast walk-in registration, lead creation, and token generation.
- `POST /reception/check-in`: Arrival check-in for pre-scheduled appointments.
- `POST /reception/book`: Self-service appointment scheduling.
- `GET /reception/available-slots`: Open doctor appointment slots.
- `POST /reception/tickets/complete`: Closes completed consultation tickets.

## 3. Patient Check-In & Arrival Flows
1. **Face Recognition Check-In**:
   - Camera streams frames to `/api/v1/detect-face` or `/reception/face-search`.
   - On match (`distance < 0.65`), patient is identified by `client_id` and welcomed by name.
2. **Phone Number / Walk-In Triage**:
   - Patient enters phone number.
   - If phone verification required, local OTP is dispatched via `POST /v1/kiosk/otp/send` (MSG91/Interakt) and confirmed via `POST /v1/kiosk/otp/verify`.
   - Walk-in is registered via `POST /reception/walkin`; existing appointments checked in via `POST /reception/check-in`.
3. **Queue & Token Issuance**:
   - Token ticket (e.g. `W-848`) issued and displayed with directions to triage or waiting room.
   - Stage updated in Mantra Assist timeline (`client_activities`).

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]
