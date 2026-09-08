# Test Console

**Files:** `static/index.html` (475 lines) + `static/app.js` (253 lines)

## Overview

Manual agent testing UI for development. Provides WebRTC room connection, microphone integration, and transcript display.

## Flow

1. Enter test payload (JSON) in the editor
2. Click "Connect" → POST `/dispatch-test` → get room token
3. Join LiveKit room via WebRTC
4. Microphone streams to agent, transcript appears in real time
5. Disconnect button ends the session

## Features

- Structured payload editor with toggleable fields
- Live transcript display (user + agent messages)
- Mic on/off toggle
- Active speaker visualizer
- Room connection state display
- OpsCraft dark theme

## Testing Inbound Calls

Use `POST /v1/test/inbound-call` endpoint to simulate inbound calls — dispatches agent with `direction: inbound` metadata and triggers SIP outbound call to the tester's phone.
