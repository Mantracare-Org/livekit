# Test Console

**Files:**
- `static/index.html` (475 lines) — HTML/CSS
- `static/app.js` (253 lines) — Client logic

## Overview

Manual agent testing interface. Connects to LiveKit rooms via WebRTC and displays real-time transcript.

## Features

- **Structured Tab:** Client name, call ID, lead ID, prompt fields
- **Raw JSON Tab:** Paste raw JSON payload, parse into structured fields
- **Connect:** POST to `/dispatch-test` → get token → join LiveKit room
- **Disconnect:** Leave room
- **Transcript Display:** Real-time chat messages with interim support
- **Mic Toggle:** Enable/disable microphone
- **Visualizer:** Active speaker indicator during agent speech

## WebRTC Flow

```
POST /dispatch-test → get { room, token, url }
Room.connect(url, token)
Publish microphone → Subscribe to agent audio
DataReceived → transcript JSON messages
```
