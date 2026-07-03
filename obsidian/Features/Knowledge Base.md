# Knowledge Base

**Planned Module:** `mantra/knowledge_base.py`
**Status:** Implemented
**Storage:** PostgreSQL + pgvector (OpenAI text-embedding-3-small)

---

## Overview

A knowledge base system for the LKT voice agent. Accepts content from **files, pasted text blocks, and URLs**, chunks it adaptively, and stores it in PostgreSQL. 

**Zero-Latency Prompt Injection:** Instead of relying on traditional tool-calling/RAG mid-conversation (which adds latency), the agent fetches the entire content of the specified knowledge base **upfront** during initialization. The text is injected directly into the agent's main system prompt (`initial_instructions`), granting the agent immediate, zero-latency recall over the information.

**Multi-KB architecture:** Every page is tagged with a `kb_id`. The call payload specifies which KB to use — the agent only fetches content for that specific `kb_id`. One table, column-level isolation, many clients.

## Key Files

- `mantra/knowledge_base.py` — Core module: KnowledgeBase class, adaptive chunker, Postgres connections.
- `mantra/agent.py` — `entrypoint` intercepts `kb_id` from the payload, fetches all related records from the database, and injects them into the system prompt.
- `mantra/ui_server.py` — Ingestion endpoints, delete endpoints, and the `/api/v1/knowledge/list` endpoint for the UI.
- `static/kb_chat.html` & `dashboard.js` — Frontend UI to test and select `kb_id`s dynamically.

## Architecture

```
Upload / Paste / URL
        │
        ▼
  ┌─ Adaptive Chunker ─────────────────────┐
  │  Auto-detect structure:                 │
  │  • Heading-based  (if #, ##, Section)   │
  │  • Paragraph-based (if clear paragraphs) │
  │  • Sliding-window (fallback)            │
  └──────────────┬──────────────────────────┘
                 │ chunks
                 ▼
  ┌─ PostgreSQL ────────────────────────────┐
  │  kb_pages table with kb_id column       │
  │  (Embeddings generated and stored for   │
  │  future scalability)                    │
  └──────────────┬──────────────────────────┘
                 │
                 ▼  (call starts)
  ┌─ Upfront Prompt Injection ──────────────┐
  │  1. SELECT * WHERE kb_id = X            │
  │  2. Append all text to Agent Prompt     │
  │  3. Agent answers instantly             │
  └─────────────────────────────────────────┘
```

## Ingestion Channels

| Endpoint                                 | Input                                | kb_id?   |
| ---------------------------------------- | ------------------------------------ | -------- |
| `POST /api/v1/knowledge/upload`          | File (`.pdf`, `.txt`, `.md`) + kb_id | Required |
| `POST /api/v1/knowledge/text`            | JSON `{kb_id, title, content}`       | Required |
| `POST /api/v1/knowledge/url`             | JSON `{kb_id, url}`                  | Required |
| `GET /api/v1/knowledge/list`             | None                                 | N/A      |
| `DELETE /api/v1/knowledge/{page_id}`     | Path param                           | N/A      |
| `DELETE /api/v1/knowledge/by-kb/{kb_id}` | Path param                           | N/A      |

## Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE kb_pages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id       TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    source_type TEXT NOT NULL,            -- 'file', 'text', 'url'
    source_ref  TEXT,                     -- original filename or URL
    embedding   vector(1536),             -- Stored for future hybrid search scaling
    page_meta   JSONB DEFAULT '{}',       -- chunking strategy, heading path, token count, etc.
    content_in_text TEXT NOT NULL,        -- text content for LLM consumption
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_kb_pages_kb_id ON kb_pages (kb_id);
```

## Payload Routing

The call payload carries `kb_id`. Agent receives it at dispatch:

```python
# In agent.py entrypoint
payload = json.loads(ctx.job.metadata)
kb_id = payload.get("kb_id")

# Fetch all KB data upfront
rows = await conn.fetch("SELECT title, content_in_text FROM kb_pages WHERE kb_id = $1", kb_id)
for r in rows:
    initial_instructions += f"--- {r['title']} ---\n{r['content_in_text']}\n\n"
```

The agent never sees other KBs — every query has `WHERE kb_id = kb_id`.

## Key Decisions

- **Upfront Context Injection** — Moved away from mid-call RAG/tool-calling to guarantee zero latency. Voice agents cannot afford tool-call execution delays.
- **pgvector retained for scalability** — Embeddings are still generated (OpenAI text-embedding-3-small) and stored in case KBs grow too large for a single prompt context, allowing for easy rollback to hybrid RAG if needed.
- **kb_id column filter** — Single table, column-level isolation, simple queries.
- **Dynamic Frontend Integration** — The test console UI actively polls `/api/v1/knowledge/list` to populate a dropdown menu for testing different clients.
- **Markdown Rendering** — Agent responses are rendered using `marked.js` in the test UI to handle properly formatted lists and bolding directly from the LLM.
