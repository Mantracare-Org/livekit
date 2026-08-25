# MantraAssist Backend Developer Guide: Doctor Availability Endpoint

> **Target Audience:** Backend Engineers (`MantraAssist-backend`)  
> **Service:** Express.js / TypeScript REST API (:5500)  
> **Caller Service:** `livekit-mcp` on `:8000`  
> **Last Updated:** 2026-08-25

---

## 📌 Executive Summary for Backend Engineers

1. **What you need to build:** A single HTTP REST endpoint:  
   `GET /api/v1/providers/availability` (or `POST /api/v1/providers/availability`)
2. **What MCP sends to your endpoint (Always in UTC):**
   - **`org_id`**: Organization ID (e.g. `68` or `""`)
   - **`date`**: UTC Date string in `YYYY-MM-DD` (e.g. `"2026-08-25"` or `""`)
   - **`datetime`**: Full ISO 8601 UTC timestamp (`YYYY-MM-DDTHH:mm:ss.sssZ`, e.g. `"2026-08-25T00:00:00.000Z"` or `""`)
   - **`doc_name`**: Doctor name filter (e.g. `"Dr. Ananya Sharma"` or `""` if not mentioned)
   - **`department`**: Department / Specialty filter (e.g. `"Cardiology"`, `"Dermatology"`, `"Orthopedics"`, or `""` if not mentioned)
3. **What your endpoint returns:** List of providers with their available time slots in **UTC** (e.g. `["04:30 - 05:30", "14:00 - 15:00"]`).
4. **Timezone Handling:** Everything exchanged with your backend is **100% in UTC**. `livekit-mcp` handles all the timezone localization for the patient on the call.

---

## 🛠️ Endpoint Specification

### `GET /api/v1/providers/availability`

#### Query Parameters:

| Parameter | Type | Required | Example | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`org_id`** | `number \| string` | **Yes** | `68` | Target Organization ID (`organizations.id`) |
| **`date`** | `string` | **Yes** | `"2026-08-25"` | UTC date in `YYYY-MM-DD` (or `""`) |
| **`datetime`** | `string` | **Yes** | `"2026-08-25T00:00:00.000Z"` | Full ISO 8601 UTC datetime timestamp (or `""`) |
| **`doc_name`** | `string` | **Yes** | `"Dr. Ananya Sharma"` | Doctor name filter (sends `""` if caller didn't specify a doctor) |
| **`department`** | `string` | **Yes** | `"Cardiology"` | Department / Specialty filter (sends `""` if not specified) |
| **`caller_phone`** | `string` | No | `"+917795163421"` | Caller's phone number |

---

### Request Payload Examples Sent by MCP

#### 1. When caller asks for a specific doctor & department:
```json
{
  "org_id": 68,
  "date": "2026-08-25",
  "datetime": "2026-08-25T00:00:00.000Z",
  "doc_name": "Dr. Ananya Sharma",
  "department": "Dermatology"
}
```

#### 2. When caller asks for a department without a specific doctor:
*(e.g., "Is any Cardiologist available tomorrow?")*
```json
{
  "org_id": 68,
  "date": "2026-08-25",
  "datetime": "2026-08-25T00:00:00.000Z",
  "doc_name": "",
  "department": "Cardiology"
}
```

---

### Expected Response Format (`HTTP 200 OK`)

```json
{
  "status": "success",
  "org_id": 68,
  "date": "2026-08-25",
  "providers": [
    {
      "user_id": 101,
      "name": "Dr. Ananya Sharma",
      "specialization": "Dermatology",
      "available_slots": [
        "04:30 - 05:30",
        "06:00 - 07:00",
        "08:30 - 09:30",
        "11:00 - 12:00"
      ]
    }
  ]
}
```

---

## 💻 Sample Express.js Controller Implementation

```typescript
import { Request, Response } from "express";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

export async function getProviderAvailability(req: Request, res: Response) {
  try {
    const orgId = parseInt((req.query.org_id || req.body?.org_id) as string, 10) || 68;
    const dateStr = ((req.query.date || req.body?.date) as string) || "";
    const utcDatetime = ((req.query.datetime || req.body?.datetime) as string) || "";
    const docName = ((req.query.doc_name || req.body?.doc_name) as string || "").trim();
    const department = ((req.query.department || req.body?.department) as string || "").trim();

    // 1. Query active providers & working hours in UTC
    const providers = await prisma.user.findMany({
      where: {
        org_id: orgId,
        role: "DOCTOR",
        isActive: true,
        ...(docName
          ? {
              name: {
                contains: docName,
                mode: "insensitive",
              },
            }
          : {}),
        ...(department
          ? {
              specialization: {
                contains: department,
                mode: "insensitive",
              },
            }
          : {}),
      },
      include: {
        user_availability: {
          where: {
            org_id: orgId,
          },
        },
      },
    });

    // 2. Format working slots in UTC (e.g. "04:30 - 05:30")
    const formattedProviders = providers.map((provider) => {
      const slots: string[] = [];

      for (const avail of provider.user_availability) {
        const startTime = avail.start_time; // UTC time string or Date
        const endTime = avail.end_time;     // UTC time string or Date

        if (startTime && endTime) {
          slots.push(`${startTime} - ${endTime}`);
        }
      }

      return {
        user_id: provider.id,
        name: provider.name,
        specialization: provider.specialization || "General Medicine",
        available_slots: slots.length > 0 ? slots : [],
      };
    });

    return res.status(200).json({
      status: "success",
      org_id: orgId,
      date: dateStr || (utcDatetime ? utcDatetime.split("T")[0] : ""),
      providers: formattedProviders,
    });
  } catch (error) {
    console.error("Error fetching provider availability:", error);
    return res.status(500).json({
      status: "error",
      message: "Internal server error fetching doctor schedules",
    });
  }
}
```
