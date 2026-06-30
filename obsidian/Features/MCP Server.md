# MCP Server

**File:** `mcp/server.py` (177 lines)

## Overview

Model Context Protocol server exposing PostgreSQL as AI-accessible tools. Built with `mcp[cli]`.

## Tools

| Tool | Description |
|------|-------------|
| `list_tables()` | List all tables in public schema |
| `describe_table(table_name)` | Column names, data types, nullability |
| `execute_query(query)` | Read-only SELECT (rejects non-SELECT) |
| `call_logs(log_data)` | Upsert call log by `call_id` |

## Usage

```bash
uv run python mcp/server.py
```

## Security

- Only SELECT/WITH queries allowed via `execute_query`
- Connection params from environment
- See `mcp/README.md` for production deployment notes
