---
title: "Skill: langgraph-agent-dev"
type: antigravity-skill
scope: project
project: "assist-ai"
workspace_dir: "/home/fardeen/assist-ai"
tags:
  - antigravity/skill
  - antigravity/skill/project
updated: 2026-11-13
---

# Skill: langgraph-agent-dev (Proyecto: assist-ai)

> [!INFO] **Metadatos de la Skill**
> - **Nombre**: `langgraph-agent-dev`
> - **Proyecto**: `assist-ai`
> - **Carpeta Local**: `/home/fardeen/assist-ai`
> - **Archivo Fuente**: `/home/fardeen/assist-ai/.agents/skills/langgraph-agent-dev/SKILL.md`
> - **Descripción**: Architecture, conventions, and implementation guide for building LangGraph agents in assist-ai.

## Instrucciones del Agente

# LangGraph Agent Development Skill

Use this skill when developing or extending conversational agents, supervisor graphs, or custom tool nodes in `backend/app/modules/assistant`.

## 1. Graph Architecture
The Assistant uses a `StateGraph` consisting of:
1. **Safety Evaluator**: Evaluates user input for emergencies, harmful requests, or out-of-scope clinical diagnoses.
2. **Supervisor / Reasoner Node**: Calls the LLM via LiteLLM gateway with bound tool definitions.
3. **Tool Execution Node**: Executes permitted tools asynchronously (with `RunnableConfig` containing tenant context `org_id` and `user_token`).
4. **Responder Node**: Generates the final grounded clinical response with citation metadata.

## 2. Tool Implementation Pattern
Every tool must be decorated with `@tool` from `langchain_core.tools`, take `RunnableConfig`, and extract the request context:
```python
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig


@tool
async def check_provider_availability(user_id: int, date: str, config: RunnableConfig) -> str:
    """Check open appointment slots for a specific healthcare provider on a given date.

    Args:
        user_id: Provider's system user ID.
        date: Date in YYYY-MM-DD format.
    """
    conf = config.get("configurable", {})
    org_id = conf.get("org_id")
    user_token = conf.get("user_token")

    if not user_token:
        return json.dumps({"error": "Authentication token missing"})

    client = MABackendClient()
    slots = await client.get_available_slots(
        user_token=user_token, user_id=user_id, date=date, org_id=org_id
    )
    return json.dumps({"slots": slots})
```

## 3. Streaming Chat Endpoint
Chat endpoints stream Server-Sent Events (`text/event-stream`) to frontends. Send delta tokens as:
```json
data: {"type": "content", "content": "chunk text"}
data: {"type": "tool_call", "name": "check_provider_availability", "args": {...}}
data: {"type": "done"}
```

---
*Conexiones del Grafo:* [[00 Antigravity Hub]] | [[00 Indice de Skills]] | [[Proyecto: assist-ai]]
