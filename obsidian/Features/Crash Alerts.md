# Crash Alerts

**File:** `mantra/email_alerts.py` (201 lines)

## Overview

SMTP-based crash notification system with optional meme generation for admin recipients.

## Flow

1. Exception caught in `agent.py` or `ui_server.py`
2. `send_crash_email(service_name, error, context_data)` called
3. Executes via `asyncio.to_thread()` (non-blocking)
4. Builds premium HTML email with:
   - Error type + message
   - Environment context (room name, job ID, PID, etc.)
   - Full stack trace
   - Optional meme image (admin recipients only)
5. Sends via SMTP (port 465 SSL or 587 STARTTLS)

## Meme Feature

- Admin recipients (from `ADMIN_MAIL_ID`) get auto-generated memes
- Meme templates: `["fine", "pigeon", "harold", "disastergirl", "rollsafe", ...]`
- Generated via `memegen.link` with randomized template selection
- Falls back gracefully if meme generation fails

## Configuration

| Env Var | Description |
|---------|-------------|
| `SMTP_HOST` | SMTP server |
| `SMTP_PORT` | Port (default 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password |
| `SMTP_FROM_EMAIL` | From address |
| `ALERT_EMAIL_IDS` | Comma-separated alert recipients |
| `ADMIN_MAIL_ID` | Admin recipients (get memes) |
