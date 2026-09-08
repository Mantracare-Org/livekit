# Doctor & Healthcare Provider Availability Tool

> **Status:** Production Ready  
> **Source:** [`mantra/agent.py`](file:///home/fardeen/lkt/mantra/agent.py)  
> **MCP Server:** `http://localhost:8000` (`livekit-mcp`)  
> **Last Updated:** 2026-08-22

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
        "ALWAYS use this tool whenever the caller asks about doctor availability, open consultation times, "
        "scheduling an appointment, or doctor working hours on a given day."
    )
)
async def check_doctor_availability(
    self,
    date: Annotated[str, "The date to check in YYYY-MM-DD format (e.g. '2026-08-25'). If the caller specifies a relative day like 'tomorrow' or 'next Tuesday', calculate the exact YYYY-MM-DD date."],
    doctor_name: Annotated[Optional[str], "Optional doctor name to filter by (e.g. 'Sharma' or 'Dr. Ananya')."] = None,
) -> str:
```

---

## Execution Flow

```
1. Caller asks: "Can I book Dr. Sharma next Tuesday?"
   │
   ▼
2. LLM invokes: `fnc_ctx.check_doctor_availability(date="2026-08-25", doctor_name="Sharma")`
   │
   ▼
3. Agent extracts `org_id` and `caller_phone` from metadata / call state
   │
   ▼ (HTTP POST to http://localhost:8000/tools/call with JWT Bearer auth)
4. `livekit-mcp` searches provider availability in `assist_db`
   • Auto-detects caller timezone via Google `phonenumbers` (e.g. +1... -> EDT, +91... -> IST)
   • Evaluates RFC 5545 recurrence rules
   • Converts UTC working hours to caller's local 12-hour format
   │
   ▼
5. Formatted string returned to LLM:
   "📅 Available Slots for Dr. Ananya Sharma on Tuesday, Aug 25, 2026 (Timezone: America/New_York):
    • 10:00 AM – 11:00 AM EDT
    • 12:00 PM – 1:00 PM EDT"
   │
   ▼
6. Voice Agent speaks natural response to caller over SIP trunk
```

---

## Environment Configuration

In `.env`:
```env
LIVEKIT_MCP_URL=http://localhost:8000
LIVEKIT_MCP_JWT_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```
