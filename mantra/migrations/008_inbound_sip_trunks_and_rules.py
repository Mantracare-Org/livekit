#!/usr/bin/env python3
"""
Migration script 008: Add inbound trunk and dispatch rule support to sip_trunks registry.

Extends the sip_trunks table to store both inbound and outbound trunks and their
associated LiveKit dispatch rules, enabling auto-recovery if Redis or LiveKit store resets.
"""

import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv

load_dotenv(".env.self")
load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_migration():
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
        logger.info("Adding direction, dispatch_rule_id, room_prefix, metadata columns to sip_trunks...")
        await conn.execute("""
            ALTER TABLE sip_trunks 
            ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'outbound',
            ADD COLUMN IF NOT EXISTS dispatch_rule_id TEXT,
            ADD COLUMN IF NOT EXISTS room_prefix TEXT,
            ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;
        """)
        logger.info("Columns added successfully")

        logger.info("Creating index on dispatch_rule_id and direction...")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sip_trunks_direction ON sip_trunks (direction);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sip_trunks_dispatch_rule_id ON sip_trunks (dispatch_rule_id);"
        )

        logger.info("Syncing existing inbound trunks from org_configs into sip_trunks...")
        rows = await conn.fetch("""
            SELECT org_id, phone_number, name, prompt, voice, model, sip_trunk_id, dispatch_rule_id
            FROM org_configs
            WHERE sip_trunk_id IS NOT NULL;
        """)

        for row in rows:
            phone = row["phone_number"]
            trunk_id = row["sip_trunk_id"]
            rule_id = row["dispatch_rule_id"]
            name = row["name"] or f"Inbound {phone}"
            
            await conn.execute("""
                INSERT INTO sip_trunks (
                    name, provider, address, numbers, auth_username, auth_password,
                    livekit_trunk_id, dispatch_rule_id, phone_number, direction, is_active
                ) VALUES (
                    $1, 'plivo', 'sip.localhost', ARRAY[$2], '', '',
                    $3, $4, $2, 'inbound', true
                )
                ON CONFLICT (name, provider) DO UPDATE SET
                    livekit_trunk_id = EXCLUDED.livekit_trunk_id,
                    dispatch_rule_id = EXCLUDED.dispatch_rule_id,
                    direction = 'inbound',
                    updated_at = NOW();
            """, name, phone, trunk_id, rule_id)
            logger.info(f"Synced inbound trunk for {phone} (Trunk: {trunk_id}, Rule: {rule_id})")

        logger.info("Migration 008 completed successfully!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
