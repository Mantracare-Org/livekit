"""Backward-compatible wrapper for core.kb.retriever."""

<<<<<<< HEAD
from core.kb.retriever import *
=======
from mantra.knowledge_base import PostgresKnowledgeBase, KnowledgePage

logger = logging.getLogger("mantra.retriever")


class KnowledgeRetriever:
    def __init__(self, kb: PostgresKnowledgeBase):
        self.kb = kb
        self.session_cache = {}
        self.accessed_pages_meta: list[dict] = []
        self.preloaded_pages: list[KnowledgePage] = []

    async def prefetch(self, kb_ids: List[str]) -> List[KnowledgePage]:
        """Preload KB pages for the session's kb_ids into memory."""
        if kb_ids:
            try:
                pages = await self.kb.prefetch_org_pages(kb_ids)
                self.preloaded_pages = pages
                logger.info(f"[Retriever] Preloaded {len(pages)} pages into memory for session kb_ids={kb_ids}")
                return pages
            except Exception as e:
                logger.warning(f"[Retriever] Prefetch error (non-fatal): {e}")
        return []

    async def retrieve(self, query: str, kb_ids: List[str], top_k: int = 3, tags: List[str] = None) -> str:
        """
        Searches the given knowledge bases for the query with optional tag filtering.
        Uses an in-memory session cache to avoid repeated DB calls.
        Falls back gracefully: FTS -> loose FTS -> tag-only -> list available docs.
        """
        if not kb_ids:
            return "No Knowledge Base configured for this session."

        kb_ids = [str(k) for k in kb_ids]
        tags = [str(t) for t in tags] if tags else None

        cache_key = (query.lower().strip(), tuple(sorted(kb_ids)), tuple(sorted(tags)) if tags else None)

        if cache_key in self.session_cache:
            logger.info(f"Retriever cache hit for query: '{query}'")
            return self.session_cache[cache_key]

        logger.info(f"Retriever cache miss for query: '{query}'.")
        try:
            logger.info(f"Searching KBs {kb_ids} with tags {tags} for query: '{query}'")
            results = await self.kb.search(
                kb_ids=kb_ids,
                query=query,
                top_k=top_k,
                tags=tags,
            )

            tier_used = "semantic/fts"
            if not results and tags:
                # Soft tag filter: retry untagged before descending tiers
                results = await self.kb.search(
                    kb_ids=kb_ids,
                    query=query,
                    top_k=top_k,
                    tags=None,
                )
                tier_used = "fts (tags dropped)"

            if not results:
                available = await self.kb.list_available(kb_ids=kb_ids, top_k=10)
                formatted_result = self._format_no_results(query, available)
            else:
                formatted_result = self._format_results(results)
                self._track_pages(results)

            self.session_cache[cache_key] = formatted_result
            logger.info(f"Retriever tier used: {tier_used} for query: '{query}'")
            return formatted_result

        except Exception as e:
            logger.error(f"Error during retrieval: {e}")
            return "An error occurred while searching the knowledge base."

    def _track_pages(self, pages: list[KnowledgePage]):
        for page in pages:
            if page.page_meta:
                self.accessed_pages_meta.append(page.page_meta)

    def _format_results(self, pages: list[KnowledgePage]) -> str:
        formatted_result = "--- RELEVANT KNOWLEDGE BASE INFORMATION ---\n\n"
        for i, page in enumerate(pages, 1):
            formatted_result += f"Source {i} [{page.title}]:\n{page.content_in_text}\n\n"
            meta = page.page_meta or {}
            if meta.get("process_stage_data"):
                formatted_result += f"Process context: {json.dumps(meta['process_stage_data'])}\n\n"
        return formatted_result

    def _format_no_results(self, query: str, available: list[KnowledgePage]) -> str:
        if not available:
            return (
                "No relevant information found in the knowledge base for this query, "
                "and the knowledge base appears to contain no documents."
            )

        titles = []
        for page in available:
            meta = page.page_meta or {}
            tags = meta.get("tags_name") or []
            tag_str = f" [tags: {', '.join(tags)}]" if tags else ""
            titles.append(f"  - {page.title}{tag_str}")

        return (
            "No exact match was found in the knowledge base for this query. "
            "The following documents ARE available in the knowledge base:\n"
            + "\n".join(titles)
            + "\n\nIf the user's question concerns any of these topics, you may search again "
            "with more specific terms from these documents. Otherwise, answer from general "
            "knowledge if appropriate."
        )
>>>>>>> 0798df6 (feat: inject organization process and stage metadata into agent instructions during KB warmup and expand KB collection scope)
