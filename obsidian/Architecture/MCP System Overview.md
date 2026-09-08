# Model Context Protocol (MCP) System Architecture

> **Architecture Style:** MCP Gateway & Microservice Routing  
> **Server Port:** `livekit-mcp` on port `:8000`  
> **Backend Integration:** HTTP REST Endpoint on `MantraAssist-backend` (`:5500`)  
> **Telephony Engine:** `lkt` Voice Agent on `:8081`  
> **Last Updated:** 2026-08-25

---

## 1. System Overview

The **LiveKit MCP Server** acts as the central intelligence and context middleware for MantraCare's voice telephony engine. 

Instead of embedding proprietary backend logic or hardcoded REST URLs inside the voice agent, the agent interacts with **MCP Tools**. When an availability query is made, `livekit-mcp` executes a high-speed HTTP request to the `MantraAssist-backend` REST API, converts the returned UTC doctor schedules into the caller's localized timezone, and returns conversational, speech-ready text to the LLM.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. lkt (:8081) [VOICE TELEPHONY AGENT]                                      │
│    • Live Inbound / Outbound Phone Call                                     │
│    • STT (Deepgram) ➔ LLM (DeepSeek/GPT-4o) ➔ TTS (Sonic-3)                 │
│    • Calls MCP tool: `receive_doctor_availability`                          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 1. Executes MCP Tool (HTTP POST /tools/call)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. livekit-mcp (:8000) [THE MCP SERVER & GATEWAY]                           │
│    • Starlette ASGI + FastMCP Framework                                     │
│    • Timezone Resolver: Google phonenumbers (+1 ➔ EDT, +91 ➔ IST)           │
│    • Timezone Converter: UTC ranges (04:30) ➔ Local 12h slots (10:00 AM)    │
│    • Fallback: Direct PostgreSQL assist_db query if backend is unreachable  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 2. HTTP GET /v1/providers/availability
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. MantraAssist-backend (:5500) [CORE BACKEND API]                          │
│    • Express.js / TypeScript REST Service                                   │
│    • Endpoint: `GET /v1/providers/availability`                         │
│    • Queries PostgreSQL `user_availability` and `appointments`              │
│    • Returns doctor list and open slots in standard UTC                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. End-to-End Dynamic Call Flow

```mermaid
sequenceDiagram
    autonumber
    actor Caller as 📞 Caller (e.g. +1 202 555 0123)
    participant LKT as 🤖 LiveKit Agent (lkt :8081)
    participant MCP as ⚙️ LiveKit MCP Server (:8000)
    participant Backend as 📱 MantraAssist Backend (:5500)
    participant DB as 🗄️ PostgreSQL (assist_db)

    Caller->>LKT: "Is Dr. Ananya Sharma available tomorrow for an appointment?"
    Note over LKT: LLM detects scheduling query<br/>Date: 2026-08-25, Doctor: Sharma
    LKT->>MCP: POST /tools/call<br/>{ tool: "receive_doctor_availability", org_id: 68, date: "2026-08-25", caller_phone: "+12025550123" }
    
    MCP->>Backend: HTTP GET /v1/providers/availability?org_id=68&date=2026-08-25&query=Dr.+Ananya+Sharma
    Backend->>DB: Query user_availability & appointments
    DB-->>Backend: Return raw working hours & busy slots (UTC)
    Backend-->>MCP: HTTP 200: { providers: [{ name: "Dr. Ananya Sharma", available_slots: ["14:00 - 15:00", "16:00 - 17:00"] }] }
    
    Note over MCP: 1. Detects caller timezone: America/New_York (EDT)<br/>2. Converts UTC 14:00 ➔ 10:00 AM EDT<br/>3. Formats speech-ready text without asterisks
    
    MCP-->>LKT: "Dr. Ananya Sharma is available on Tuesday, Aug 25, 2026: 10:00 AM – 11:00 AM EDT, 12:00 PM – 1:00 PM EDT."
    LKT-->>Caller: "Dr. Ananya Sharma is open tomorrow at 10:00 AM and 12:00 PM EDT. Which slot would you prefer?"
```

---

## 3. Key Advantages of This Architecture

1. **Clean Separation of Concerns**:
   - Backend developers build simple, robust REST endpoints (`GET /v1/providers/availability`) in Node.js.
   - LiveKit MCP handles timezone parsing, phone parsing, LLM context formatting, and protocol normalization.
   - The Voice Agent receives pre-formatted, speech-optimized text.

2. **International Timezone Intelligence**:
   - The backend stores and returns all times in pure **UTC**.
   - `livekit-mcp` automatically detects if the caller is in New York (`+1`), London (`+44`), Mumbai (`+91`), or Dubai (`+971`), converting the UTC times on the fly without any backend timezone logic.

3. **High Availability & Resiliency**:
   - If the backend REST service is restarting or experiencing latency, `livekit-mcp` has a built-in direct PostgreSQL query fallback to ensure live calls are never interrupted.

---

## 4. Related Guides
- [[Features/MCP Integration Guide for Backend.md|Backend Developer REST Integration Guide]]
- [[Features/Doctor Availability Tool.md|Voice Agent Doctor Availability Tool]]
- [[MCP Architecture.canvas|Interactive MCP Whiteboard Canvas]]
