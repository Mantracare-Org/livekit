# Dashboard

**Files:** `static/dashboard.html` (1,130 lines) + `static/dashboard.js` (1,150 lines)

## Overview

Operations dashboard for monitoring telephony activity in real time. OpsCraft dark theme with card-style layout, tabbed inspection modals, and live SSE event streams.

## Features

### Navigation & Health Indicator
- **System Status Indicator**:
  - Live health badge in navigation bar (`#system-status`) polling `GET /health` every 15 seconds
  - Displays "All systems operational" (`.operational`) or "Systems degraded" (`.degraded`)
  - Interactive status popover (`#system-status-popover`) connected to `GET /health/details` displaying per-service health breakdowns (Postgres, Redis, STT, LiveKit, MantraAssist backend, S3)
  - Auto-dismisses on outside click

### Metrics Bar
- Today's calls (total, completed, busy, no answer, error, incomplete)
- Answer rate percentage
- Average call duration
- Refreshes automatically via `GET /v1/dashboard/metrics`

### Queue Gauge
- Pending call count from Redis SSE stream (`queue:pending`)
- Active call count (`calls:active`)
- Max concurrency limit indicator

### Active Calls
- Real-time card display of active calls via SSE
- Status badges with animated green speaking pulse indicator (`.active-pulse`)
- Displays: `call_id`, `room_name`, and status

### Call History Table
- Search bar with instant keyup filtering (`/` shortcut to focus)
- Status filter dropdown (`All`, `Completed`, `Busy`, `No Answer`, `Error`, `Incomplete`, `Ignored`)
- **Server-Side Pagination Bar**:
  - Configurable page sizes: `25`, `50`, `100`, `200`
  - Record range display: `Showing X to Y of Z calls`
  - Page navigation controls with disabled boundary handling
- **Data Exporting**:
  - One-click **Export CSV** (generates formatted CSV file client-side)
  - One-click **Export JSON** (generates formatted JSON log dump client-side)

### Call Log Details Modal
- **Call Step Navigation**: Previous (`← Prev`) and Next (`Next →`) buttons (`ArrowLeft` / `ArrowRight` / `j` / `k`) to browse records without closing the modal
- **One-Click Redial**: Direct redial trigger (`#btn-redial-modal-call`) that confirms and dispatches an outbound call to the caller number via `/v1/webhooks/telephony`
- **Event Name Display & Persistence**:
  - Explicitly displays the webhook event name (`CALL_DATA_UPDATE`, `CALL_RETRY`, `CALL_DATA_INBOUND_UPDATE`) in the overview metadata grid (`#modal-call-event`) and retry timeline cards
  - Full event name persisted in PostgreSQL DB `call_logs.call_log` JSON and within the attempts payload history
- **Tabbed Modal Navigation**:
  - `Overview & Summary`: Metadata grid and AI call summary
  - `Transcript`: Multi-turn dialogue bubbles with click-to-seek audio integration
  - `Retry History`: Timeline of all retry attempts and previous call statuses
  - `Raw Payload`: Indented raw PostgreSQL JSON log dump
  - `All Sections`: Full expanded view
- **Audio Recording Player**:
  - Dedicated playback speed controls (`1x`, `1.25x`, `1.5x`, `2x`)
  - Dedicated **Download MP3** button
  - Transcript click-to-seek: clicking turns seeks playback directly to turn timestamps
- **One-Click Copy Buttons**:
  - **Copy Summary**: Copies AI summary to clipboard
  - **Copy Transcript**: Formats dialogue turns into plain-text dialogue (`Caller: ...` / `Agent: ...`)
  - **Copy Payload**: Copies indented JSON payload
  - Visual checkmark indicator and toast confirmation

### Global Toast Notifications
- Lightweight top-right floating toast notification container (`#toast-container`)
- Provides immediate feedback for copy actions, audio seek points, exports, and errors

### Keyboard Shortcuts
- `/` — Focus search bar
- `Esc` — Close call inspection modal
- `j` / `ArrowRight` — Navigate to next call in inspection modal
- `k` / `ArrowLeft` — Navigate to previous call in inspection modal
- `Space` — Toggle audio playback when modal is open

## SSE Stream

Endpoint: `GET /v1/dashboard/stream` (Server-Sent Events)
- 2s interval updates
- Payload: `{ pending_calls, active_calls, max_concurrency, active_call_details, timestamp }`
- Reads from Redis: `queue:pending` (ZCARD), `calls:active` (HGETALL), `calls:status:{id}` (GET)
