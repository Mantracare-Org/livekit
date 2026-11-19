---
title: "Multi-Country Multilingual Telephony Architecture"
type: antigravity-memory
category: "telephony"
tags:
  - antigravity/memoria
created: 2026-11-18
updated: 2026-11-18
---

# Multi-Country Multilingual Telephony Architecture

> [!NOTE]
> Automated country detection, language resolution, and engine alignment across 33+ countries for LiveKit voice agents

## Solucion Tecnica

Implemented FastMCP tool `resolve_caller_country_language` in `livekit-mcp` and calibrated `lkt` voice agent pipeline across 33+ countries.

### Inbound vs Outbound Telephony Quirks (Learned in Execution)
1. **Inbound DID vs Caller Number Discrepancy:**
   - In inbound calls, `meta_payload.get("phone_number")` is the dialed DID (trunk number, e.g. `+919484959268`), NOT the customer's phone number!
   - Guarding entrypoint resolution with `is_inbound_hint`: Never pre-resolve caller country from `phone_number` on inbound calls.
   - The actual caller number is extracted from the LiveKit SIP participant upon connect (`sip_+39800957032`).
2. **Caller Resolution Overwrite Protection:**
   - Removed `if not call_state.get("caller_country"):` guard when participants join. The participant's caller-ID is the source of truth and must always resolve country and language.
3. **Voice Overrides for Non-English Telephony:**
   - When a caller's country language is non-English (e.g. Italian `it`, Spanish `es`, German `de`), the regional Cartesia voice from the country catalog takes priority over default English voices (e.g. Arushi).
4. **India Catalog Entry:**
   - Added India (`IN`, `+91`, `en-IN`, Arushi voice `95d51f79-c397-46f9-b49a-23763d3eaa2d`, `Asia/Kolkata`) to `COUNTRY_CATALOG`.

### Proyecto: [[lkt]]

---
*Conexiones:* [[00 Antigravity Hub]] | [[00 Indice de Memoria]]
