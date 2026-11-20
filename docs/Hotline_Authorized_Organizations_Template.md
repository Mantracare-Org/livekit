# Authorized Hotline Organizations

This document serves as the ground-truth authorization directory for the restricted corporate hotline. Only employees, members, and representatives of the organizations listed below are authorized to access this hotline.

---

## 1. Acme Corporation
- **Official Registered Name:** Acme Corporation
- **Recognized Aliases & Phonetic Variations:** Acme, Acme Corp, Acme Health, Acme Logistics, Akme
- **Status:** Active / Authorized
- **Service Tier:** Enterprise Support
- **Notes:** Authorized for technical assistance, employee care services, and benefits inquiries.

## 2. MantraCare Global
- **Official Registered Name:** MantraCare Global
- **Recognized Aliases & Phonetic Variations:** MantraCare, Mantra, Mantra Care India, Mantra Health
- **Status:** Active / Authorized
- **Service Tier:** Internal Corporate Desk
- **Notes:** Full access to internal administrative, medical, and executive support.

## 3. Nexus Technologies
- **Official Registered Name:** Nexus Technologies
- **Recognized Aliases & Phonetic Variations:** Nexus, Nexus Tech, Nexus Solutions, Nexus Corp
- **Status:** Active / Authorized
- **Service Tier:** Standard Hotline
- **Notes:** Authorized for standard corporate desk support.

---

## Gatekeeper Operating Instructions for AI Agent

1. **Authorization Match:**
   - If the caller states any of the official registered names or recognized aliases above, evaluate as **AUTHORIZED**.
   - Greet them warmly by their organization name, ask for their personal name, and proceed with their request.

2. **Rejection / Non-Match:**
   - If the caller's stated organization does not clearly correspond to an entry above, evaluate as **UNAUTHORIZED**.
   - If this is their first attempt, give them one polite opportunity to clarify or restate their organization name clearly.
   - If their organization remains unlisted or unrecognized, deliver the polite rejection statement and terminate the call immediately using `end_call`.
   - Never provide support, information, or appointment scheduling to unauthorized callers.
