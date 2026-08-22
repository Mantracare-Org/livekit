# STRUCTURE.md

## Directory Layout
- **`.planning/`**: Contains generated workflow plans and codebase mapping metadata (managed by GSD tools).
- **`mantra/`**: The core application module containing all business logic.
  - `agent.py`: Contains the LiveKit voice agent and orchestration callbacks.
  - `dispatcher.py`: The background queue worker for processing SIP dispatch logic.
  - `ui_server.py`: FastAPI server definitions and webhook routes.
  - `utils.py`: Helper functions, logging setups, S3 upload logic, and webhook dispatchers.
- **`mcp/`**: Context/tool servers using Model Context Protocol.
  - `server.py`: MCP Postgres integration exposing database queries as AI tools.
- **`src/`**: Empty, intended for additional custom modules or legacy migration.
- **`static/`**: Contains static frontend assets served by FastAPI.
  - `app.js`: Client-side logic for the web UI.
  - `index.html`: The main web interface for the service.
- **`recordings/`**: A local storage directory used for caching temporary audio recording files before they are pushed to S3.

## Naming Conventions
- Top-level executables use standard `snake_case`.
- Redis keys use namespaces: `queue:pending`, `calls:active`, `calls:status:<call_id>`.
