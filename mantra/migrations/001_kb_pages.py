#!/usr/bin/env python3
"""
Migration script to set up pgvector and kb_pages table.
Run once before deploying the knowledge base feature.
"""

import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv

load_dotenv(".env.local")  # Load local env for DB connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to create pgvector extension and kb_pages table."""

    db_user = os.getenv("POSTGRES_USER")
    db_password = os.getenv("POSTGRES_PASSWORD")
    db_name = os.getenv("POSTGRES_DB")
    db_host = os.getenv("POSTGRES_HOST")
    db_port = os.getenv("POSTGRES_PORT")

    if not all([db_user, db_password, db_name, db_host, db_port]):
        raise ValueError("Missing required PostgreSQL environment variables")

    conn = await asyncpg.connect(
        user=db_user,
        password=db_password,
        database=db_name,
        host=db_host,
        port=int(db_port),
        timeout=10.0,
    )

    try:
        await conn.execute("DROP TABLE IF EXISTS kb_pages;")

        logger.info("Creating kb_pages table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_pages (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                kb_id           TEXT NOT NULL,
                title           TEXT NOT NULL,
                content         TEXT NOT NULL,
                source_type     TEXT NOT NULL,
                page_meta       JSONB DEFAULT '{}',
                content_in_text TEXT NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                text_search     tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content_in_text, ''))) STORED
            );
        """)
        logger.info("kb_pages table created successfully")

        logger.info("Creating indexes...")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_pages_kb_id ON kb_pages (kb_id);"
        )

        # GIN index for full-text search
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_pages_fts 
            ON kb_pages USING GIN (text_search);
        """)
        logger.info("Indexes created successfully")

        table = await conn.fetchrow("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'kb_pages' 
            ORDER BY ordinal_position;
        """)
        if table:
            logger.info("kb_pages table schema:")
            for col in await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'kb_pages' 
                ORDER BY ordinal_position;
            """):
                logger.info(f"  {col['column_name']}: {col['data_type']}")

        logger.info("Migration completed successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
