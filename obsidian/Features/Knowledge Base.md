# Knowledge Base

**Module:** `mantra/knowledge_base.py` + `mantra/retriever.py`
**Status:** Implemented (see gaps below)
**Storage:** PostgreSQL Full-Text Search (FTS) — `tsvector` + `websearch_to_tsquery`

---

## Overview

A knowledge base system for the LKT voice agent. Accepts content from **files, pasted text blocks, and URLs**, chunks it adaptively, and stores it in PostgreSQL.

**RAG via Function Tool:** The KB is NOT injected upfront into the system prompt. Instead, the LLM has a `search_knowledge_base` function tool available during the call. When it needs factual information, it calls this tool, which runs a PostgreSQL Full-Text Search and returns results inline. This keeps the prompt small and avoids context window limits.

**Multi-KB architecture:** Every page is tagged with a `kb_id`. The inbound call resolution provides the `kb_id` and optional `kb_tags` — the agent only searches those KBs. One table, column-level isolation, many clients.

## Key Files

| File | Path | Role |
|------|------|------|
| `knowledge_base.py` | `mantra/knowledge_base.py` (461 lines) | Core: `PostgresKnowledgeBase` abstract interface + FTS implementation, adaptive chunker, ingestion helpers |
| `retriever.py` | `mantra/retriever.py` (50 lines) | `KnowledgeRetriever` wrapping `kb.search()` with in-memory per-session cache |
| `agent.py` | `mantra/agent.py` (1513 lines) | `search_knowledge_base` tool registration (line 326-338), KB scope resolution from inbound context (lines 424-490) |
| `ui_server.py` | `mantra/ui_server.py` (2143 lines) | Ingestion endpoints (`/api/v1/kb/ingest`), test chat (`/api/v1/kb/chat`), deletion (`/api/v1/kb/document`) |

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
  │  kb_pages table (tsvector FTS index)    │
  │  Full-Text Search via websearch_to_tsquery│
  │  + ts_rank() relevance scoring           │
  └──────────────┬──────────────────────────┘
                 │
                 ▼  (call starts)
  ┌─ Function Tool RAG ─────────────────────┐
  │  1. LLM calls search_knowledge_base()   │
  │  2. retriever → kb.search() → FTS       │
  │  3. Results formatted → returned to LLM │
  └─────────────────────────────────────────┘
```

## Inbound Call KB Resolution

When an inbound call arrives, the agent resolves scope before the conversation starts (`agent.py:424-490`):

1. `resolve_inbound_context(phone_number)` tries `MANTRAASSIST_BACKEND_URL/api/v1/telephony/resolve-inbound-call` first
2. **If backend fails** (or `LOCAL_INBOUND_MAPPINGS=1` is set), falls back to `inbound_mappings.json` in the repo root
3. Backend (or local config) returns `org_id`, `kb_id`, `kb_tags`, `prompt`, `voice`, `model`, `process_id`, `transfer_numbers`, `client_name`
4. KB scope is built:
   - `org_id` is always appended as a `kb_id`
   - `kb_id` from payload is appended if present
   - `kb_ids[]` from payload is extended if present
   - `kb_tags[]` from payload is extended if present
5. Scope is passed to `AssistantFunctions(kb_ids=..., kb_tags=...)`
6. During the call, `search_knowledge_base` uses these `kb_ids` and `kb_tags` to filter searches

Previously the call was **rejected** if the backend was unreachable; now it falls back to local mappings.

## Ingestion Channels

| Endpoint | Input | kb_id? |
| -------- | ----- | ------ |
| `POST /api/v1/kb/ingest` | File (`.pdf`, `.txt`, `.md`) + kb_id + optional `document_id` | Required |
| `POST /api/v1/kb/ingest` (JSON) | `{kb_id, title, content}` | Required |
| `POST /api/v1/kb/ingest` (URL) | `{kb_id, url}` | Required |
| `DELETE /api/v1/kb/document` | `{kb_id, document_id}` | Required |

## Schema

```sql
CREATE TABLE kb_pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_type     TEXT NOT NULL,            -- 'file', 'text', 'url'
    page_meta       JSONB DEFAULT '{}',       -- chunking strategy, heading path, token count, tags_name, document_id
    content_in_text TEXT NOT NULL,            -- text content for LLM consumption
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    text_search     tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content_in_text, ''))
                    ) STORED
);

CREATE INDEX idx_kb_pages_kb_id ON kb_pages (kb_id);
CREATE INDEX idx_kb_pages_fts ON kb_pages USING GIN (text_search);
```

**Note:** There is NO `vector` extension or `embedding` column. The docstring and README mention "pgvector with OpenAI embeddings" but this was never implemented — the system uses pure Full-Text Search.

## Tag Filtering

The `kb_tags` feature enables sub-scoping within a KB. Tags are stored as a `tags_name` JSONB array in `page_meta`. Search supports two tag formats:
- JSONB array: `page_meta->'tags_name' ?| $tags`
- JSONB string: `page_meta->>'tags_name' = ANY($tags)`

This allows the backend to define tags like `["sales", "pricing"]` and the agent to search only within those tagged pages.

## Key Decisions

- **Function Tool RAG (not upfront injection):** KB content is retrieved on-demand via a function tool, not injected into the system prompt. This keeps prompt size manageable. (There is a commented-out comment `# Removed query_knowledge_base tool` at line 370 suggesting a prior approach was merged into the job context then reverted.)
- **Full-Text Search (not vector/embedding):** PostgreSQL `websearch_to_tsquery` + `ts_rank` provides usable search without any external embedding API or vector database dependency. Semantic/embedding search remains a future upgrade path.
- **kb_id column filter:** Single table, column-level isolation, simple queries.
- **In-memory per-call cache:** `KnowledgeRetriever.session_cache` deduplicates repeated queries within a single call session.
- **No embedding env vars set:** `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `KB_SIMILARITY_THRESHOLD`, `KB_MAX_CHUNK_TOKENS` are documented in README but **not configured** in `.env` or `.env.local`.

## Known Gaps

1. **No vector/embedding search** — Despite docs claiming pgvector, the system uses pure FTS. Semantic search would require adding the `pgvector` extension, generating embeddings via OpenAI API, and adding a vector column.
2. **No upfront prompt injection** — The Obsidian vault previously described "Zero-Latency Prompt Injection" but this was never implemented. The function-tool approach relies on the LLM choosing to call the tool.
3. **LLM-dependent KB usage** — The KB is only queried if the LLM decides to call `search_knowledge_base`. There is no forced/automatic KB retrieval.
4. **No fallback blending** — If the KB returns no results, the LLM gets a `"No relevant information found"` message and falls back to its own knowledge.

## Guardrails & Factual Overrides

To prevent hallucinations while still answering factual questions effectively, the prompt includes a **5-Rule Absolute Override Framework**:
1. **Mandatory Factual Answers:** Forces the agent to answer factual questions *before* guiding the user back to the call flow.
2. **Primary Source Constraint:** Forces the agent to use only KB content for specific facts (services, treatments, pricing, policies) without inventing information.
3. **Factual Explanation vs. Personalized Advice:** Authorizes the agent to explain conditions or symptoms purely based on KB text, but explicitly bans applying this knowledge to diagnose the user.
4. **General Knowledge Fallback:** Allows the agent to answer completely unrelated general questions neutrally if not in the KB.
5. **No Source-Citing Language:** Prevents the agent from breaking character by saying "According to my knowledge base...".
