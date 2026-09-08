# Knowledge Base

**Module:** `mantra/knowledge_base.py` + `mantra/retriever.py` + `mantra/gemini_embeddings.py`
**Status:** Implemented — Hybrid FTS + pgvector semantic search (2026-08-09)
**Storage:** PostgreSQL Full-Text Search (`english` tsvector) + pgvector (`embedding vector(1536)`, HNSW index) via Google Gemini `gemini-embedding-2`

---

## Overview

A knowledge base system for the LKT voice agent. Accepts content from **files, pasted text blocks, and URLs**, chunks it adaptively, and stores it in PostgreSQL.

**RAG via Function Tool:** The KB is NOT injected upfront into the system prompt. Instead, the LLM has a `search_knowledge_base` function tool available during the call. When it needs factual information, it calls this tool, which runs a tiered search (semantic + FTS) and returns results inline. This keeps the prompt small and avoids context window limits.

**Multi-KB architecture:** Every page is tagged with a `kb_id`. The inbound call resolution provides the `kb_id` and optional `kb_tags` — the agent only searches those KBs. One table, column-level isolation, many clients.

## Key Files

| File | Path | Role |
|------|------|------|
| `knowledge_base.py` | `mantra/knowledge_base.py` | Core: `PostgresKnowledgeBase` abstract interface + FTS/vector implementation, adaptive chunker, ingestion helpers, collection management, tiered query builders, RRF blending |
| `retriever.py` | `mantra/retriever.py` | `KnowledgeRetriever` wrapping `kb.search()` with in-memory per-session cache, tiered fallback, available-docs listing on no-results |
| `gemini_embeddings.py` | `mantra/gemini_embeddings.py` | Google Gemini embedding client (`gemini-embedding-2`, 1536 dims), batched + retrying |
| `agent.py` | `mantra/agent.py` | `search_knowledge_base` tool registration, KB scope resolution from inbound context |

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
                 │ chunks (embedded at ingest)
                 ▼
  ┌─ PostgreSQL ────────────────────────────┐
  │  kb_pages table                         │
  │  • text_search: tsvector, `english` FTS │
  │  • embedding: vector(1536) + HNSW idx   │
  │  Tiered search:                         │
  │    A. strict FTS + vector (RRF blend)   │
  │    B. loose OR query                    │
  │    C. tag-only                          │
  │    D. list_available() doc listing      │
  └──────────────┬──────────────────────────┘
                 │
                 ▼  (call starts)
  ┌─ Function Tool RAG ─────────────────────┐
  │  1. LLM calls search_knowledge_base()   │
  │  2. retriever → kb.search() → tiers     │
  │  3. Results (or doc listing) formatted  │
  │     → returned to LLM                   │
  └─────────────────────────────────────────┘
```

### Tiered Retrieval (fixes "no results despite data existing")

The 2026-08-09 fix added fallback tiers so inflections and near-misses still resolve:

1. **Tier A — strict semantic+FTS:** `websearch_to_tsquery('english', query)` AND-matched pages, blended with pgvector cosine similarity via Reciprocal Rank Fusion (`_blend_results`). `english` config stems inflections (`diagnostic codes` → matches `diagnostic code`).
2. **Soft tag retry:** if a `tags` filter returns nothing, retry untagged before descending (in `retriever.py`).
3. **Tier B — loose OR:** each token OR'd so partial matches still surface.
4. **Tier C — tag-only:** match on tags alone.
5. **Tier D — document listing:** if nothing matches, `list_available()` returns the titles + tags of documents in scope, so the LLM answers truthfully or asks a sharper question instead of claiming nothing exists.

Embeddings are computed only if `GOOGLE_API_KEY` is set; otherwise the system degrades to FTS-only gracefully.

## Inbound Call KB Resolution

When an inbound call arrives, the agent resolves scope before the conversation starts (`agent.py:424-490`):

1. `resolve_inbound_context(phone_number)` tries `MANTRAASSIST_BACKEND_URL/v1/telephony/resolve-inbound-call` first
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
| `POST /v1/kb/ingest` | File (`.pdf`, `.txt`, `.md`) + kb_id + optional `document_id` | Required |
| `POST /v1/kb/ingest` (JSON) | `{kb_id, title, content}` | Required |
| `POST /v1/kb/ingest` (URL) | `{kb_id, url}` | Required |
| `DELETE /v1/kb/document` | `{kb_id, document_id}` | Required |

Ingestion embeds each chunk up front (batched, via `gemini_embeddings.embed_texts`) and stores the vector; on any embedding failure it falls back to FTS-only so ingestion never blocks on the API.

## Schema

```sql
CREATE TABLE kb_pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_type     TEXT NOT NULL,            -- 'file', 'text', 'url'
    page_meta       JSONB DEFAULT '{}',       -- chunking strategy, heading path, token count, tags_name, document_id, process_stage_data
    content_in_text TEXT NOT NULL,            -- text content for LLM consumption
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    text_search     tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content_in_text, ''))
                    ) STORED,
    embedding       vector(1536)              -- Gemini gemini-embedding-2 @ 1536 dims (pgvector cap = 2000)
);

CREATE INDEX idx_kb_pages_kb_id ON kb_pages (kb_id);
CREATE INDEX idx_kb_pages_fts ON kb_pages USING GIN (text_search);
CREATE INDEX idx_kb_pages_embedding ON kb_pages USING hnsw (embedding vector_cosine_ops);
```

**Migrated 2026-08-09:** `text_search` was rebuilt from `simple` → `english` (fixes stemming: `diagnostic codes` now matches `diagnostic code`). `embedding vector(1536)` + HNSW index added. Requires the `vector` extension. Backfill existing rows via `tools/backfill_embeddings.py` (idempotent/resumable, `--dry-run` to preview).

## Tag Filtering

The `kb_tags` feature enables sub-scoping within a KB. Tags are stored as a `tags_name` JSONB array in `page_meta`. Search supports two tag formats:
- JSONB array: `page_meta->'tags_name' ?| $tags`
- JSONB string: `page_meta->>'tags_name' = ANY($tags)`

This allows the backend to define tags like `["sales", "pricing"]` and the agent to search only within those tagged pages.

## Key Decisions

- **Function Tool RAG (not upfront injection):** KB content is retrieved on-demand via a function tool, not injected into the system prompt. This keeps prompt size manageable. (There is a commented-out comment `# Removed query_knowledge_base tool` at line 370 suggesting a prior approach was merged into the job context then reverted.)
- **Hybrid FTS + semantic (2026-08-09):** `english` FTS is the base tier; pgvector cosine similarity is blended in via RRF. Semantic search complements FTS rather than replacing it — no match ever depends on embeddings alone.
- **`english` config for FTS:** the previous `simple` config had no stemming, so `diagnostic codes` failed to match `diagnostic code` (the reported org 77 bug). `english` stems both sides.
- **1536-dim embeddings via Google Gemini `gemini-embedding-2`:** 1536 was chosen because pgvector HNSW/IVFFlat indexes cap at 2000 dimensions; Gemini supports `output_dimensionality=1536`. (3072 native dims cannot be HNSW-indexed.)
- **No OpenAI / no local embedding infra:** OpenAI key was unavailable; DeepSeek has no embedding API; a local embedding server was rejected to avoid Docker bloat. Gemini key already present in `.env.local`.
- **Graceful degradation:** missing `GOOGLE_API_KEY`, empty embedding column, or failed embed calls all fall back to FTS-only — the KB never becomes unusable because of the embedding layer.
- **kb_id column filter:** Single table, column-level isolation, simple queries.
- **In-memory per-call cache:** `KnowledgeRetriever.session_cache` deduplicates repeated queries within a single call session.
- **Truthful no-results:** when nothing matches, the retriever returns the list of available documents (with tags) instead of a bare "no relevant information", so the LLM doesn't hallucinate or claim the KB is empty.

## Known Gaps

1. **Embeddings require backfill for existing rows** — the migration adds the column but pre-existing rows have `embedding IS NULL` until `tools/backfill_embeddings.py` runs. FTS covers them meanwhile.
2. **No upfront prompt injection** — The Obsidian vault previously described "Zero-Latency Prompt Injection" but this was never implemented. The function-tool approach relies on the LLM choosing to call the tool.
3. **LLM-dependent KB usage** — The KB is only queried if the LLM decides to call `search_knowledge_base`. There is no forced/automatic KB retrieval.

## Guardrails & Factual Overrides

To prevent hallucinations while still answering factual questions effectively, the prompt includes a **5-Rule Absolute Override Framework**:
1. **Mandatory Factual Answers:** Forces the agent to answer factual questions *before* guiding the user back to the call flow.
2. **Primary Source Constraint:** Forces the agent to use only KB content for specific facts (services, treatments, pricing, policies) without inventing information.
3. **Factual Explanation vs. Personalized Advice:** Authorizes the agent to explain conditions or symptoms purely based on KB text, but explicitly bans applying this knowledge to diagnose the user.
4. **General Knowledge Fallback:** Allows the agent to answer completely unrelated general questions neutrally if not in the KB.
5. **No Source-Citing Language:** Prevents the agent from breaking character by saying "According to my knowledge base...".
