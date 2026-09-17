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
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            database=os.getenv("POSTGRES_DB"),
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
            timeout=5.0,
        )