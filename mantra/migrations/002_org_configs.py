#!/usr/bin/env python3
"""
Migration script to set up org_configs table for mapping phone numbers to organizations.
Run once before using the DB inbound context resolution feature.
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
    """Run the migration to create the org_configs table."""

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
        logger.info("Creating org_configs table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS org_configs (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                org_id            TEXT NOT NULL,
                phone_number      TEXT NOT NULL UNIQUE,
                name              TEXT,
                prompt            TEXT,
                voice             TEXT DEFAULT 'arushi',
                model             TEXT DEFAULT 'deepseek',
                kb_tags           TEXT[] DEFAULT '{}',
                transfer_numbers  JSONB DEFAULT '{}',
                client_name       TEXT DEFAULT 'User',
                process_id        TEXT,
                sip_trunk_id      TEXT,
                dispatch_rule_id  TEXT,
                is_active         BOOLEAN DEFAULT true,
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                updated_at        TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        logger.info("org_configs table created successfully")

        logger.info("Creating indexes...")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_configs_org_id ON org_configs (org_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_configs_active ON org_configs (phone_number, is_active);"
        )
        logger.info("Indexes created successfully")

        table = await conn.fetchrow("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'org_configs' 
            ORDER BY ordinal_position;
        """)
        if table:
            logger.info("org_configs table schema:")
            for col in await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'org_configs' 
                ORDER BY ordinal_position;
            """):
                logger.info(f"  {col['column_name']}: {col['data_type']}")

        logger.info("Migration completed successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
