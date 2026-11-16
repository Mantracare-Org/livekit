---
skill: reception-launch
version: 1.0
last_verified: 2026-11-13
triggers: ["reception-launch","/project"]
---

## Procedimiento Operativo
del Agente

# Reception Launch & Operations Guide

## Overview

The AI Receptionist module in `assist-ai` serves clinic walk-ins, scheduled appointments, facial check-ins, and token queue management. It connects `ui/reception` (Next.js 16) with the FastAPI backend (`backend/app/api/reception.py`) and upstream EHR `assist-monorepo` (`apps/api`).

---

## 1. Zero-Fabrication Rules

1. **No Fake Doctors or Slots**: Never fall back to mock consultants (e.g. Dr. Sharma / Dr. Patel) or synthetic 30-minute intervals. If MA has no available slots, return `[]`. If MA returns no active doctors and local DB is empty, return HTTP `502 Bad Gateway`.
2. **No Invented "Now Serving" Math**: In `TokenAndCurrentServingCard.tsx`, never invent queue arithmetic (`tokenNumber - 4`) or hardcode sample phone numbers (`+91 91234 56780`).
3. **No Fabricated Journeys**: `/reception/journey/template` returns HTTP `501 Not Implemented` until dynamic journey stages are attached to patient active processes in MA.
4. **Payment Gating**: Patient billing plumbing is wired (`/reception/patients/:id/invoices` and `/reception/invoices/:id/pay`), but returns HTTP `501` (`error_code: "ma_endpoint_pending"`). The kiosk UI gates payment behind `features.paymentGatewayEnabled: false`.

---

## 2. Syncing Prototype Changes from `ma-figma`

Whenever prototype components from `ma-figma` need to be synced:

```bash
cd ui/reception
npm run sync
```

The script `ui/reception/scripts/sync-receptionist.mjs` safely mirrors assets from `/home/lakshay/ma-figma` while protecting:
- `src/reception/types/reception.ts`
- `src/reception/avatar/AvatarReceptionView.tsx`
- `src/reception/avatar/components/TokenAndCurrentServingCard.tsx`
- `src/reception/lib/api/httpMaClient.ts`
- `src/reception/lib/api/maClient.ts`
- `src/reception/lib/api/generated`

---

## 3. Automated Verification & Testing

### Python Backend & Contract Tests
Run all 38 contract and integration tests:

```bash
uv run pytest backend/tests/reception/
```

### Next.js Frontend Verification
Verify TypeScript compilation and production build:

```bash
cd ui/reception
npx tsc --noEmit
npm run build
```

### Live Smoke Test Script
Verify live or mock MA endpoints end-to-end:

```bash
./scripts/live_ma_smoke.sh
```

---

## 4. Upstream MA Blocker Checklist

Before going live with production EHR, verify that the following items are shipped in `assist-monorepo` (`pp/feature/ai-integrate`):
1. [ ] **P0-1**: Move `/auth/login` and `/auth/verify-otp` to a public unauthenticated sub-router with rate limiting.
2. [ ] **P0-2**: Mount `POST /walkin` (and `/booking/doctors` alias) in `assist-ai.routes.ts` (reverted after commit `4fbb34c4`).
3. [ ] **P0-3**: Allow walk-ins without required `process_id`/`stage_id` (fallback to org default onboarding process).
4. [ ] **P0-4**: Advance client stage to `Waiting` upon appointment check-in.
5. [ ] **P0-5**: Mount kiosk-accessible `GET /reception/patients/:id/invoices` and `POST /reception/invoices/:id/pay`.

Detailed specifications are maintained in [`docs/MA_Reception_Handoff.md`](file:///home/lakshay/assist-ai/docs/MA_Reception_Handoff.md).

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]

## Errores Conocidos y Corrección (Aprendidos en Ejecución)
- Sin incidencias iniciales registradas en ejecución.
