# Dashboard

**Files:** `static/dashboard.html` (577 lines) + `static/dashboard.js` (206 lines)

## Overview

Operations dashboard for monitoring call activity in real time. OpsCraft dark theme with card-style layout.

## Features

### Metrics Bar
- Today's calls (total, completed, busy, no answer, error, incomplete)
- Answer rate percentage
- Average call duration
- Refreshes every 30 seconds via `GET /v1/dashboard/metrics`

### Queue Gauge
- Pending call count from Redis SSE stream
- Active call count
- Max concurrency limit

### Active Calls
- Real-time card display of active calls via SSE (2s updates)
- Each card shows: call_id, room_name, status
- Data from `GET /v1/dashboard/active-calls` + SSE

### Call History
- Paginated table of recent calls
- Columns: call_id, status, client, phone, duration, recording, summary, purpose
- Data from `GET /v1/dashboard/calls?limit=20&offset=0`

### Authentication
- JWT login page (`login.html`)
- Token stored in localStorage
- Logout button clears token
- Auth required for dashboard routes (currently commented out)

## SSE Stream

Endpoint: `GET /v1/dashboard/stream` (Server-Sent Events)
- 2s interval updates
- Payload: `{ pending_calls, active_calls, max_concurrency, active_call_details, timestamp }`
- Reads from Redis: `queue:pending` (ZCARD), `calls:active` (HGETALL), `calls:status:{id}` (GET)
