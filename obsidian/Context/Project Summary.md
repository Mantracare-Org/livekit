# Project Summary

## Mantra Voice Agent

A production-grade, low-latency bilingual (English/Hindi) voice AI agent for telephony. Built on LiveKit Cloud, it orchestrates Deepgram STT → LLM (OpenAI/Gemini/DeepSeek) → LiveKit native sonic-3 TTS in real time.

## Purpose

Professional care support and automated outbound follow-up calls for MantraCare/MantraAssist. Handles appointment scheduling, patient follow-ups, and care support conversations.

## Key Differentiators

- **Bilingual:** Flawless English/Hindi switching
- **Telephony-first:** Tuned VAD for cellular/background noise
- **Multi-provider:** Twilio, Plivo (India proxy + Zentrunk), Zadarma, VoiceLink
- **Self-healing:** Zombie cleanup, capacity management, per-trunk gating, crash alerts with memes
- **Inbound + outbound:** Supports both call directions with DB-based context resolution
- **Multi-KB per org:** Each org can have multiple KB collections loaded from documents

## Current Limitations

- 3-minute maximum call duration
- No human call transfer (code present but disabled)
- Manual testing only
- Single admin user
