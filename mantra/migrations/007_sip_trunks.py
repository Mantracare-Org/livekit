#!/usr/bin/env python3
"""
Migration script to set up the durable sip_trunks registry.

The LiveKit SIP store is volatile (Redis-backed) and can lose outbound trunks
on `flushall` or restart. This table is the durable source of truth: the
webhook resolves the caller-ID / trunk from here (DB-first) and recreates the
trunk in the LiveKit store on demand when it is missing.

Run once before deploying the DB-first telephony path.
"""

import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv

load_dotenv(".env.self")
load_dotenv(".env.local")  # Local env for DB connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration():
    """Run the migration to create the sip_trunks table and seed rows."""

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
        logger.info("Creating sip_trunks table...")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sip_trunks (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name                TEXT NOT NULL,
                provider            TEXT NOT NULL DEFAULT 'plivo',
                address             TEXT NOT NULL,
                numbers             TEXT[] NOT NULL DEFAULT '{}',
                auth_username       TEXT NOT NULL,
                auth_password       TEXT NOT NULL,
                destination_country TEXT,
                livekit_trunk_id    TEXT,
                cloud_aliases       TEXT[] DEFAULT '{}',
                phone_number        TEXT,
                is_active           BOOLEAN DEFAULT true,
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                updated_at          TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (name, provider)
            );
        """)
        logger.info("sip_trunks table created successfully")

        logger.info("Creating indexes...")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sip_trunks_name ON sip_trunks (name);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sip_trunks_livekit_id ON sip_trunks (livekit_trunk_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sip_trunks_aliases ON sip_trunks USING GIN (cloud_aliases);"
        )
        logger.info("Indexes created successfully")

        logger.info("Seeding sip_trunks row...")
        await conn.execute(
            """
            INSERT INTO sip_trunks (
                name, provider, address, numbers, auth_username, auth_password,
                destination_country, livekit_trunk_id, cloud_aliases,
                phone_number, is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (name, provider) DO UPDATE SET
                address             = EXCLUDED.address,
                numbers             = EXCLUDED.numbers,
                auth_username       = EXCLUDED.auth_username,
                auth_password       = EXCLUDED.auth_password,
                destination_country = EXCLUDED.destination_country,
                livekit_trunk_id    = EXCLUDED.livekit_trunk_id,
                cloud_aliases       = EXCLUDED.cloud_aliases,
                phone_number        = EXCLUDED.phone_number,
                is_active           = EXCLUDED.is_active,
                updated_at          = NOW();
            """,
            "MC-B2C-Plivo-LOCAL",
            "plivo",
            "18963348729522656.zt.plivo.com",
            ["+918035375213", "918035375213"],
            "mantralocal",
            "McLocal#2026",
            "in",
            "ST_3PTvXVhdW8hN",
            ["ST_maHQuSjpJXNZ"],
            "+918031321203",
            True,
        )
        logger.info("Seed row upserted successfully")

        logger.info("sip_trunks table schema:")
        for col in await conn.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'sip_trunks'
            ORDER BY ordinal_position;
        """):
            logger.info(f"  {col['column_name']}: {col['data_type']}")

        logger.info("Migration completed successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
