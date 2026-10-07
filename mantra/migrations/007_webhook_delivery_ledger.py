#!/usr/bin/env python3
"""
Migration 007: webhook delivery ledger on call_logs.

Adds columns so the DB records, per call, whether the post-call payload was
actually delivered to the n8n/summary endpoint AND the exact payload sent.

    backend_sent        BOOLEAN      — final delivery outcome (NULL = not attempted)
    backend_send_state  VARCHAR(16)  — sent | failed | skipped_dedupe | unconfigured
    backend_payload     JSONB        — the exact JSON body POSTed to the backend
    backend_event       VARCHAR(64)  — webhook event name (CALL_DATA_UPDATE, ...)
    backend_http_status INTEGER      — last HTTP status code seen
    backend_error       TEXT         — last delivery error message
    backend_attempts    INTEGER      — HTTP attempts made
    backend_sent_at     TIMESTAMPTZ  — when the final outcome was recorded
    backend_last_try_at TIMESTAMPTZ  — when the last delivery attempt happened

backend_send_state distinguishes "we never tried" from "we tried and failed",
which a single boolean cannot express.
"""

import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIGRATION_ID = "007_webhook_delivery_ledger"

ADD_COLUMNS_SQL = """
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_sent         BOOLEAN;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_send_state   VARCHAR(16);
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_payload      JSONB;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_event        VARCHAR(64);
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_http_status  INTEGER;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_error        TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_attempts     INTEGER;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_sent_at      TIMESTAMPTZ;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS backend_last_try_at  TIMESTAMPTZ;
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_call_logs_backend_sent
    ON call_logs (backend_send_state);
CREATE INDEX IF NOT EXISTS idx_call_logs_backend_sent_at
    ON call_logs (backend_sent_at);
"""


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
        logger.info(f"[{MIGRATION_ID}] Adding delivery-ledger columns to call_logs...")
        await conn.execute(ADD_COLUMNS_SQL)

        logger.info(f"[{MIGRATION_ID}] Creating delivery indexes...")
        await conn.execute(CREATE_INDEX_SQL)

        logger.info(f"[{MIGRATION_ID}] completed successfully!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
