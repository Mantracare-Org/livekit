---
project: "support-bot"
type: hermes-architecture
updated: 2026-11-14
stack: "Python (FastAPI)"
---

# Arquitectura — support-bot

## Stack Tecnológico y Entorno
- **Ruta local**: `/home/fardeen/support-bot`
- **Stack**: Python (FastAPI)
- **Framework / Core**: `Python (FastAPI)`
- **Versión**: `1.0.0`
- **Dependencias clave**: Ninguna
- **Skill Asociada**: —

## Decisiones de Diseño y Estructura
This repository contains the backend implementation of the AI Support Agent for Mantra Care. It provides comprehensive endpoints built with **FastAPI** to facil

## Reglas y Condiciones Obligatorias del Proyecto
- `[2026-11-02]` [support-bot] Never expose Mantraback URLs in support-bot; support-bot is purely a service/receiver queried by Mantraback via SUPPORT_CHAT_BOT_URL (Physio pattern).

## Anti-Patrones y Trampas Prohibidas del Proyecto
<!-- Trampas técnicas, errores a evitar y prácticas prohibidas en este repositorio -->
