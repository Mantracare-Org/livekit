import logging
from typing import List
from mantra.knowledge_base import PostgresKnowledgeBase

logger = logging.getLogger("mantra.retriever")

class KnowledgeRetriever:
    def __init__(self, kb: PostgresKnowledgeBase):
        self.kb = kb
        self.session_cache = {}

    async def retrieve(self, query: str, kb_ids: List[str], top_k: int = 3, tags: List[str] = None) -> str:
        """
        Searches the given knowledge bases for the query with optional tag filtering.
        Uses an in-memory session cache to avoid repeated DB calls.
        """
        if not kb_ids:
            return "No Knowledge Base configured for this session."
            
        # Create a cache key using the query, sorted kb_ids, and sorted tags
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
                tags=tags
            )
            
            if not results:
                formatted_result = "No relevant information found in the knowledge base for this query."
            else:
                formatted_result = "--- RELEVANT KNOWLEDGE BASE INFORMATION ---\n\n"
                for i, page in enumerate(results, 1):
                    formatted_result += f"Source {i} [{page.title}]:\n{page.content_in_text}\n\n"
            
            # Cache the result
            self.session_cache[cache_key] = formatted_result
            return formatted_result
            
        except Exception as e:
            logger.error(f"Error during retrieval: {e}")
            return "An error occurred while searching the knowledge base."
