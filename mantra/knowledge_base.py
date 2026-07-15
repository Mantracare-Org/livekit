"""
Vector Knowledge Base for LKT Voice Agent.

PostgreSQL + pgvector with OpenAI embeddings.
Multi-KB isolation via kb_id column filtering.
"""

import json
import uuid
import logging
from typing import Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

import asyncpg
from pypdf import PdfReader
import trafilatura
import asyncio

logger = logging.getLogger("mantra.knowledge_base")

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
        """Full-text search within a KB, with optional metadata tag filtering."""
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
    async def delete_by_document(self, kb_id: str, document_id: str) -> int:
        """Delete all pages for a specific document. Returns count."""
        pass

    @abstractmethod
    async def close(self):
        """Close connections."""
        pass


# ---- PostgreSQL + pgvector Implementation ----


class PostgresKnowledgeBase(KnowledgeBase):
    """PostgreSQL implementation with pgvector."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        return self._pool

    async def add_page(self, page: KnowledgePage) -> str:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO kb_pages (id, kb_id, title, content, source_type, page_meta, content_in_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            """,
                uuid.UUID(page.id),
                page.kb_id,
                page.title,
                page.content,
                page.source_type,
                json.dumps(page.page_meta),
                page.content_in_text,
            )
            return str(row["id"])

    async def search(
        self,
        kb_ids: list[str],
        query: str,
        top_k: int = 3,
        tags: Optional[list[str]] = None,
    ) -> list[KnowledgePage]:
        """
        Executes a Vectorless Full-Text Search (FTS) query against kb_pages.
        Filters by kb_id and optionally by tags. 
        The tags filter uses the JSONB ?| operator to natively check for overlaps 
        between the requested tags and the stored 'tags_name' JSONB array in page_meta.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kb_id, title, content, source_type, page_meta, content_in_text, created_at,
                       ts_rank(text_search, websearch_to_tsquery('simple', $2)) as similarity
                FROM kb_pages
                WHERE kb_id = ANY($1::text[])
                  AND text_search @@ websearch_to_tsquery('simple', $2)
                  AND ($4::text[] IS NULL OR 
                      (jsonb_typeof(page_meta->'tags_name') = 'array' AND page_meta->'tags_name' ?| $4::text[]) OR
                      (jsonb_typeof(page_meta->'tags_name') = 'string' AND page_meta->>'tags_name' = ANY($4::text[]))
                  )
                ORDER BY similarity DESC
                LIMIT $3
            """,
                kb_ids,
                query,
                top_k,
                tags,
            )

            return [
                KnowledgePage(
                    id=str(r["id"]),
                    kb_id=r["kb_id"],
                    title=r["title"],
                    content=r["content"],
                    source_type=r["source_type"],
                    page_meta=json.loads(r["page_meta"]) if isinstance(r["page_meta"], str) else r["page_meta"],
                    content_in_text=r["content_in_text"],
                    created_at=r["created_at"].isoformat() if r["created_at"] else None,
                )
                for r in rows
            ]

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
    # Extract text
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
) -> dict:
    """Ingest raw text into the knowledge base."""
    # Chunk adaptively
    chunks = adaptive_chunk(content_in_text)

    # Store pages
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
