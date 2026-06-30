# Project Summary

## Mantra Voice Agent

A production-grade, low-latency bilingual (English/Hindi) voice AI agent for outbound telephony. Built on LiveKit Cloud, it orchestrates Deepgram STT → LLM (OpenAI/Gemini/DeepSeek) → Cartesia TTS in real time.

## Purpose

Professional care support and automated outbound follow-up calls for MantraCare/MantraAssist. Handles appointment scheduling, patient follow-ups, and care support conversations.

## Key Differentiators

- **Bilingual:** Flawless English/Hindi switching
- **Telephony-first:** Tuned VAD for cellular/background noise
- **Multi-provider:** Twilio, Plivo (India proxy), Zadarma
- **Self-healing:** Zombie cleanup, capacity management, crash alerts with memes

## Current Limitations

- 3-minute maximum call duration
- No human call transfer (code present but disabled)
- Manual testing only
- Single admin user
