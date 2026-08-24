# MCP Integration Guide for Backend Developers

> **Target Audience:** Backend Engineers (`MantraAssist-backend`)  
> **Protocol:** Model Context Protocol (MCP 2.0) over SSE  
> **Server Endpoint:** `http://localhost:8000/sse` (or production livekit-mcp URL)  
> **SDK:** `@modelcontextprotocol/sdk` (Node.js / TypeScript)  
> **Last Updated:** 2026-08-24

---

## 📌 Executive Summary for Backend Engineers

1. **You do NOT need to create REST endpoints for the AI agent to poll.**
2. Instead, your backend acts as an **MCP Client**.
3. Your backend connects to the **LiveKit MCP Server** (`livekit-mcp` on port `8000`) using the official `@modelcontextprotocol/sdk`.
4. When availability is queried or pushed, your backend invokes the standard MCP tool: **`receive_doctor_availability`**.
5. All times are sent in **UTC** alongside the caller's phone number (`caller_phone`). The MCP server automatically detects the caller's timezone (EDT, GMT, IST, GST) and localizes the schedule for the voice agent.

---

## 🚀 Step 1: Install MCP SDK

In your `MantraAssist-backend` project:

```bash
npm install @modelcontextprotocol/sdk
```

---

## 🔐 Step 2: Configure Environment Variables

Add the following to your `MantraAssist-backend/.env`:

```env
# LiveKit MCP Server Connection
LIVEKIT_MCP_URL=http://localhost:8000
MCP_JWT_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Mantra Auth Credentials (For dynamic token generation)
AUTH_SERVER_URL=http://localhost:3000
OAUTH_CLIENT_ID=test-client-id
OAUTH_CLIENT_SECRET=mantra_backend_secret_2026
```

---

## 📦 Step 3: Payload Data Contract (`structure.json`)

When calling `receive_doctor_availability`, send this exact JSON payload structure:

```json
{
  "org_id": 66,
  "date": "2026-08-25",
  "caller_phone": "+12025550123",
  "providers": [
    {
      "user_id": 12,
      "name": "Dr. Ananya Sharma",
      "available_slots": [
        "14:00 - 15:00",
        "16:00 - 17:00"
      ]
    },
    {
      "user_id": 15,
      "name": "Dr. Rajesh Kumar",
      "available_slots": [
        "15:00 - 16:00",
        "17:00 - 18:00"
      ]
    }
  ]
}
```

### Schema Field Definitions (1:1 Database Mapping):

| Field | Type | Prisma Database Origin | Description |
| :--- | :--- | :--- | :--- |
| **`org_id`** | `number` | `organizations.id` / `user_availability.org_id` | Target Organization ID |
| **`date`** | `string` | Target query date | Target date in `YYYY-MM-DD` format |
| **`caller_phone`** | `string` | Caller Phone (`+1...`, `+44...`, `+91...`) | Used to auto-detect the caller's IANA timezone |
| **`providers`** | `array` | Array of doctor objects | List of available doctors |
| ↳ **`user_id`** | `number` | `users.id` / `user_availability.user_id` | Doctor User ID |
| ↳ **`name`** | `string` | `users.name` | Doctor's full name |
| ↳ **`available_slots`**| `string[]`| Calculated from `user_availability` - `appointments` | Array of 24h **UTC** time ranges (e.g. `["14:00 - 15:00"]`) |

---

## 🌍 Timezone Rules for Backend

* **Database Storage**: Times in PostgreSQL (`user_availability`, `appointments`) are stored in **UTC**.
* **Backend Processing**: When calculating open slots, **keep them in UTC** (e.g., `"14:00 - 15:00"`).
* **Timezone Localization**: When you provide `caller_phone` (e.g., `+12025550123` for US or `+918360625862` for India), the **LiveKit MCP Server automatically resolves the caller's timezone** and converts the UTC slots into natural 12-hour voice format:
  * For US caller: `14:00 UTC` ➔ `10:00 AM EDT`
  * For UK caller: `14:00 UTC` ➔ `3:00 PM BST`
  * For India caller: `04:30 UTC` ➔ `10:00 AM IST`

---

## 💻 Step 4: Complete Copy-Paste TypeScript Service

Create `src/services/LiveKitMcpClient.ts` in `MantraAssist-backend`:

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";

export interface ProviderSchedulePayload {
  user_id: number;
  name: string;
  available_slots: string[]; // UTC time ranges e.g. ["14:00 - 15:00", "16:00 - 17:00"]
}

export interface DoctorAvailabilityPayload {
  org_id: number;
  date: string; // "YYYY-MM-DD"
  caller_phone?: string; // "+12025550123"
  providers: ProviderSchedulePayload[];
}

export class LiveKitMcpClient {
  private client: Client | null = null;
  private transport: SSEClientTransport | null = null;
  private mcpUrl: string;
  private jwtToken: string;

  constructor() {
    this.mcpUrl = process.env.LIVEKIT_MCP_URL || "http://localhost:8000";
    this.jwtToken = process.env.MCP_JWT_TOKEN || "";
  }

  /**
   * Connect to the LiveKit MCP Server over SSE
   */
  public async connect(): Promise<void> {
    if (this.client) {
      return; // Already connected
    }

    try {
      const sseUrl = new URL(`${this.mcpUrl}/sse?token=${this.jwtToken}`);
      this.transport = new SSEClientTransport(sseUrl);
      
      this.client = new Client(
        {
          name: "mantrassist-backend-client",
          version: "1.0.0",
        },
        {
          capabilities: {},
        }
      );

      await this.client.connect(this.transport);
      console.log("✅ [MCP] Connected to LiveKit MCP Server at", this.mcpUrl);
    } catch (error) {
      console.error("❌ [MCP] Failed to connect to LiveKit MCP Server:", error);
      this.client = null;
      this.transport = null;
      throw error;
    }
  }

  /**
   * Send doctor availability schedule to LiveKit MCP
   */
  public async sendDoctorAvailability(payload: DoctorAvailabilityPayload): Promise<string> {
    if (!this.client) {
      await this.connect();
    }

    try {
      const response = await this.client!.callTool({
        name: "receive_doctor_availability",
        arguments: payload as Record<string, unknown>,
      });

      const resultText = Array.isArray(response.content)
        ? response.content.map((c: any) => c.text || "").join("\n")
        : JSON.stringify(response);

      console.log("✅ [MCP] Doctor availability delivered successfully");
      return resultText;
    } catch (error) {
      console.error("❌ [MCP] Error calling receive_doctor_availability:", error);
      throw error;
    }
  }

  /**
   * Close the MCP connection gracefully
   */
  public async disconnect(): Promise<void> {
    if (this.transport) {
      await this.transport.close();
      this.client = null;
      this.transport = null;
      console.log("🔌 [MCP] Disconnected from LiveKit MCP Server");
    }
  }
}

// Export singleton instance
export const livekitMcp = new LiveKitMcpClient();
```

---

## 🧪 Step 5: Quick Test Script

Create `scripts/test-mcp.ts` in `MantraAssist-backend`:

```typescript
import { livekitMcp } from "../src/services/LiveKitMcpClient";

async function runTest() {
  console.log("🚀 Testing LiveKit MCP Connection...");
  
  const testPayload = {
    org_id: 66,
    date: "2026-08-25",
    caller_phone: "+12025550123", // US Caller Number
    providers: [
      {
        user_id: 12,
        name: "Dr. Ananya Sharma",
        available_slots: [
          "14:00 - 15:00", // 10:00 AM - 11:00 AM EDT
          "16:00 - 17:00"  // 12:00 PM - 1:00 PM EDT
        ],
      },
      {
        user_id: 15,
        name: "Dr. Rajesh Kumar",
        available_slots: [
          "18:00 - 19:00"  // 2:00 PM - 3:00 PM EDT
        ],
      },
    ],
  };

  try {
    const formattedResult = await livekitMcp.sendDoctorAvailability(testPayload);
    console.log("\n📋 Formatted Result from MCP Server for Voice Agent:\n");
    console.log(formattedResult);
  } catch (error) {
    console.error("Test failed:", error);
  } finally {
    await livekitMcp.disconnect();
  }
}

runTest();
```

Run test:
```bash
npx tsx scripts/test-mcp.ts
```

---

## ❓ FAQ & Troubleshooting

### 1. What if my JWT token expires?
Generate a new token from `mantra-auth` or run the CLI helper in `~/livekit-mcp`:
```bash
cd ~/livekit-mcp
uv run python scripts/generate_token.py --hours 8760
```

### 2. Can I test with Indian callers?
Yes! Simply pass `caller_phone: "+918360625862"`. The MCP server will automatically format the slots in **IST** (`Asia/Kolkata`).

### 3. What if a doctor has no open slots on that day?
Send `available_slots: []`. The MCP server will output `"No open slots on this date"` to the voice agent.
