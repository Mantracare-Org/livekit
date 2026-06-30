# Master Agent

## When to Use This Vault

Start here whenever resuming work on this repository. Read [[Home.md]] → [[Architecture/Overview.md]] → [[Development/Current Sprint.md]].

## Repository Summary

Production-grade bilingual voice AI agent built on LiveKit Cloud for outbound telephony. Orchestrates STT→LLM→TTS pipeline with Redis queue dispatch, SIP trunking (Twilio/Plivo/Zadarma), and post-call processing (S3 + PostgreSQL + CRM webhook).

## Key Files

| File | Role |
|------|------|
| `mantra/agent.py` | Voice agent worker |
| `mantra/ui_server.py` | HTTP API + dashboard server |
| `mantra/dispatcher.py` | Redis queue consumer |
| `mantra/utils.py` | Recording, analysis, webhooks |
| `mantra/email_alerts.py` | Crash notifications |
| `mcp/server.py` | Postgres MCP server |
| `static/` | Frontend (HTML/JS/CSS) |

## Before Making Changes

1. Read [[Architecture/Overview.md]]
2. Read [[Development/TODO.md]]
3. Update [[Development/Changelog.md]] after
4. Update relevant feature docs
