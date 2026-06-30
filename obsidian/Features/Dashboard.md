# Dashboard

**Files:**
- `static/dashboard.html` (577 lines) — HTML/CSS
- `static/dashboard.js` (206 lines) — Client logic

## Overview

Operations monitoring dashboard with "OpsCraft" dark theme (Discord/Linear inspired).

## Features

- **Metrics Bar:** Today's total calls, answer rate, avg duration (from Postgres, refreshed 30s)
- **Queue Gauge:** Visual bar showing active/max concurrency usage
- **Active Calls:** Live cards with call ID, room, status; updates via SSE every 2s
- **Activity Feed:** Recent events (calls started, ended, queued)
- **Call History:** Paginated table with status, caller, duration, timestamp (from Postgres)
- **Logout:** Clears JWT token

## SSE Stream

Connects to `GET /api/v1/dashboard/stream?token=<jwt>` with auto-reconnect on error.

Data: `{ pending_calls, active_calls, max_concurrency, active_call_details, timestamp }`

## Auth

JWT token stored in `localStorage`. Redirects to `/` if missing or 401.
