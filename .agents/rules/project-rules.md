---
trigger: always_on
description: Reglas Obligatorias y Anti-Patrones — lkt
---
# Reglas Obligatorias del Proyecto (lkt)
Estas reglas han sido registradas en Obsidian Second Brain y son de OBLIGATORIO CUMPLIMIENTO en cada intervencion del agente en este workspace:
## Condiciones y Flujo de Trabajo
1. **Prioridad Estricta de Voces del Backend:** Si una llamada u organización especifica una voz en la base de datos o en el payload de despacho, debe preservarse de forma absoluta; las voces del catálogo de países son estrictamente de respaldo.
2. **Desacoplamiento de Idioma y org_id:** La resolución de país e idioma del número de teléfono debe ejecutarse de forma independiente de la búsqueda de `org_id` para garantizar cero latencia en el saludo inicial.

## Anti-Patrones y Trampas Prohibidas (Errores Recurrentes)
1. **Saludos Regionales Hardcodeados:** Prohibido escribir presentaciones estáticas en prompts base como "Arushi from MantraCare" o "calling from India".
2. **Suposición de Idioma Único:** Prohibido asumir que todas las llamadas son en inglés o español sin antes consultar la herramienta MCP `resolve_caller_country_language`.
