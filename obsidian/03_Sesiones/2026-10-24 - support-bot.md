---
title: "Sesiones: support-bot (2026-10-24)"
type: antigravity-session-daily
project: "support-bot"
tags:
  - antigravity/sesion
date: 2026-10-24
updated: "16:51"
summary: "Set user outgoing messages to insert with read=false until room is loaded"
milestones: 3
---

# Sesiones: support-bot — 2026-10-24

> [!NOTE] **Último Hito (16:51)**
> Set user outgoing messages to insert with read=false until room is loaded

## 16:14 — Added GET /assistant/unread-count endpoint and unread count to get-app-services

### Trabajo Realizado y Decisiones Técnicas
- Added GET /assistant/unread-count in support-bot routers/assistant.py with support for total and per-service unread filtering.
- Implemented get_unread_count method in core/assistant/graph.py querying assistant_chats (author=-1, read=False) grouped by service_id.
- Added UnreadCountResponseModel to custom_types/assistant.py.
- Enriched POST /assistant/get-app-services to also return unread_count per service card.
- Reverted all changes in mantraback as requested.


## 16:45 — Fixed get_messages internal hydration marking unread messages as read prematurely

### Trabajo Realizado y Decisiones Técnicas
- Diagnosed root cause: LangGraph conversation hydration called self.get_messages() internally during each incoming SSE turn, which prematurely marked prior unread assistant replies as read.
- Added explicit mark_as_read parameter to get_messages (defaulting to False).
- Updated GET /assistant/get-messages endpoint in routers/assistant.py to pass mark_as_read=True so that messages are only marked read when the user actually loads the conversation.
- Verified multi-turn unread count increments accurately (1 -> 2 -> 3) and clears to 0 upon loading messages.


## 16:51 — Set user outgoing messages to insert with read=false until room is loaded

### Trabajo Realizado y Decisiones Técnicas
- Updated core/assistant/graph.py so user messages are inserted with read=False (instead of default True).
- Updated get_messages mark-read condition so that both user and assistant unread messages in the room are marked read=True when get-messages is called.
- Verified in PostgreSQL: both user messages and assistant messages start with read=False and only transition to read=True when get-messages is loaded.

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[Proyecto: support-bot]] | [[00 Indice de Sesiones]]
