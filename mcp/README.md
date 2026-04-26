# MCP Postgres Server

This is an MCP (Model Context Protocol) server that provides tools to interact with your PostgreSQL database.

## Local Setup

1. **Install Dependencies**:

   ```bash
   uv add "mcp[cli]" asyncpg python-dotenv
   ```

2. **Environment Variables**:

   ```env
   POSTGRES_USER=${POSTGRES_USER}
   POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
   POSTGRES_DB=${POSTGRES_DB}
   POSTGRES_PORT=${POSTGRES_PORT}
   POSTGRES_HOST=${POSTGRES_HOST}
   ```

3. **Run the Server**:
   ```bash
   uv run python mcp/server.py
   ```

## Production Changes

When deploying to production (e.g., via Docker):

1. **Update Environment Variables**:
   - `POSTGRES_HOST`: Change from `localhost` to `postgres` (or your database service name/endpoint).
   - `POSTGRES_PORT`: Change from `5433` to `5432` (internal container port).

2. **Secrets Management**:
   Use CI/CD secrets or a secrets manager for `POSTGRES_PASSWORD` instead of hardcoding it in `.env`.

3. **Docker Networking**:
   Ensure the MCP server container is in the same network as the `postgres` container.

## Available Tools

- `list_tables`: Lists all public tables.
- `describe_table(table_name)`: Shows columns and types for a table.
- `execute_query(query)`: Executes a read-only SELECT query.
