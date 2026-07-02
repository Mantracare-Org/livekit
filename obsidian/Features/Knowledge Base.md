# Knowledge Base

**Planned Module:** `mantra/knowledge_base.py`
**Status:** Design Phase
**Storage:** PostgreSQL + pgvector (OpenAI text-embedding-3-small)

---

## Overview

A vector knowledge base system for the LKT voice agent. Accepts content from **files, pasted text blocks, and URLs**, chunks it adaptively, embeds each chunk via OpenAI, stores in PostgreSQL with pgvector, and exposes retrieval through a function tool the agent calls mid-conversation.

**Multi-KB architecture:** Every page is tagged with a `kb_id`. The call payload specifies which KB to use — the agent only queries that KB. One table, column-level isolation, many agencies.

## Key Files

- `mantra/knowledge_base.py` — Core module: KnowledgeBase class, adaptive chunker, embedding pipeline, vector search
- `mantra/agent.py` — `AssistantFunctions` gets a new `query_knowledge_base` tool + `kb_id` from payload
- `mantra/ui_server.py` — Three ingestion endpoints + two delete endpoints

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
  ┌─ Embedding Pipeline ────────────────────┐
  │  Each chunk → OpenAI text-embedding-3   │
  │  → vector(1536) per chunk              │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  ┌─ PostgreSQL + pgvector ─────────────────┐
  │  kb_pages table with kb_id column       │
  │  IVFFlat/HNSW index on embedding       │
  └──────────────┬──────────────────────────┘
                 │
                 ▼  (live call)
  ┌─ Agent Function Tool ───────────────────┐
  │  1. Embed search_text                   │
  │  2. Cosine distance WHERE kb_id = X     │
  │  3. Return pages above threshold        │
  └─────────────────────────────────────────┘
```

## Ingestion Channels

| Endpoint                                 | Input                                | kb_id?   |
| ---------------------------------------- | ------------------------------------ | -------- |
| `POST /api/v1/knowledge/upload`          | File (`.pdf`, `.txt`, `.md`) + kb_id | Required |
| `POST /api/v1/knowledge/text`            | JSON `{kb_id, title, content}`       | Required |
| `POST /api/v1/knowledge/url`             | JSON `{kb_id, url}`                  | Required |
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
    embedding   vector(1536),
    page_meta   JSONB DEFAULT '{}',       -- chunking strategy, heading path, token count, etc.
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_kb_pages_kb_id ON kb_pages (kb_id);
CREATE INDEX idx_kb_pages_embedding ON kb_pages
    USING hnsw (embedding vector_cosine_ops);
```

## Payload Routing

The call payload now carries `kb_id`. Agent receives it at dispatch:

```python
# In agent.py entrypoint
payload = json.loads(ctx.job.metadata)
kb_id = payload.get("kb_id", "default")

# Injected into AssistantFunctions
fnc_ctx = AssistantFunctions(kb_id=kb_id, ...)
```

The agent never sees other KBs — every query has `WHERE kb_id = self.kb_id`.

## Agent Tool

```python
@llm.function_tool(
    description="Search the knowledge base for information relevant to the user's question. "
                "Call this when the user asks something you don't know."
)
async def query_knowledge_base(
    search_text: Annotated[str, "The search query"],
    top_k: Annotated[int, "Number of results (default 3)"] = 3
) -> str:
    """Embed search_text → cosine distance query filtered by self.kb_id → return top_k"""
```

## Key Decisions

- **Vector search** with pgvector — existing Postgres, no new infrastructure.
- **OpenAI text-embedding-3-small** — 1536 dim, cheap, reliable.
- **Ingest-time embedding** — uploads accept latency; calls cannot.
- **kb_id column filter** — single table, column-level isolation, simple queries.
- **Adaptive chunking** — three-strategy fallback: heading → paragraph → sliding-window.
- **Synchronous ingestion** — simpler than a job queue.

## Open Questions

- [ ] Payload field name for KB? (placeholder: `kb_id`)
- [ ] ivfflat vs hnsw index?
- [ ] Similarity threshold? (default: 0.7)
- [ ] Max chunk size in tokens?
- [ ] Bilingual handling for embeddings?

## Dependencies to Add

- `pgvector` extension on Postgres
- `openai` Python SDK (for embedding API calls)
- `pypdf` (file parsing)
- `trafilatura` (URL text extraction)

## Environment Variables

- `EMBEDDING_MODEL` — OpenAI embedding model (default: `text-embedding-3-small`)
- `EMBEDDING_API_KEY` — API key for embedding (defaults to OPENAI_API_KEY)
- `KB_SIMILARITY_THRESHOLD` — Minimum cosine similarity for results (default: 0.7)
- `KB_MAX_CHUNK_TOKENS` — Max tokens per chunk before sub-chunking (default: 2000)
