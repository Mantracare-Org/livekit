---
skill: assist-api-integration
version: 1.0
last_verified: 2026-11-13
triggers: ["assist-api-integration","/project"]
---

## Procedimiento Operativo
del Agente

# Assist API Integration Skill

Use this skill whenever you write or modify agent tools, background sync services, or backend endpoints that interact with `assist-monorepo/apps/api`.

## 1. Overview & Single Source of Truth
Mantra Assist is the authoritative **Single Source of Truth** for:
- Clinical EHR entities (leads, clients, care processes, doctor calendars, slots, service pricing).
- Clinic organization authentication (`POST /auth/login` and `POST /auth/verify` for terminal provisioning).

All interactions with Mantra Assist must use `MABackendClient` from `backend.app.shared.assist_client`. Never query the Assist PostgreSQL database directly.

> **Important Boundary**:  
> Mantra Assist's internal auth OTP service for clinic staff is separate and internal to them. Assist-AI does NOT connect to or rely on Mantra Assist for patient check-in OTPs; Assist-AI operates its own dedicated patient OTP delivery engine locally (MSG91 primary for India, Interakt, Twilio SMS).

## 2. Authentication Modes
- **Forwarded User JWT (Interactive Copilot & Streaming Chatbot)**:
  Pass the authenticated user's bearer token. Assist API will validate permissions (RBAC) and tenant context:
  ```python
  from backend.app.shared.assist_client.client import MABackendClient

  client = MABackendClient()
  slots = await client.get_available_slots(
      user_token=user_jwt,
      user_id=provider_id,
      date="2026-09-25",
      org_id=org_id,
  )
  ```
- **Service-to-Service API Token (Terminal Provisioning & Background Sync)**:
  Used when a kiosk machine or background worker queries organization metadata without a user JWT:
  ```python
  client = MABackendClient(api_token=settings.ASSIST_API_TOKEN, org_id=org_id)
  doctors = await client.list_staff(org_id=org_id)
  ```
- **Terminal Admin Authentication**:
  Used to onboard and provision physical kiosks:
  ```python
  # Initial login trigger
  res = await client.auth_login(email="admin@clinic.com", password="password")
  # MFA OTP verification
  verify_res = await client.auth_verify_otp(email="admin@clinic.com", otp="123456")
  ```
- **Webhook HMAC-SHA256 Signatures**:
  Machine-to-machine webhooks from Mantra Assist (`POST /webhook/assist`) are verified using HMAC-SHA256 (`HMAC_SECRET`) with `x-mantra-signature` and `x-mantra-timestamp` (< 300s replay window).

## 3. Core Methods
- `auth_login(email, password)`: Clinic admin authentication.
- `auth_verify_otp(email, otp)`: MFA verification & terminal provisioning.
- `get_setup_config(org_id)`: Clinic branding, business hours, and kiosk features.
- `lookup_patient(phone, client_id, org_id)`: Composite patient profile and today's appointments.
- `walkin_checkin(data, org_id)`: Quick lead creation and queue ticket assignment.
- `checkin_appointment(appointment_id, queue_token, org_id)`: Advance stage and log timeline.
- `get_available_slots(user_id, date, org_id)`: Doctor schedule availability.
- `book_appointment(data, org_id)`: Appointment booking.
- `list_doctors(org_id, location_id)`: Practitioner directory.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]

## Errores Conocidos y Corrección (Aprendidos en Ejecución)
- Sin incidencias iniciales registradas en ejecución.
