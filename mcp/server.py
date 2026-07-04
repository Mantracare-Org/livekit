import os
from typing import List
from mcp.server.fastmcp import FastMCP
import asyncpg
import datetime
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("Postgres-Database-Server")

# Database connection parameters
DB_USER = os.getenv("POSTGRES_USER", "user")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
DB_NAME = os.getenv("POSTGRES_DB", "main_db")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5433")


async def get_db_connection():
    """Establish a connection to the PostgreSQL database."""
    try:
        conn = await asyncpg.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT,
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None


@mcp.tool()
async def list_tables() -> List[str]:
    """List all tables in the public schema of the database."""
    conn = await get_db_connection()
    if not conn:
        return ["Error: Could not connect to database"]

    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        await conn.close()
        return [row["table_name"] for row in rows]
    except Exception as e:
        await conn.close()
        return [f"Error: {e}"]


@mcp.tool()
async def describe_table(table_name: str) -> str:
    """Get the schema details of a specific table."""
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = $1
            ORDER BY ordinal_position
            """,
            table_name,
        )
        await conn.close()

        if not rows:
            return f"Table '{table_name}' not found or has no columns."

        schema = f"Schema for {table_name}:\n"
        for row in rows:
            nullable = "NULL" if row["is_nullable"] == "YES" else "NOT NULL"
            schema += f"- {row['column_name']} ({row['data_type']}) {nullable}\n"
        return schema
    except Exception as e:
        await conn.close()
        return f"Error: {e}"


@mcp.tool()
async def execute_query(query: str) -> str:
    """Execute a read-only SQL query and return the results."""
    # Safety check: Basic read-only check (though real safety should be at DB level)
    query_lower = query.lower().strip()
    if not query_lower.startswith("select") and not query_lower.startswith("with"):
        return "Error: Only SELECT queries are allowed for safety."

    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        rows = await conn.fetch(query)
        await conn.close()

        if not rows:
            return "Query returned no results."

        # Format results as a simple string table
        headers = rows[0].keys()
        output = " | ".join(headers) + "\n"
        output += "-" * len(output) + "\n"
        for row in rows:
            output += " | ".join(str(val) for val in row.values()) + "\n"
        return output
    except Exception as e:
        await conn.close()
        return f"Error: {e}"


@mcp.tool()
async def call_logs(log_data: dict) -> str:
    """Upsert a call log entry into the call_logs table based on call_id.
    Expected keys in log_data may include: call_id, call_log, status, recording_url.
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        if "call_id" not in log_data:
            return "Error: 'call_id' is required in log_data for upsert."

        columns_list = list(log_data.keys())
        columns = ", ".join(columns_list)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns_list)))

        values = []
        for val in log_data.values():
            if isinstance(val, str):
                try:
                    if len(val) >= 10 and (val[4] == "-" and val[7] == "-"):
                        values.append(
                            datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
                        )
                    elif (
                        val.strip().strip("*-• ").lower()
                        in ["none", "null", "n/a", "na", ""]
                        or "none" in val.lower()
                    ):
                        values.append(None)
                    else:
                        values.append(val)
                except (ValueError, TypeError):
                    values.append(None)
            else:
                values.append(val)

        # Build the ON CONFLICT clause
        update_assignments = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in columns_list if col != "call_id"
        )

        if update_assignments:
            conflict_clause = (
                f" ON CONFLICT (call_id) DO UPDATE SET {update_assignments}"
            )
        else:
            conflict_clause = " ON CONFLICT (call_id) DO NOTHING"

        query = f"INSERT INTO call_logs ({columns}) VALUES ({placeholders}){conflict_clause} RETURNING id"

        row = await conn.fetchrow(query, *values)
        await conn.close()

        if row:
            return f"Successfully processed call log with ID: {row['id']}"
        else:
            return f"Successfully processed call log for call_id: {log_data['call_id']}"

    except Exception as e:
        if conn:
            await conn.close()
        return f"Error processing call log: {e}"


if __name__ == "__main__":
    mcp.run()
