"""Database connection helpers for the UI server."""
import os

import asyncpg


async def get_db_connection():
    """Create a PostgreSQL connection for dashboard queries."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return await asyncpg.connect(dsn=database_url, timeout=5.0)
    else:
        return await asyncpg.connect(
            user=os.getenv("POSTGRES_USER", "redscarf"),
            password=os.getenv("POSTGRES_PASSWORD", "nowandforever"),
            database=os.getenv("POSTGRES_DB", "livekit_db"),
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5440"),
            timeout=5.0,
        )