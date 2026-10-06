"""
Knowledge Base for LKT Voice Agent.

PostgreSQL Full-Text Search (tsvector) with multi-KB isolation via kb_id column filtering.
Supports tag-based sub-scoping via JSONB tags_name in page_meta.
"""

import json
import uuid
import logging
import os
from typing import Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

import asyncpg
from pypdf import PdfReader
import trafilatura
import asyncio

logger = logging.getLogger("mantra.knowledge_base")


FTS_CONFIG = os.getenv("KB_FTS_CONFIG", "english")


def build_search_query(use_generated_column: bool = True) -> str:
    """Build the KB FTS search SQL using either the generated text_search column or a direct expression."""
    vector_expr = "text_search" if use_generated_column else f"to_tsvector('{FTS_CONFIG}', coalesce(title, '') || ' ' || coalesce(content_in_text, ''))"
    return f"""
        SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
               ts_rank({vector_expr}, websearch_to_tsquery('{FTS_CONFIG}', $2)) as similarity
        FROM kb_pages
        WHERE (kb_id = ANY($1::text[]) OR page_meta->>'org_id' = ANY($1::text[]))
          AND {vector_expr} @@ websearch_to_tsquery('{FTS_CONFIG}', $2)
          AND ($4::text[] IS NULL OR 
              (jsonb_typeof(page_meta->'tags_name') = 'array' AND page_meta->'tags_name' ?| $4::text[]) OR
              (jsonb_typeof(page_meta->'tags_name') = 'string' AND page_meta->>'tags_name' = ANY($4::text[]))
          )
        ORDER BY similarity DESC
        LIMIT $3
    """


def build_loose_search_query(use_generated_column: bool = True) -> str:
    """
    Loose (OR) FTS search: matches pages containing ANY of the query terms.
    Used as Tier B fallback when strict AND finds nothing.
    """
    vector_expr = "text_search" if use_generated_column else f"to_tsvector('{FTS_CONFIG}', coalesce(title, '') || ' ' || coalesce(content_in_text, ''))"
    return f"""
        SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
               ts_rank({vector_expr}, plainto_tsquery('{FTS_CONFIG}', $2)) as similarity
        FROM kb_pages
        WHERE (kb_id = ANY($1::text[]) OR page_meta->>'org_id' = ANY($1::text[]))
          AND {vector_expr} @@ plainto_tsquery('{FTS_CONFIG}', $2)
          AND ($4::text[] IS NULL OR 
              (jsonb_typeof(page_meta->'tags_name') = 'array' AND page_meta->'tags_name' ?| $4::text[]) OR
              (jsonb_typeof(page_meta->'tags_name') = 'string' AND page_meta->>'tags_name' = ANY($4::text[]))
          )
        ORDER BY similarity DESC
        LIMIT $3
    """


def build_vector_search_query() -> str:
    """Semantic (pgvector) search: cosine distance on the embedding column."""
    return """
        SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
               1 - (embedding <=> $2::vector) as similarity
        FROM kb_pages
        WHERE kb_id = ANY($1::text[])
          AND embedding IS NOT NULL
          AND ($4::text[] IS NULL OR 
              (jsonb_typeof(page_meta->'tags_name') = 'array' AND page_meta->'tags_name' ?| $4::text[]) OR
              (jsonb_typeof(page_meta->'tags_name') = 'string' AND page_meta->>'tags_name' = ANY($4::text[]))
          )
        ORDER BY embedding <=> $2::vector
        LIMIT $3
    """


def build_tag_search_query() -> str:
    """Tag-only match: pages whose tags_name overlap the requested tags (Tier C)."""
    return """
        SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
               1.0 as similarity
        FROM kb_pages
        WHERE kb_id = ANY($1::text[])
          AND (
              (jsonb_typeof(page_meta->'tags_name') = 'array' AND page_meta->'tags_name' ?| $2::text[]) OR
              (jsonb_typeof(page_meta->'tags_name') = 'string' AND page_meta->>'tags_name' = ANY($2::text[]))
          )
        ORDER BY created_at DESC
        LIMIT $3
    """


def build_list_docs_query() -> str:
    """List all pages (title + tags only) in the scoped KBs for Tier D fallback."""
    return """
        SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
               0.0 as similarity
        FROM kb_pages
        WHERE (kb_id = ANY($1::text[]) OR page_meta->>'org_id' = ANY($1::text[]))
        ORDER BY created_at DESC
        LIMIT $2
    """


# ---- Models ----


@dataclass
class KnowledgePage:
    id: str
    kb_id: str
    title: str
    content: str
    source_type: str
    page_meta: dict
    content_in_text: str
    created_at: Optional[str] = None
    embedding: Optional[list] = None


def _embedding_to_text(vec: list[float]) -> str:
    """Serialize a float list into pgvector's text literal form ('[a,b,c]')."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def _blend_results(
    fts_pages: list[KnowledgePage],
    vec_pages: list[KnowledgePage],
    top_k: int,
    fts_weight: float = 0.5,
) -> list[KnowledgePage]:
    """
    Reciprocal Rank Fusion (RRF) of FTS + vector results.
    Produces a stable, well-ranked blend regardless of score scale differences.
    """
    scores: dict[str, float] = {}
    page_by_id: dict[str, KnowledgePage] = {}

    for rank, page in enumerate(fts_pages):
        scores[page.id] = scores.get(page.id, 0.0) + fts_weight / (60 + rank)
        page_by_id[page.id] = page
    for rank, page in enumerate(vec_pages):
        scores[page.id] = scores.get(page.id, 0.0) + (1.0 - fts_weight) / (60 + rank)
        page_by_id[page.id] = page

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [page_by_id[pid] for pid, _ in ranked[:top_k]]


# ---- Abstract Storage Interface ----


class KnowledgeBase(ABC):
    """Abstract storage interface for knowledge bases."""

    @abstractmethod
    async def add_page(self, page: KnowledgePage) -> str:
        """Add a page, return its ID."""
        pass

    @abstractmethod
    async def search(
        self,
        kb_ids: list[str],
        query: str,
        top_k: int = 3,
        tags: Optional[list[str]] = None,
    ) -> list[KnowledgePage]:
        """Search within a KB (full-text + semantic when available), with optional metadata tag filtering."""
        pass

    @abstractmethod
    async def list_available(self, kb_ids: list[str], top_k: int = 10) -> list[KnowledgePage]:
        """List pages available in the scoped KBs (title + tags) for graceful no-match fallback."""
        pass

    @abstractmethod
    async def delete_page(self, page_id: str) -> bool:
        """Delete a page by ID."""
        pass

    @abstractmethod
    async def delete_by_kb(self, kb_id: str) -> int:
        """Delete all pages for a KB. Returns count."""
        pass

    @abstractmethod
    async def delete_by_document(self, org_id: str, document_id: str) -> int:
        """Delete all pages for a specific document across all KBs for an org. Returns count."""
        pass

    @abstractmethod
    async def get_or_create_collection(
        self,
        org_id: str,
        document_id: str,
        name: str = "",
        process_description: str = "",
        stage_description: str = "",
        process_id: Optional[int] = None,
        stage_id: Optional[int] = None,
        stage_ids: Optional[list[int]] = None,
        process_assignments: Optional[list | dict] = None,
    ) -> dict:
        """Find or create a KB collection for (org_id, document_id). Returns collection dict."""
        pass

    @abstractmethod
    async def list_collections(self, org_id: str) -> list[dict]:
        """List all KB collections for an org."""
        pass

    @abstractmethod
    async def delete_collection(self, collection_id: str) -> bool:
        """Delete a collection and all its pages. Returns True if deleted."""
        pass

    @abstractmethod
    async def get_kb_ids_for_org(self, org_id: str) -> list[str]:
        """Get all KB IDs (collection UUIDs) for an org, including org_id fallback."""
        pass

    async def close(self):
        """Close connections."""
        pass


# ---- PostgreSQL + pgvector Implementation ----


class PostgresKnowledgeBase(KnowledgeBase):
    """PostgreSQL implementation with Full-Text Search (tsvector)."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._use_generated_text_search: Optional[bool] = None
        self._use_embeddings: Optional[bool] = None
        self._org_pages_cache: dict[tuple, list[KnowledgePage]] = {}

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        return self._pool

    async def prefetch_org_pages(self, kb_ids: list[str]) -> list[KnowledgePage]:
        """Fetch and cache all KB pages for the given org/kb_ids into memory."""
        if not kb_ids:
            return []
        kb_ids = [str(k) for k in kb_ids]
        cache_key = tuple(sorted(kb_ids))
        if cache_key in self._org_pages_cache:
            return self._org_pages_cache[cache_key]

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at
                FROM kb_pages
                WHERE (kb_id = ANY($1::text[]) OR page_meta->>'org_id' = ANY($1::text[]))
                ORDER BY created_at ASC
                """,
                kb_ids,
            )
            pages = [self._row_to_page(r) for r in rows]
            self._org_pages_cache[cache_key] = pages
            logger.info(f"[KB] Pre-fetched and cached {len(pages)} KB pages for kb_ids={kb_ids}")
            return pages

    async def warmup(self, kb_ids: Optional[list[str]] = None):
        """Pre-warm asyncpg connection pool, schema column detection, embedding client, and pre-fetch org KB pages."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await self._supports_generated_text_search(conn)
                await self._supports_embeddings(conn)
                await conn.fetchval("SELECT 1")
            try:
                from mantra.gemini_embeddings import embedding_enabled, _get_client
                if embedding_enabled():
                    _get_client()
            except Exception:
                pass
            if kb_ids:
                await self.prefetch_org_pages(kb_ids)
            logger.info("[KB] PostgresKnowledgeBase warmed up (pool, schema, embeddings client ready, org KB cached)")
        except Exception as e:
            logger.warning(f"[KB] Warmup encountered error (non-fatal): {e}")

    async def add_page(self, page: KnowledgePage) -> str:
        pool = await self._get_pool()

        # Clean null bytes from strings to prevent asyncpg.exceptions.CharacterNotInRepertoireError
        def clean_val(val):
            if isinstance(val, str):
                return val.replace("\x00", "")
            elif isinstance(val, dict):
                return {k: clean_val(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [clean_val(v) for v in val]
            return val

        kb_id = clean_val(page.kb_id)
        title = clean_val(page.title)
        content = clean_val(page.content)
        source_type = clean_val(page.source_type)
        content_in_text = clean_val(page.content_in_text)
        page_meta = clean_val(page.page_meta)
        embedding = page.embedding

        async with pool.acquire() as conn:
            supports_emb = await self._supports_embeddings(conn)
            if embedding is not None and supports_emb:
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO kb_pages (id, kb_id, title, content, source_type, page_meta, content_in_text, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
                        RETURNING id
                    """,
                        uuid.UUID(page.id),
                        kb_id,
                        title,
                        content,
                        source_type,
                        json.dumps(page_meta),
                        content_in_text,
                        _embedding_to_text(embedding),
                    )
                except asyncpg.exceptions.UndefinedColumnError:
                    self._use_embeddings = False
                    row = await conn.fetchrow(
                        """
                        INSERT INTO kb_pages (id, kb_id, title, content, source_type, page_meta, content_in_text)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        RETURNING id
                    """,
                        uuid.UUID(page.id),
                        kb_id,
                        title,
                        content,
                        source_type,
                        json.dumps(page_meta),
                        content_in_text,
                    )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO kb_pages (id, kb_id, title, content, source_type, page_meta, content_in_text)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                """,
                    uuid.UUID(page.id),
                    kb_id,
                    title,
                    content,
                    source_type,
                    json.dumps(page_meta),
                    content_in_text,
                )
            return str(row["id"])

    async def _supports_generated_text_search(self, conn: asyncpg.Connection) -> bool:
        if self._use_generated_text_search is not None:
            return self._use_generated_text_search

        row = await conn.fetchrow(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'kb_pages' AND column_name = 'text_search'
            ) AS has_column
            """
        )
        self._use_generated_text_search = bool(row["has_column"]) if row else False
        return self._use_generated_text_search

    async def _supports_embeddings(self, conn: asyncpg.Connection) -> bool:
        if self._use_embeddings is not None:
            return self._use_embeddings

        row = await conn.fetchrow(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'kb_pages' AND column_name = 'embedding'
            ) AS has_column
            """
        )
        self._use_embeddings = bool(row["has_column"]) if row else False
        return self._use_embeddings

    def _row_to_page(self, r) -> KnowledgePage:
        return KnowledgePage(
            id=str(r["id"]),
            kb_id=r["kb_id"],
            title=r["title"],
            content=r["content"],
            source_type=r["source_type"],
            page_meta=json.loads(r["page_meta"]) if isinstance(r["page_meta"], str) else r["page_meta"],
            content_in_text=r["content_in_text"],
            created_at=r["created_at"].isoformat() if r["created_at"] else None,
        )

    async def search(
        self,
        kb_ids: list[str],
        query: str,
        top_k: int = 3,
        tags: Optional[list[str]] = None,
    ) -> list[KnowledgePage]:
        """
        Tiered hybrid retrieval:
          Tier A: strict FTS (AND) + semantic (pgvector) blended via RRF.
                  FTS query and query embedding are executed concurrently.
          Tier B: loose FTS (OR) if A returns nothing.
          Tier C: tag-only match if B returns nothing and tags are present.
          Tier D: list available docs if all above return nothing.
        """
        kb_ids = [str(k) for k in kb_ids] if kb_ids else []
        tags = [str(t) for t in tags] if tags else None

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            use_generated_column = await self._supports_generated_text_search(conn)
            supports_embeddings = await self._supports_embeddings(conn)

            # Concurrent launch: FTS query + fast-raced Gemini query embedding API (950ms timeout cap)
            fts_coro = conn.fetch(
                build_search_query(use_generated_column=use_generated_column),
                kb_ids,
                query,
                top_k,
                tags,
            )

            async def _raced_embed(q: str, timeout_s: float = 0.35) -> Optional[list[float]]:
                try:
                    return await asyncio.wait_for(self._embed_query(q), timeout=timeout_s)
                except asyncio.TimeoutError:
                    logger.info(f"[KB] Fast-race: Embedding exceeded {int(timeout_s*1000)}ms — proceeding immediately with FTS")
                    return None
                except Exception as err:
                    logger.warning(f"[KB] Fast-race embedding error: {err}")
                    return None

            embed_coro = _raced_embed(query) if supports_embeddings else None

            if embed_coro:
                fts_rows, query_embedding = await asyncio.gather(
                    fts_coro, embed_coro, return_exceptions=True
                )
                if isinstance(fts_rows, Exception):
                    logger.error(f"[KB] FTS search error: {fts_rows}")
                    fts_rows = []
                if isinstance(query_embedding, Exception):
                    logger.warning(f"[KB] Embedding query error: {query_embedding}")
                    query_embedding = None
            else:
                fts_rows = await fts_coro
                query_embedding = None

            pages = [self._row_to_page(r) for r in fts_rows]

            # Soft tag filter: if tagged search missed, retry without tags
            if not pages and tags:
                rows = await conn.fetch(
                    build_search_query(use_generated_column=use_generated_column),
                    kb_ids,
                    query,
                    top_k,
                    None,
                )
                pages = [self._row_to_page(r) for r in rows]

            # Blend in semantic results (Tier A+)
            if query_embedding:
                try:
                    vec_rows = await conn.fetch(
                        build_vector_search_query(),
                        kb_ids,
                        _embedding_to_text(query_embedding),
                        top_k,
                        tags,
                    )
                    vec_pages = [self._row_to_page(r) for r in vec_rows]
                    if vec_pages:
                        pages = _blend_results(pages, vec_pages, top_k)
                except Exception as vec_err:
                    logger.warning(f"[KB] Vector search fetch error: {vec_err}")

            if not pages:
                # Tier B — loose FTS (OR)
                rows = await conn.fetch(
                    build_loose_search_query(use_generated_column=use_generated_column),
                    kb_ids,
                    query,
                    top_k,
                    tags,
                )
                pages = [self._row_to_page(r) for r in rows]

            if not pages and tags:
                # Tier C — tag-only match
                rows = await conn.fetch(
                    build_tag_search_query(),
                    kb_ids,
                    tags,
                    top_k,
                )
                pages = [self._row_to_page(r) for r in rows]

            return pages

    async def _embed_query(self, query: str) -> Optional[list[float]]:
        """Embed a query for semantic search; None if embeddings unavailable."""
        from mantra.gemini_embeddings import embed_text
        return await embed_text(query)

    async def list_available(self, kb_ids: list[str], top_k: int = 10) -> list[KnowledgePage]:
        """List pages available in the scoped KBs (title + tags) for no-match fallback."""
        if not kb_ids:
            return []
        kb_ids = [str(k) for k in kb_ids]
        cache_key = tuple(sorted(kb_ids))
        if cache_key in self._org_pages_cache:
            return self._org_pages_cache[cache_key][:top_k]

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                build_list_docs_query(),
                kb_ids,
                top_k,
            )
            return [self._row_to_page(r) for r in rows]

    async def backfill_embeddings(
        self,
        kb_id: Optional[str] = None,
        batch_size: int = 100,
        limit: Optional[int] = None,
        dry_run: bool = False,
        progress: Optional[callable] = None,
    ) -> dict:
        """
        Backfill `embedding` for kb_pages rows that don't have one yet.

        Idempotent/resumable: rows with an existing embedding are skipped, so
        re-running after a failure continues from where it stopped.

        Returns a summary dict. When `progress` is given, it is called with a
        string after each batch (useful for streaming progress to an HTTP client).
        """
        pool = await self._get_pool()

        where = "embedding IS NULL"
        params: list = []
        if kb_id:
            where += " AND kb_id = $1"
            params.append(kb_id)

        async with pool.acquire() as conn:
            supports = await self._supports_embeddings(conn)
            if not supports:
                raise RuntimeError("kb_pages.embedding column does not exist. Run migration 006 first.")

            sql = f"SELECT id, content_in_text FROM kb_pages WHERE {where} ORDER BY created_at"
            if limit:
                sql += f" LIMIT {limit}"
            rows = await conn.fetch(sql, *params)

        summary = {
            "found": len(rows),
            "dry_run": dry_run,
            "done": 0,
            "failed": 0,
            "kb_id": kb_id,
        }

        if dry_run:
            return summary

        from mantra.gemini_embeddings import embed_texts, get_embedding_dim

        dim = get_embedding_dim()

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [r["content_in_text"][:8000] for r in batch]
            try:
                vectors = await embed_texts(texts)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Backfill batch {start // batch_size} failed: {e}")
                summary["failed"] += len(batch)
                continue

            async with pool.acquire() as conn:
                for row, vec in zip(batch, vectors):
                    await conn.execute(
                        "UPDATE kb_pages SET embedding = $1::vector WHERE id = $2",
                        _embedding_to_text(vec),
                        row["id"],
                    )
            summary["done"] += len(batch)
            if progress:
                progress(f"Backfilled {summary['done']}/{len(rows)} (dim={dim})")

        return summary

    async def delete_page(self, page_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM kb_pages WHERE id = $1", uuid.UUID(page_id)
            )
            return result == "DELETE 1"

    async def delete_by_kb(self, kb_id: str) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM kb_pages WHERE kb_id = $1", kb_id)
            return int(result.split()[-1]) if result.startswith("DELETE") else 0


    async def delete_by_document(self, org_id: str, document_id: str) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM kb_pages WHERE page_meta->>'document_id' = $1",
                document_id.replace("\x00", ""),
            )
            deleted_pages = int(result.split()[-1]) if result.startswith("DELETE") else 0
            await conn.execute(
                "DELETE FROM kb_collections WHERE org_id = $1 AND document_id = $2",
                org_id.replace("\x00", ""),
                document_id.replace("\x00", ""),
            )
            return deleted_pages

    async def get_or_create_collection(
        self,
        org_id: str,
        document_id: str,
        name: str = "",
        process_description: str = "",
        stage_description: str = "",
        process_id: Optional[int] = None,
        stage_id: Optional[int] = None,
        stage_ids: Optional[list[int]] = None,
        process_assignments: Optional[list | dict] = None,
    ) -> dict:
        pool = await self._get_pool()
        pa_json = json.dumps(process_assignments) if process_assignments is not None else None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO kb_collections (
                    org_id, document_id, name, process_description, stage_description,
                    process_id, stage_id, stage_ids, process_assignments
                )
                VALUES ($1, $2, COALESCE(NULLIF($3, ''), $2), NULLIF($4, ''), NULLIF($5, ''), $6, $7, $8, $9::jsonb)
                ON CONFLICT (org_id, document_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    process_description = EXCLUDED.process_description,
                    stage_description = EXCLUDED.stage_description,
                    process_id = COALESCE(EXCLUDED.process_id, kb_collections.process_id),
                    stage_id = COALESCE(EXCLUDED.stage_id, kb_collections.stage_id),
                    stage_ids = COALESCE(EXCLUDED.stage_ids, kb_collections.stage_ids),
                    process_assignments = COALESCE(EXCLUDED.process_assignments, kb_collections.process_assignments)
                RETURNING id, org_id, document_id, name, description, created_at, process_description, stage_description, process_id, stage_id, stage_ids, process_assignments
                """,
                org_id, document_id, name, process_description, stage_description, process_id, stage_id, stage_ids, pa_json,
            )
            res = dict(row)
            if res.get("process_assignments") and isinstance(res["process_assignments"], str):
                try:
                    res["process_assignments"] = json.loads(res["process_assignments"])
                except Exception:
                    pass
            return res

    async def list_collections(self, org_id: str) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, document_id, name, description, process_description, stage_description,
                       process_id, stage_id, stage_ids, process_assignments, created_at
                FROM kb_collections
                WHERE org_id = $1
                ORDER BY created_at DESC
                """,
                org_id,
            )
            res = []
            for r in rows:
                d = dict(r)
                if d.get("process_assignments") and isinstance(d["process_assignments"], str):
                    try:
                        d["process_assignments"] = json.loads(d["process_assignments"])
                    except Exception:
                        pass
                res.append(d)
            return res

    async def get_collection_details_for_org(self, org_id: str) -> dict | None:
        """Fetch the most recent collection details (including process_id and stage_id) for an org."""
        cols = await self.list_collections(org_id)
        if cols:
            return cols[0]
        return None

    async def delete_collection(self, collection_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM kb_pages WHERE kb_id = $1", collection_id
                )
                result = await conn.execute(
                    "DELETE FROM kb_collections WHERE id = $1", uuid.UUID(collection_id)
                )
            return result != "DELETE 0"

    async def get_kb_ids_for_org(self, org_id: str) -> list[str]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM kb_collections WHERE org_id::text = $1 OR id::text = $1", str(org_id)
            )
            kb_ids = [str(r["id"]) for r in rows]
            if str(org_id) not in kb_ids:
                kb_ids.append(str(org_id))
            return kb_ids

    async def get_process_stage_data_for_kb_ids(self, kb_ids: list[str]) -> list:
        """Fetch process_stage_data arrays from all KB pages associated with the given kb_ids, or build from kb_collections."""
        if not kb_ids:
            return []
        str_kb_ids = [str(k) for k in kb_ids]
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT page_meta FROM kb_pages 
                WHERE (kb_id = ANY($1::text[]) OR page_meta->>'org_id' = ANY($1::text[])) 
                  AND (page_meta->'process_stage_data' IS NOT NULL OR page_meta->>'process_stage_data' IS NOT NULL)
                """,
                str_kb_ids,
            )
            seen_ids = set()
            result = []
            for r in rows:
                meta = json.loads(r["page_meta"]) if isinstance(r["page_meta"], str) else r["page_meta"]
                if isinstance(meta, dict):
                    psd = meta.get("process_stage_data")
                    if isinstance(psd, str):
                        try:
                            psd = json.loads(psd)
                        except Exception:
                            pass
                    if isinstance(psd, list):
                        for entry in psd:
                            if isinstance(entry, dict):
                                pid = entry.get("id") or entry.get("process_id")
                                if pid is not None and pid not in seen_ids:
                                    seen_ids.add(pid)
                                    result.append(entry)

            if not result:
                try:
                    cols = await conn.fetch(
                        """
                        SELECT id, org_id, document_id, name, process_description, stage_description,
                               process_id, stage_id, stage_ids, process_assignments
                        FROM kb_collections WHERE org_id = ANY($1::text[]) OR id::text = ANY($1::text[])
                        ORDER BY created_at DESC
                        """,
                        str_kb_ids,
                    )
                    for c in cols:
                        proc_desc = c.get("process_description", "") if hasattr(c, "get") else ""
                        stage_desc = c.get("stage_description", "") if hasattr(c, "get") else ""
                        pa_raw = c.get("process_assignments") if hasattr(c, "get") else None
                        if pa_raw:
                            if isinstance(pa_raw, str):
                                try:
                                    pa_raw = json.loads(pa_raw)
                                except Exception:
                                    pass
                            if isinstance(pa_raw, list):
                                for entry in pa_raw:
                                    if isinstance(entry, dict):
                                        pid = entry.get("process_id")
                                        s_ids = entry.get("stage_ids") or []
                                        if pid is not None and pid not in seen_ids:
                                            seen_ids.add(pid)
                                            result.append({
                                                "id": pid,
                                                "process_id": pid,
                                                "name": c["name"] or "Process",
                                                "description": proc_desc or "",
                                                "stages": [
                                                    {
                                                        "stage_id": sid,
                                                        "name": c["name"] or "Stage",
                                                        "description": stage_desc or "",
                                                    }
                                                    for sid in s_ids
                                                ]
                                            })
                        if not result:
                            pid = c.get("process_id") or c["document_id"]
                            sid = c.get("stage_id") or c["document_id"]
                            if pid not in seen_ids:
                                seen_ids.add(pid)
                                result.append({
                                    "id": pid,
                                    "process_id": pid,
                                    "name": c["name"] or "Process",
                                    "description": proc_desc or "",
                                    "stages": [
                                        {
                                            "stage_id": sid,
                                            "name": c["name"] or "Stage",
                                            "description": stage_desc or "",
                                        }
                                    ]
                                })
                except Exception:
                    pass

            return result

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None


# ---- Chunking Strategies ----


def detect_structure(text: str) -> str:
    """Detect document structure: 'heading', 'paragraph', or 'dense'."""
    lines = text.split("\n")
    heading_count = sum(
        1
        for l in lines
        if l.strip().startswith(
            ("#", "##", "###", "Section", "SECTION", "Chapter", "CHAPTER")
        )
    )
    paragraph_count = sum(1 for l in lines if len(l.strip()) > 50)

    if heading_count >= 2:
        return "heading"
    elif paragraph_count >= 3:
        return "paragraph"
    return "dense"


def chunk_by_heading(text: str, max_tokens: int = 2000) -> list[dict]:
    """Chunk by markdown/heading structure."""
    chunks = []
    current_chunk = []
    current_heading = "Introduction"
    current_tokens = 0

    for line in text.split("\n"):
        line_stripped = line.strip()
        is_heading = line_stripped.startswith(
            ("#", "##", "###", "Section", "SECTION", "Chapter", "CHAPTER")
        )

        if is_heading and current_chunk:
            chunks.append(
                {
                    "content": "\n".join(current_chunk).strip(),
                    "heading": current_heading,
                    "strategy": "heading",
                }
            )
            current_chunk = [line]
            current_heading = line_stripped.lstrip("#").strip()
            current_tokens = len(line) // 4
        else:
            current_chunk.append(line)
            current_tokens += len(line) // 4

            if current_tokens > max_tokens:
                chunks.append(
                    {
                        "content": "\n".join(current_chunk).strip(),
                        "heading": current_heading,
                        "strategy": "heading",
                    }
                )
                current_chunk = []
                current_tokens = 0

    if current_chunk:
        chunks.append(
            {
                "content": "\n".join(current_chunk).strip(),
                "heading": current_heading,
                "strategy": "heading",
            }
        )

    return chunks


def chunk_by_paragraph(text: str, max_tokens: int = 2000) -> list[dict]:
    """Chunk by paragraph breaks."""
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 0]

    current_chunk = []
    current_tokens = 0

    for p in paragraphs:
        p_tokens = len(p) // 4
        if current_tokens + p_tokens > max_tokens and current_chunk:
            chunks.append(
                {
                    "content": "\n\n".join(current_chunk),
                    "heading": f"Section {len(chunks) + 1}",
                    "strategy": "paragraph",
                }
            )
            current_chunk = [p]
            current_tokens = p_tokens
        else:
            current_chunk.append(p)
            current_tokens += p_tokens

    if current_chunk:
        chunks.append(
            {
                "content": "\n\n".join(current_chunk),
                "heading": f"Section {len(chunks) + 1}",
                "strategy": "paragraph",
            }
        )

    return chunks


def chunk_by_sliding_window(
    text: str, max_tokens: int = 2000, overlap: int = 200
) -> list[dict]:
    """Chunk by fixed token window with overlap."""
    words = text.split()
    chunks = []
    step = max_tokens - overlap

    for i in range(0, len(words), step):
        chunk_words = words[i : i + max_tokens]
        if len(chunk_words) == 0:
            break
        chunks.append(
            {
                "content": " ".join(chunk_words),
                "heading": f"Chunk {len(chunks) + 1}",
                "strategy": "sliding_window",
            }
        )

    return chunks


def adaptive_chunk(text: str, max_tokens: int = 2000) -> list[dict]:
    """Auto-detect structure and apply appropriate chunking."""
    structure = detect_structure(text)
    logger.info(f"Detected structure: {structure}")

    if structure == "heading":
        return chunk_by_heading(text, max_tokens)
    elif structure == "paragraph":
        return chunk_by_paragraph(text, max_tokens)
    else:
        return chunk_by_sliding_window(text, max_tokens)


# ---- Ingestion Pipeline ----


async def extract_pdf_text(file_bytes: bytes) -> str:
    """Extract text from PDF bytes."""
    import io

    reader = PdfReader(io.BytesIO(file_bytes))
    texts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            texts.append(t)
    return "\n\n".join(texts)


async def extract_url_text(url: str) -> str:
    """Extract readable text from URL."""
    # Run synchronous network request in a thread pool so it doesn't block the FastAPI event loop
    downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
    if not downloaded:
        raise ValueError(f"Failed to fetch URL: {url}")

    extracted = trafilatura.extract(
        downloaded, include_comments=False, include_tables=True, favor_recall=True
    )

    # Trafilatura aggressively strips grids and cards common on landing pages.
    # Fallback to regex text extraction if trafilatura stripped a lot of text.
    import re

    raw_text = re.sub(
        r"<(script|style|head|svg|nav|footer)[^>]*>.*?</\1>",
        " ",
        downloaded,
        flags=re.DOTALL | re.IGNORECASE,
    )
    raw_text = re.sub(r"<[^>]+>", " ", raw_text)
    raw_text = re.sub(r"\s+", " ", raw_text).strip()

    if not extracted or len(raw_text) > len(extracted or "") * 2:
        extracted = raw_text

    if not extracted:
        raise ValueError(f"No readable content found at URL: {url}")
    return extracted


async def ingest_file(
    kb: KnowledgeBase, kb_id: str, file_bytes: bytes, filename: str,
    page_meta: Optional[dict] = None
) -> dict:
    """Ingest a file into the knowledge base."""
    if filename.endswith(".pdf"):
        text = await extract_pdf_text(file_bytes)
    elif filename.endswith((".txt", ".md")):
        text = file_bytes.decode("utf-8")
    else:
        raise ValueError(f"Unsupported file type: {filename}")

    return await ingest_text(kb, kb_id, text, source_type="file", content=filename, page_meta=page_meta)


async def ingest_text(
    kb: KnowledgeBase,
    kb_id: str,
    content_in_text: str,
    title: Optional[str] = None,
    source_type: str = "text",
    content: str = "",
    page_meta: Optional[dict] = None,
    embed: bool = True,
) -> dict:
    """Ingest raw text into the knowledge base."""
    chunks = adaptive_chunk(content_in_text)

    # Compute embeddings for all chunks up front (batch API call), then fall
    # back gracefully to FTS-only rows if embedding fails.
    embeddings: list[Optional[list[float]]] = [None] * len(chunks)
    if embed:
        try:
            from mantra.gemini_embeddings import embed_texts, embedding_enabled

            if embedding_enabled():
                texts = [chunk["content"][:8000] for chunk in chunks]
                embeddings = await embed_texts(texts)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Embedding skipped during ingest (FTS-only): {e}")
            embeddings = [None] * len(chunks)

    page_ids = []
    for i, chunk in enumerate(chunks):
        meta = {
            "strategy": chunk["strategy"],
            "chunk_index": i,
            "total_chunks": len(chunks),
        }
        if page_meta:
            meta.update(page_meta)

        page = KnowledgePage(
            id=str(uuid.uuid4()),
            kb_id=kb_id,
            title=title or chunk["heading"],
            content=content,
            source_type=source_type,
            page_meta=meta,
            content_in_text=chunk["content"],
            embedding=embeddings[i] if i < len(embeddings) else None,
        )
        page_id = await kb.add_page(page)
        page_ids.append(page_id)

    return {
        "chunks_created": len(chunks),
        "strategy_used": chunks[0]["strategy"] if chunks else "unknown",
        "page_ids": page_ids,
        "kb_id": kb_id,
    }


async def ingest_url(kb: KnowledgeBase, kb_id: str, url: str) -> dict:
    """Ingest a URL into the knowledge base."""
    text = await extract_url_text(url)
    return await ingest_text(kb, kb_id, text, source_type="url", content=url)
