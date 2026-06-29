import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv(".env.local")

async def test():
    try:
        conn = await asyncpg.connect(
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            database=os.getenv("POSTGRES_DB"),
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
        )
        print("Connected!")
        row = await conn.fetchrow("""
            SELECT
                COUNT(*)::int AS total_calls,
                ROUND(
                    AVG(
                        CAST(NULLIF(call_log::json ->> 'call_duration_seconds', '') AS integer)
                    ) FILTER (
                        WHERE call_log::json ->> 'call_duration_seconds' ~ '^\d+$'
                    )
                )::int AS avg_duration_seconds
            FROM call_logs
            WHERE created_at >= CURRENT_DATE
        """)
        print(row)
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
