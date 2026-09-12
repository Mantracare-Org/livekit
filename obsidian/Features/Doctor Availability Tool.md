# Doctor & Healthcare Provider Availability Tool

> **Status:** Production Ready  
> **Source:** [`mantra/agent.py`](file:///home/fardeen/lkt/mantra/agent.py) (`clarify_medical_department` + `check_doctor_availability`)  
> **MCP Server:** remote `livekit-mcp` over SSE (`MantraMCPClient` in `mantra/mcp_client.py`, dynamic OAuth)  
> **Last Updated:** 2026-09-10

---

## Overview

The **Doctor Availability Tool** (`check_doctor_availability`) enables the LiveKit Voice AI agent to dynamically check real-time doctor working hours, open appointment slots, and provider schedules during both **inbound** and **outbound** telephony calls.

Whenever a caller asks a scheduling or availability question (e.g. *"Is Dr. Sharma available next Tuesday?"*, *"Who can I see tomorrow afternoon?"*), the LLM invokes this function tool mid-call, queries `livekit-mcp`, and receives localized time slots tailored to the caller's timezone.

---

## Tool Signature

```python
@llm.function_tool(
    description=(
        "Check doctor and healthcare provider availability, working hours, and open appointment slots on a specific date. "
        "This is the authoritative real-time MCP tool for appointment availability. Never use the knowledge base for this request. "
        ...
    )
)
async def check_doctor_availability(
    self,
    date: Annotated[str, "The date to check in YYYY-MM-DD format (e.g. '2026-08-25'). If the caller specifies a relative day like 'tomorrow' or 'next Tuesday', calculate the exact YYYY-MM-DD date."],
    doctor_name: Annotated[Optional[str], "Optional doctor name to filter by (e.g. 'Sharma' or 'Dr. Ananya')."] = None,
    department: Annotated[Optional[str], "Optional medical department/specialty (e.g. 'Cardiology', 'Dermatology', 'Orthopedics'). Must exactly match the org list when available."] = None,
) -> str:

@llm.function_tool(
    description=(
        "Use when the caller gives a broad medical symptom without a clear department ... "
        "Fetch the organization's department list silently ... then call check_doctor_availability with the selected exact department."
    )
)
async def clarify_medical_department(
    self,
    symptom: Annotated[str, "The caller's broad symptom or reason for the appointment."],
) -> str:
```

**Department gate:** when the org returns a department list, `check_doctor_availability` exact-matches `department` (case-insensitive) and rejects invented values (e.g. `Ophthalmology`) with a retry-through-clarification directive. Failed department discovery never blocks the MCP availability call. `provider_user_id` is parsed from the MCP result (`User ID: N`) into `appointment_metadata` for the post-call webhook. All timestamps are UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`).

---

## Execution Flow

```
1. Caller asks: "Can I book Dr. Sharma next Tuesday?"
   │  (broad symptom like "eye problem" → `clarify_medical_department` first, silently)
   ▼
2. LLM invokes: `fnc_ctx.check_doctor_availability(date="2026-08-25", doctor_name="Sharma", department="Ophthalmology")`
   │
   ▼
3. Agent extracts `org_id` and `caller_phone` from call state / job metadata; preloads `department_options` via `get_org_departments`
   │
   ▼ (MCP SSE `tools/call` to `livekit-mcp` with dynamic OAuth Bearer token, browser UA + `ngrok-skip-browser-warning` headers)
4. `livekit-mcp` (`receive_doctor_availability`) searches provider availability
   • Auto-detects caller timezone via Google `phonenumbers` (e.g. +1... -> EDT, +91... -> IST)
   • Evaluates RFC 5545 recurrence rules; past-year dates roll forward to current year
   • Converts UTC working hours to caller's local 12-hour format; injects `User ID: N`
   │
   ▼
5. Formatted string returned to LLM:
   "📅 Available Slots for Dr. Ananya Sharma on Tuesday, Aug 25, 2026 (Timezone: America/New_York):
    • 10:00 AM – 11:00 AM EDT
    • 12:00 PM – 1:00 PM EDT"
   │  (agent captures `provider_user_id` for `appointment_metadata`)
   ▼
6. Voice Agent speaks natural response to caller over SIP trunk
```

---

## Environment Configuration

```env
LIVEKIT_MCP_URL=https://livekit-mcp.app-mantra.com  # or http://localhost:8000 for local
OAUTH_CLIENT_ID=...
OAUTH_CLIENT_SECRET=...
# Legacy static token fallback (deprecated): LIVEKIT_MCP_JWT_TOKEN=...
NO_PROXY=auth.mantracare.com,livekit-mcp.app-mantra.com,app-mantra.com
```
