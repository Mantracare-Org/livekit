# Model Context Protocol (MCP) System Architecture

> **Protocol Version:** MCP 2.0 (Model Context Protocol)  
> **Transports:** SSE (Server-Sent Events) over HTTP / Streamable HTTP POST  
> **Target Services:** `mantra-auth` (:3000), `MantraAssist-backend` (:5500), `livekit-mcp` (:8000), `lkt` (:8081)  
> **Last Updated:** 2026-08-24

---

## 1. What is the Model Context Protocol (MCP)?

The **Model Context Protocol (MCP)** is an open industry standard (created by Anthropic) designed to connect AI applications and agents to external data sources, enterprise databases, and business tools in a secure, standardized way.

### Why MCP Instead of Traditional REST APIs?
Traditional AI applications require bespoke custom REST code, ad-hoc prompt engineering, and static API wrappers for every database or external tool. MCP replaces this with a structured **Client-Server Protocol**:

| Traditional REST Integration | Model Context Protocol (MCP) |
| :--- | :--- |
| Static, hardcoded endpoints requiring custom client code per API | **Dynamic Tool Discovery**: Agents automatically discover available tools, arguments, and types via JSON-RPC 2.0 schema negotiation. |
| Complex polling or fragmented websocket implementations | **Native Streaming Transports**: Built-in Server-Sent Events (SSE) and bidirectional messaging. |
| Inconsistent payload schemas and data serialization | **Strict JSON Schema Contracts**: Structured type validation and error handling for LLMs. |
| Tight coupling between backend endpoints and AI prompts | **Clean Separation of Concerns**: Backend services act as MCP clients, tools run in an isolated MCP server, and voice agents consume clean context. |

---

## 2. The 4-Tier MantraCare Architecture

Our telephony ecosystem consists of four specialized services collaborating via MCP:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. mantra-auth (:3000)                                                      │
│    Next.js OAuth 2.1 Authorization Server                                   │
│    • DB: postgres_auth (:5441 / mantra_auth_dev)                            │
│    • Issues HS256 JWT Tokens for Clients & Services                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Issues JWT Bearer Token
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. MantraAssist-backend (:5500) [MCP CLIENT]                                │
│    Express.js / TypeScript Core Backend                                     │
│    • Computes doctor availability from PostgreSQL (assist_db)               │
│    • Connects via `@modelcontextprotocol/sdk` to livekit-mcp                │
│    • Calls tool `receive_doctor_availability` over SSE                      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Invokes MCP Tool (JSON-RPC over SSE)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. livekit-mcp (:8000) [THE MCP SERVER & MIDDLE-MAN]                        │
│    Starlette ASGI + MCP 2.0 SSE Transport                                   │
│    • Auth Middleware: Validates shared HS256 JWT                            │
│    • Timezone Engine: Auto-detects caller country from phone (+1, +44, +91) │
│    • Normalizer: Converts raw UTC working hours ➔ Caller's Local Time       │
│    • Endpoints: /sse, /messages, /health, /api/tools/call                   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Real-time Voice Context
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. lkt (:8081) [VOICE TELEPHONY AGENT]                                      │
│    MantraCare LiveKit Voice Telephony Engine                                │
│    • STT (Deepgram) ➔ LLM (GPT-4o-Mini/DeepSeek) ➔ TTS (Sonic-3)           │
│    • Calls `check_doctor_availability` dynamically mid-conversation         │
│    • Speaks natural localized appointment slots to the caller               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. End-to-End Sequence Diagrams

### Flow A: In-Call Dynamic Doctor Availability Lookup (Mid-Call)

When a patient calls or receives an outbound call and asks for doctor availability mid-conversation:

```mermaid
sequenceDiagram
    autonumber
    actor Caller as 📞 Caller (e.g. +1 202 555 0123)
    participant LKT as 🤖 LiveKit Agent (lkt :8081)
    participant MCP as ⚙️ LiveKit MCP (:8000)
    participant Backend as 📱 MantraAssist Backend (:5500)
    participant Auth as 🔐 Mantra Auth (:3000)

    Caller->>LKT: "Is Dr. Sharma available next Tuesday afternoon?"
    Note over LKT: LLM detects scheduling intent<br/>Extracts date: 2026-08-25, doctor: Sharma
    LKT->>MCP: HTTP POST /api/tools/call<br/>{ tool: "search_provider_availability", date: "2026-08-25", caller_phone: "+12025550123" }
    Note over MCP: 1. Detects timezone: America/New_York (EDT)<br/>2. Queries provider schedule (UTC)<br/>3. Converts UTC slots ➔ EDT local time
    MCP-->>LKT: "📅 Dr. Sharma is available at 10:00 AM EDT and 12:00 PM EDT"
    LKT-->>Caller: "Dr. Sharma has open appointments on Tuesday at 10:00 AM and 12:00 PM EDT. Which time works for you?"
```

---

### Flow B: Backend Push / Pre-Computed Availability (MCP Client Flow)

When `MantraAssist-backend` computes availability and communicates with `livekit-mcp` as an MCP Client:

```mermaid
sequenceDiagram
    autonumber
    participant Backend as 📱 MantraAssist Backend (MCP Client)
    participant Auth as 🔐 Mantra Auth (:3000)
    participant MCP as ⚙️ LiveKit MCP Server (:8000)

    Backend->>Auth: POST /api/oauth/token (client_credentials)
    Auth-->>Backend: Return JWT Access Token (HS256)
    Backend->>MCP: SSE Connect: GET /sse?token=<JWT_TOKEN>
    MCP-->>Backend: Connection established (Endpoint URI for /messages)
    Note over Backend: Queries assist_db for doctor working hours & appointments
    Backend->>MCP: callTool("receive_doctor_availability", { org_id: 66, date: "2026-08-25", caller_phone: "+1...", providers: [...] })
    Note over MCP: Auto-detects caller timezone & validates slots
    MCP-->>Backend: Returns voice-ready formatted schedule confirmation
```

---

## 4. MCP Security & Authentication Model

1. **OAuth 2.1 Confidential Clients**:
   - `MantraAssist-backend` is registered as a **Trusted Client** in `mantra-auth`.
   - Uses `client_id` and `client_secret` to obtain JWT access tokens.
2. **Shared JWT Validation (`livekit-mcp`)**:
   - `livekit-mcp` validates JWTs using the shared secret (`JWT_SECRET`).
   - Tokens can be provided via standard HTTP Header (`Authorization: Bearer <token>`) or Query Parameter (`?token=<token>`) for EventSource SSE compatibility.
3. **No Unauthenticated Execution**:
   - Public routes: `/health`, `/`
   - Protected routes: `/sse`, `/messages`, `/api/tools/call`

---

## 5. Tool Catalog

| Tool Name | Type | Description |
| :--- | :--- | :--- |
| **`receive_doctor_availability`** | Receiver Tool | Accepts calculated doctor schedules (array of providers with UTC slots) from `MantraAssist-backend`, auto-detects caller timezone, and formats voice-ready text. |
| **`search_provider_availability`** | Query Tool | Direct PostgreSQL query tool for `assist_db` evaluating RFC 5545 recurrence rules and working hours. |
| **`greet_user`** | Diagnostic Tool | Health check and latency testing tool. |

---

## 6. Related Documentation
- [[Features/MCP Integration Guide for Backend.md|Backend Developer Integration Guide]]
- [[Features/Doctor Availability Tool.md|Voice Agent Doctor Availability Tool]]
- [[Architecture/APIs.md|LiveKit API Reference]]
- [[Architecture/Security & Auth.md|Security & Authentication Architecture]]
