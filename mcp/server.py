import asyncio
import os
from typing import Any, List, Optional
from mcp.server.fastmcp import FastMCP
import asyncpg
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("Postgres-Database-Server")

# Database connection parameters
DB_USER = os.getenv("POSTGRES_USER", "user")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "admin@77413")
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
            port=DB_PORT
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
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
        return [row['table_name'] for row in rows]
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
            table_name
        )
        await conn.close()
        
        if not rows:
            return f"Table '{table_name}' not found or has no columns."
        
        schema = f"Schema for {table_name}:\n"
        for row in rows:
            nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
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
async def insert_call_log(log_data: dict) -> str:
    """Insert a new call log entry into the call_logs table."""
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"
    
    try:
        # Construct the query dynamically based on log_data keys
        columns = ", ".join(log_data.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(log_data)))
        values = list(log_data.values())
        
        query = f"INSERT INTO call_logs ({columns}) VALUES ({placeholders}) RETURNING id"
        
        row = await conn.fetchrow(query, *values)
        await conn.close()
        
        return f"Successfully inserted call log with ID: {row['id']}"
    except Exception as e:
        if conn:
            await conn.close()
        return f"Error inserting call log: {e}"

if __name__ == "__main__":
    mcp.run()
