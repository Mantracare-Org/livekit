#!/usr/bin/env python3
"""
Migration 008: webhook_deliveries — replayable outbox for post-call payloads.

Every n8n/summary delivery attempt gets its own row so nothing is lost when
call_logs is upserted over. Failed rows stay here and can be replayed later
via `mantra.resend_webhooks`.

    id              BIGSERIAL    — PK, one row per delivery ATTEMPT
    call_id         VARCHAR(64)  — call this delivery belongs to
    ai_call_id      VARCHAR(64)  — LiveKit job id (dedupe component)
    event_type      VARCHAR(64)  — CALL_DATA_UPDATE / CALL_RETRY / ...
    endpoint        TEXT         — absolute URL POSTed to
    payload         JSONB        — exact JSON body sent (queryable)
    payload_raw     TEXT         — exact bytes sent, preserved for byte-faithful replay
    send_state      VARCHAR(16)  — sent | failed | skipped_dedupe | unconfigured
    attempts        INTEGER     — HTTP attempts made for this row
    http_status     INTEGER     — last HTTP status
    last_error      TEXT        — last failure message
    replay_of       BIGINT      — parent row id when this row is a replay
    replay_count    INTEGER     — times this logical delivery has been replayed
    replayed_at     TIMESTAMPTZ — last replay time
    resolved_at     TIMESTAMPTZ — set when a replay of this row finally succeeded
    created_at      TIMESTAMPTZ — row creation

Why payload_raw exists: the HMAC signature is computed over the exact
serialized body. Postgres JSONB does NOT preserve key order, whitespace or
number formatting, so re-serializing payload would change the bytes. Replay
sends payload_raw verbatim when present so a replayed delivery is
byte-identical to the original attempt.

Why resolved_at exists: the original FAILED row is kept forever as history, so
`--all` must skip failures whose replay already succeeded. Without this,
replaying an already-resolved failure loops forever.

Indexes support the two hot queries: "what failed and needs replay" and
"full attempt history for a call".
"""

import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIGRATION_ID = "008_webhook_deliveries_outbox"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id            BIGSERIAL PRIMARY KEY,
    call_id       VARCHAR(64)  NOT NULL,
    ai_call_id    VARCHAR(64),
    event_type    VARCHAR(64),
    endpoint      TEXT,
    payload       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    payload_raw   TEXT,
    send_state    VARCHAR(16)  NOT NULL DEFAULT 'pending',
    attempts      INTEGER      NOT NULL DEFAULT 0,
    http_status   INTEGER,
    last_error    TEXT,
    replay_of     BIGINT,
    replay_count  INTEGER      NOT NULL DEFAULT 0,
    replayed_at   TIMESTAMPTZ,
    resolved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

# Idempotent: bring an already-applied 008 table up to date.
ADD_RESOLVED_AT_SQL = """
ALTER TABLE webhook_deliveries ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_failed
    ON webhook_deliveries (send_state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_call
    ON webhook_deliveries (call_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_replay_of
    ON webhook_deliveries (replay_of);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_pending_replay
    ON webhook_deliveries (send_state, resolved_at);
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
        logger.info(f"[{MIGRATION_ID}] Creating webhook_deliveries outbox table...")
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(ADD_RESOLVED_AT_SQL)

        logger.info(f"[{MIGRATION_ID}] Creating outbox indexes...")
        await conn.execute(CREATE_INDEX_SQL)

        logger.info(f"[{MIGRATION_ID}] completed successfully!")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
