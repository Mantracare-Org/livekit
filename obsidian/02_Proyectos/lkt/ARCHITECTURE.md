---
project: "lkt"
type: hermes-architecture
updated: 2026-11-19
stack: "Python (FastAPI)"
---

# Arquitectura — lkt

## Stack Tecnológico y Entorno
- **Ruta local**: `/home/fardeen/lkt`
- **Stack**: Python (FastAPI)
- **Framework / Core**: `Python (FastAPI)`
- **Versión**: `1.0.0`
- **Dependencias clave**: Ninguna
- **Skill Asociada**: —

## Decisiones de Diseño y Estructura
Production-grade real-time conversational AI voice agent and telephony orchestrator for inbound and outbound healthcare coordination, appointment management, an

## Reglas y Condiciones Obligatorias del Proyecto
1. **Prioridad Estricta de Voces del Backend:** Si una llamada u organización especifica una voz en la base de datos o en el payload de despacho, debe preservarse de forma absoluta; las voces del catálogo de países son estrictamente de respaldo.
2. **Desacoplamiento de Idioma y org_id:** La resolución de país e idioma del número de teléfono debe ejecutarse de forma independiente de la búsqueda de `org_id` para garantizar cero latencia en el saludo inicial.

## Anti-Patrones y Trampas Prohibidas del Proyecto
1. **Saludos Regionales Hardcodeados:** Prohibido escribir presentaciones estáticas en prompts base como "Arushi from MantraCare" o "calling from India".
2. **Suposición de Idioma Único:** Prohibido asumir que todas las llamadas son en inglés o español sin antes consultar la herramienta MCP `resolve_caller_country_language`.
