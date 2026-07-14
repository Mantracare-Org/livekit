import asyncio
import os
import uuid
import logging
from dotenv import load_dotenv

load_dotenv(".env.local")

from mantra.knowledge_base import PostgresKnowledgeBase, ingest_text
from mantra.retriever import KnowledgeRetriever

# Setup simple logging
logging.basicConfig(level=logging.INFO)

async def test_knowledge_base():
    print("--- Starting KB Test ---")
    load_dotenv(".env.local")
    
    dsn = (
        f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
        f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
    )
    
    kb = PostgresKnowledgeBase(dsn)
    
    # 1. Ingest dummy data
    test_kb_id = f"test_kb_{uuid.uuid4().hex[:8]}"
    print(f"1. Ingesting test document into KB: {test_kb_id}")
    
    test_doc = """
    # MantraCare Employee Policy
    
    MantraCare offers its employees 25 days of paid leave annually. 
    Additionally, we have a remote-first culture and provide a $500 home office stipend.
    """
    
    result = await ingest_text(kb, test_kb_id, test_doc, title="Employee Policy")
    print(f"Ingestion result: {result}")
    
    # 2. Test Retrieval
    print("\n2. Testing retrieval...")
    retriever = KnowledgeRetriever(kb)
    query = "paid leave"
    
    search_result = await retriever.retrieve(query, [test_kb_id])
    print(f"\nQuery: '{query}'")
    print(f"Result:\n{search_result}")
    
    if "25 days" in search_result:
        print("\n✅ SUCCESS: Retrieval found the correct information!")
    else:
        print("\n❌ FAILED: Did not find expected information.")
        
    # 3. Clean up
    print("\n3. Cleaning up test data...")
    deleted_count = await kb.delete_by_kb(test_kb_id)
    print(f"Deleted {deleted_count} pages.")
    
    await kb.close()
    print("--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_knowledge_base())
