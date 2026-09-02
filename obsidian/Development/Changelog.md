# Changelog

## 2026-09-02

### MCP Client Cloudflare WAF Bypass & AuthMiddleware Public Paths Update

- **fix:** Added `auth.mantracare.com`, `livekit-mcp.app-mantra.com`, and `app-mantra.com` to `NO_PROXY` in `lkt/.env` to prevent outgoing MCP/Auth HTTP requests from routing through the HTTP proxy `13.234.222.62:8888` and triggering Cloudflare 403 blocks. Files: `lkt/.env`.
- **fix:** Added standard browser `User-Agent` and `ngrok-skip-browser-warning` headers to `MantraMCPClient` (`mantra/mcp_client.py`) across OAuth token requests and SSE/HTTP tool call connections. Resolves Cloudflare WAF 403 Forbidden ("Attention Required! Cloudflare") blocks when fetching tokens or querying MCP endpoints over domain routes like `app-mantra.com`. Files: `mantra/mcp_client.py`.
- **fix:** Added `/sitemap.xml` and `/robots.txt` to `DEFAULT_PUBLIC_PATHS` in `livekit-mcp` (`livekit_mcp/auth/middleware.py`). Prevents unauthenticated web crawlers and scanners from triggering unnecessary `401 Unauthorized: Missing token` warning logs. Files: `livekit-mcp/src/livekit_mcp/auth/middleware.py`.

## 2026-09-01

### MCP Server Transport Fix & `check_doctor_availability` Re-enablement

- **fix:** `mcp/server.py` was running in `stdio` mode (`mcp.run()` default). `MantraMCPClient` connects over SSE HTTP to `http://0.0.0.0:8000/sse`, so no requests ever reached the server. Changed to `mcp.run(transport="sse", port=8000)` (configurable via `MCP_SERVER_PORT` env var). Files: `mcp/server.py`.
- **fix:** `check_doctor_availability` was commented out of `agent_tools` in `mantra/agent.py`, so the LLM was never offered the tool and could never call the MCP server even when availability queries were made. Re-enabled it in the tools list. Files: `mantra/agent.py`.

## 2026-08-30


### Dynamic OAuth Token Acquisition & Hardcoded JWT Removal

- **feat:** Updated `MantraMCPClient` in [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py) to dynamically acquire OAuth access tokens from Auth Server (`POST ${AUTH_SERVER_URL}/api/oauth/token`) using `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`.
- **refactor:** Removed static `LIVEKIT_MCP_JWT_TOKEN` from [.env.prod](file:///home/fardeen/lkt/.env.prod) in favor of clean OAuth client credentials authentication.

## 2026-08-27

### Organization Processes & Stages MCP Tool (`fetch_org_processes`) & Inbound Post-Call Integration

- **feat:** Added `fetch_org_processes` (and alias `receive_org_processes`) tool to `livekit-mcp`:
  - Queries `MantraAssist-backend` (`GET /api/v1/processes?org_id={org_id}`) with standard webhook headers (`x-client-id`, `x-client-secret`, `ngrok-skip-browser-warning`).
  - Normalizes processes and stages along with their descriptions and stage IDs into structured format `[{"process_id": 317, "process_name": "...", "process_description": "...", "stage_ids": [1155, 1156, ...], "stages": [...]}]`.
  - Implemented an in-memory TTL cache (10-minute expiry) in `MantraAssistBackendClient` to avoid redundant network queries for the same organization.
- **feat:** Integrated MCP process stage retrieval into inbound call post-call analysis in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py):
  - When an inbound call completes in `finalize()`, if `org_id` is present, it invokes `fetch_org_processes` via `MantraMCPClient`.
  - Injects the retrieved `process_stage_data` into `SessionRecorder.analyze_call()`, enabling LLM post-call analysis to accurately assign `derived_process_id` and `new_stage_id` for CRM webhooks.
- **refactor:** Removed legacy placeholder `greeting` tool from `livekit-mcp` (`tools/greeting.py`, `tools/__init__.py`, `server.py`).
- **refactor:** Removed HTML landing page template from `livekit-mcp`:
  - Replaced root endpoint (`GET /`) with a clean, lightweight JSON response (`{"status": "working", "service": "livekit-mcp", "transport": "sse", ...}`).
  - Deleted `src/livekit_mcp/templates/` directory.
- **fix:** Corrected database connection configuration in `livekit-mcp`:
  - Mapped `DATABASE_URL` and `MCP_EVENTS_DB_URL` to point to `postgresql://postgres:password@localhost:5442/mcp_logs_db` (from the active `lkdb` Docker `postgres_mcp` container).
  - Fixed health check endpoint `/api/dev/check-db` to return `200 OK` (healthy).
- **feat:** Added provider `user_id` injection to doctor availability tool and post-call analysis:
  - Formatted provider list in `doctor_availability.py` to include `(User ID: <id>)` (e.g. `Anshul (User ID: 345)`).
  - Captured `provider_user_id` in `check_doctor_availability` and auto-injected into `appointment_metadata.provider_user_id` in `finalize()` in `mantra/agent.py`.
- **refactor:** Standardized datetime formats and reconciled inbound stage transitions:
  - Removed `preferred_end_datetime` from `appointment_metadata`.
  - Updated `normalize_datetime` in `mantra/utils.py` to convert all timestamps to true UTC ISO 8601 string (`YYYY-MM-DDTHH:MM:SSZ`).
  - Added reconciliation for initial `stage_id` in `finalize()` to ensure it strictly belongs to `effective_process_id` (defaulting to the process initial entry stage `1155`), preventing mismatched legacy stages (`323`).
- **refactor:** Removed `x-client-id` and `x-client-secret` auth headers from `livekit-mcp` backend requests to `MantraAssist-backend`.
- Files: [livekit-mcp/src/livekit_mcp/clients/backend_client.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/clients/backend_client.py), [livekit-mcp/src/livekit_mcp/tools/org_processes.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/tools/org_processes.py), [livekit-mcp/src/livekit_mcp/tools/doctor_availability.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/tools/doctor_availability.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py).

## 2026-08-26

### Post-Call `appointment_metadata` & Entrypoint Shutdown Timeout Hardening

- **feat:** Added `appointment_metadata` schema and extraction to post-call analysis in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py):
  - Extracts `provider_user_id`, `provider_name`, `preferred_datetime`, `preferred_end_datetime`, `appointment_title`, and `appointment_notes`.
  - Added `appointment_metadata` key to `CALL_DATA_UPDATE` and `CALL_DATA_INBOUND_UPDATE` webhook payloads in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py).
- **feat:** Extended LiveKit IPC worker shutdown timeout from 15s to 40s (`LIVEKIT_ENTRYPOINT_TIMEOUT=40`) in `.venv/.../job_proc_lazy_main.py` and `.env.local`, ensuring deep reasoning post-call LLM models (`deepseek-v4-pro`) have sufficient execution time to generate summaries, extract fields, and dispatch webhooks before worker termination.
- Files: [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), `.env.local`.

### MCP Server JWT Authentication & Dynamic Live Date/Time Injection

- **feat:** Configured `lkt` to authenticate against `livekit-mcp` with signed Mantra Auth JWT tokens:
  - Added active `LIVEKIT_MCP_URL` and `LIVEKIT_MCP_JWT_TOKEN` in `lkt/.env`.
  - `MantraMCPClient` sends `Authorization: Bearer <jwt>` and `?token=<jwt>` over SSE transport.
  - `livekit-mcp` `AuthMiddleware` verifies token signature using shared `JWT_SECRET` (`sub: lkt-voice-agent`).
- **feat:** Cleaned `livekit-mcp` $\rightarrow$ `MantraAssist-backend` client to query `GET /api/v1/webhooks/mcp` directly using webhook headers (`x-client-id`, `x-client-secret`, `ngrok-skip-browser-warning`).
- **feat:** Injected dynamic live date, time, and current year (`2026`) directly into `initial_instructions` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), eliminating LLM 2024/2025 pre-training cutoff bias on appointment date calculations.
- **feat:** Added past-year auto-roll forward in `livekit-mcp` (`resolve_date_string` in `src/livekit_mcp/utils/timezone.py`) to automatically update any past-year dates to the current calendar year.
- **feat:** Updated `backend_client.py` and `timezone.py` in `livekit-mcp` to preserve the target calendar date (`date = 2026-08-31`) and default `datetime` to `YYYY-MM-DDT00:00:00.000Z` when time is not specified by the user.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py), `lkt/.env`, `livekit-mcp/src/livekit_mcp/clients/backend_client.py`, `livekit-mcp/src/livekit_mcp/utils/timezone.py`.

## 2026-08-25

### Dynamic Department Parameter & Inbound Org ID Resolution for Doctor Availability

- **feat:** Added `department` parameter extraction across `lkt` and `livekit-mcp`:
  - **`mantra/agent.py`:** Updated `check_doctor_availability` tool to accept `department: Optional[str]` and dynamically extract medical specialty/department (e.g. `'Cardiology'`, `'Dermatology'`, `'Retina'`, `'Orthopedics'`) from caller transcripts.
  - **`livekit-mcp`:** Updated `receive_doctor_availability` and `search_provider_availability` tools to accept `department` and query `GET /api/v1/providers/availability` with standard UTC params (`org_id`, `date`, `datetime`, `doc_name`, `department`).
- **fix:** Fixed dynamic `org_id` resolution for inbound telephony calls in `mantra/agent.py`:
  - Inbound calls now look up dialed DID in PostgreSQL `org_configs` and pass the registered `org_id` (e.g. `68`, `278`) directly into `call_state["org_id"]` and `AssistantFunctions`.
  - Removed incorrect fallback to `kb_ids` vector collection UUIDs.
- **fix:** Hardened `livekit-mcp` and `mantra/mcp_client.py`:
  - Added seamless dev-mode anonymous auth in `livekit_mcp/auth/middleware.py`.
  - Added relative date resolver (`resolve_date_string`) handling `'today'`, `'tomorrow'`, `'yesterday'`.
  - Added resilient HTTP fallback (`POST /api/tools/call` via `httpx`) in `mantra/mcp_client.py` if SSE transport fails.
  - Fixed logging format string `%d` → `%s` and normalized 10-digit Indian phone numbers.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py), `livekit-mcp/src/livekit_mcp/tools/doctor_availability.py`, `livekit-mcp/src/livekit_mcp/clients/backend_client.py`.

## 2026-08-29

### DeepSeek TTFT Resiliency & LLM Streaming Read Timeout Hardening

- **fix:** Resolved `httpcore.ReadTimeout` $\rightarrow$ `httpx.ReadTimeout` $\rightarrow$ `livekit.agents._exceptions.APIConnectionError` when streaming responses from DeepSeek models (`deepseek-v4-pro` and `deepseek-v4-flash`):
  - **`mantra/agent.py`:** Configured a dedicated `httpx.AsyncClient` with generous 60-second read timeouts (`timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)`) and connection limits (`max_connections=20`, `keepalive_expiry=120`) in `build_post_call_llm()` and `entrypoint` live DeepSeek instantiation.
  - **`mantra/utils.py`:** Injected `conn_options=APIConnectOptions(timeout=60.0, max_retry=3, retry_interval=2.0)` into `llm_engine.chat()` across `SessionRecorder.analyze_call()` and `SessionRecorder.generate_summary()`, preventing LiveKit's internal `LLMStream` from enforcing the 10-second default connect/read cutoff on DeepSeek thinking/reasoning requests.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py).

### Production Multi-Stage Dockerfile for `livekit-mcp` & Deployment Alignment

- **feat:** Implemented a secure, optimized multi-stage build (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) for `livekit-mcp` aligning with `lkt`'s containerization architecture:
  - Multi-stage build with dependency layer caching via `uv sync --locked --no-install-project --no-dev`.
  - Non-root system user `appuser` (UID 10001) for strict production security compliance.
  - Native Starlette healthcheck (`HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1`).
  - Executable entrypoint via `livekit-mcp` CLI.
- Files: [livekit-mcp/Dockerfile](file:///home/fardeen/livekit-mcp/Dockerfile).

## 2026-08-27

### Organization Processes & Stages MCP Tool (`fetch_org_processes`) & Inbound Post-Call Integration

- **feat:** Added `fetch_org_processes` (and alias `receive_org_processes`) tool to `livekit-mcp`:
  - Queries `MantraAssist-backend` (`GET /api/v1/processes?org_id={org_id}`).
  - Normalizes processes and stages along with their descriptions and stage IDs into structured format `[{"process_id": 317, "process_name": "...", "process_description": "...", "stage_ids": [1155, 1156, ...], "stages": [...]}]`.
  - Implemented an in-memory TTL cache (10-minute expiry) in `MantraAssistBackendClient` to avoid redundant network queries for the same organization.
- **feat:** Integrated MCP process stage retrieval into inbound call post-call analysis in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py):
  - When an inbound call completes in `finalize()`, if `org_id` is present, it invokes `fetch_org_processes` via `MantraMCPClient`.
  - Injects the retrieved `process_stage_data` into `SessionRecorder.analyze_call()`, enabling LLM post-call analysis to accurately assign `derived_process_id` and `new_stage_id` for CRM webhooks.
- **refactor:** Removed legacy placeholder `greeting` tool from `livekit-mcp` (`tools/greeting.py`, `tools/__init__.py`, `server.py`).
- **refactor:** Removed HTML landing page template from `livekit-mcp`:
  - Replaced root endpoint (`GET /`) with a clean, lightweight JSON response (`{"status": "working", "service": "livekit-mcp", "transport": "sse", ...}`).
  - Deleted `src/livekit_mcp/templates/` directory.
- **fix:** Corrected database connection configuration in `livekit-mcp`:
  - Mapped `DATABASE_URL` and `MCP_EVENTS_DB_URL` to point to `postgresql://postgres:password@localhost:5442/mcp_logs_db` (from the active `lkdb` Docker `postgres_mcp` container).
  - Fixed health check endpoint `/api/dev/check-db` to return `200 OK` (healthy).
- **feat:** Added provider `user_id` injection to doctor availability tool and post-call analysis:
  - Formatted provider list in `doctor_availability.py` to include `(User ID: <id>)` (e.g. `Anshul (User ID: 345)`).
  - Captured `provider_user_id` in `check_doctor_availability` and auto-injected into `appointment_metadata.provider_user_id` in `finalize()` in `mantra/agent.py`.
- **refactor:** Standardized datetime formats and reconciled inbound stage transitions:
  - Removed `preferred_end_datetime` from `appointment_metadata`.
  - Updated `normalize_datetime` in `mantra/utils.py` to convert all timestamps to true UTC ISO 8601 string (`YYYY-MM-DDTHH:MM:SSZ`).
  - Added reconciliation for initial `stage_id` in `finalize()` to ensure it strictly belongs to `effective_process_id` (defaulting to the process initial entry stage `1155`), preventing mismatched legacy stages (`323`).
- **refactor:** Removed `x-client-id` and `x-client-secret` auth headers from `livekit-mcp` backend requests to `MantraAssist-backend`.
- Files: [livekit-mcp/src/livekit_mcp/clients/backend_client.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/clients/backend_client.py), [livekit-mcp/src/livekit_mcp/tools/org_processes.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/tools/org_processes.py), [livekit-mcp/src/livekit_mcp/tools/doctor_availability.py](file:///home/fardeen/livekit-mcp/src/livekit_mcp/tools/doctor_availability.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py).

## 2026-08-26

### Post-Call `appointment_metadata` & Entrypoint Shutdown Timeout Hardening

- **feat:** Added `appointment_metadata` schema and extraction to post-call analysis in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py):
  - Extracts `provider_user_id`, `provider_name`, `preferred_datetime`, `preferred_end_datetime`, `appointment_title`, and `appointment_notes`.
  - Added `appointment_metadata` key to `CALL_DATA_UPDATE` and `CALL_DATA_INBOUND_UPDATE` webhook payloads in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py).
- **feat:** Extended LiveKit IPC worker shutdown timeout from 15s to 40s (`LIVEKIT_ENTRYPOINT_TIMEOUT=40`) in `.venv/.../job_proc_lazy_main.py` and `.env.local`, ensuring deep reasoning post-call LLM models (`deepseek-v4-pro`) have sufficient execution time to generate summaries, extract fields, and dispatch webhooks before worker termination.
- Files: [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), `.env.local`.

### MCP Server JWT Authentication & Dynamic Live Date/Time Injection

- **feat:** Configured `lkt` to authenticate against `livekit-mcp` with signed Mantra Auth JWT tokens:
  - Added active `LIVEKIT_MCP_URL` and `LIVEKIT_MCP_JWT_TOKEN` in `lkt/.env`.
  - `MantraMCPClient` sends `Authorization: Bearer <jwt>` and `?token=<jwt>` over SSE transport.
  - `livekit-mcp` `AuthMiddleware` verifies token signature using shared `JWT_SECRET` (`sub: lkt-voice-agent`).
- **feat:** Cleaned `livekit-mcp` $\rightarrow$ `MantraAssist-backend` client to query `GET /api/v1/webhooks/mcp` directly using webhook headers (`x-client-id`, `x-client-secret`, `ngrok-skip-browser-warning`).
- **feat:** Injected dynamic live date, time, and current year (`2026`) directly into `initial_instructions` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), eliminating LLM 2024/2025 pre-training cutoff bias on appointment date calculations.
- **feat:** Added past-year auto-roll forward in `livekit-mcp` (`resolve_date_string` in `src/livekit_mcp/utils/timezone.py`) to automatically update any past-year dates to the current calendar year.
- **feat:** Updated `backend_client.py` and `timezone.py` in `livekit-mcp` to preserve the target calendar date (`date = 2026-08-31`) and default `datetime` to `YYYY-MM-DDT00:00:00.000Z` when time is not specified by the user.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py), `lkt/.env`, `livekit-mcp/src/livekit_mcp/clients/backend_client.py`, `livekit-mcp/src/livekit_mcp/utils/timezone.py`.

## 2026-08-25

### Dynamic Department Parameter & Inbound Org ID Resolution for Doctor Availability

- **feat:** Added `department` parameter extraction across `lkt` and `livekit-mcp`:
  - **`mantra/agent.py`:** Updated `check_doctor_availability` tool to accept `department: Optional[str]` and dynamically extract medical specialty/department (e.g. `'Cardiology'`, `'Dermatology'`, `'Retina'`, `'Orthopedics'`) from caller transcripts.
  - **`livekit-mcp`:** Updated `receive_doctor_availability` and `search_provider_availability` tools to accept `department` and query `GET /api/v1/providers/availability` with standard UTC params (`org_id`, `date`, `datetime`, `doc_name`, `department`).
- **fix:** Fixed dynamic `org_id` resolution for inbound telephony calls in `mantra/agent.py`:
  - Inbound calls now look up dialed DID in PostgreSQL `org_configs` and pass the registered `org_id` (e.g. `68`, `278`) directly into `call_state["org_id"]` and `AssistantFunctions`.
  - Removed incorrect fallback to `kb_ids` vector collection UUIDs.
- **fix:** Hardened `livekit-mcp` and `mantra/mcp_client.py`:
  - Added seamless dev-mode anonymous auth in `livekit_mcp/auth/middleware.py`.
  - Added relative date resolver (`resolve_date_string`) handling `'today'`, `'tomorrow'`, `'yesterday'`.
  - Added resilient HTTP fallback (`POST /api/tools/call` via `httpx`) in `mantra/mcp_client.py` if SSE transport fails.
  - Fixed logging format string `%d` → `%s` and normalized 10-digit Indian phone numbers.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py), `livekit-mcp/src/livekit_mcp/tools/doctor_availability.py`, `livekit-mcp/src/livekit_mcp/clients/backend_client.py`.

## 2026-08-24

### Official Model Context Protocol (MCP 2.0) Architecture & SSE JSON-RPC Client

- **feat:** Implemented official Model Context Protocol (MCP) JSON-RPC 2.0 client and server architecture across `lkt` and `livekit-mcp`:
  - **`mantra/mcp_client.py`:** Built `MantraMCPClient` using Anthropic's official `mcp` SDK (`mcp.client.sse.sse_client` and `mcp.ClientSession`), querying `tools/list` and executing `tools/call` over SSE transport.
  - **`mantra/agent.py`:** Integrated `MantraMCPClient` into `AssistantFunctions.check_doctor_availability`, replacing legacy REST endpoints with native MCP JSON-RPC 2.0 frames over SSE.
  - **`livekit-mcp`:** Upgraded server application to use official `FastMCP` and `SseServerTransport` with mounted `/sse` and `/messages/` endpoints.
- Files: [mantra/mcp_client.py](file:///home/fardeen/lkt/mantra/mcp_client.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), `livekit-mcp/src/livekit_mcp/server.py`.

### Knowledge Base Search Filler Elimination & Proactive Retrieval

- **feat / prompt:** Eliminated vocal filler interruptions ("Let me check that for you", "Let me look that up") when the agent queries the knowledge base:
  - Updated `search_knowledge_base` tool description and prompt directives to strictly enforce silent background execution.
  - Added overriding prompt rules forbidding search narration phrases so the agent directly delivers the retrieved factual answer to the caller.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### Single-Turn Tool Calling & Sequential Search Prevention

- **perf:** Prevented multi-turn sequential tool chaining latency spikes where DeepSeek executed consecutive search tool retries (`search_knowledge_base` -> no match -> `search_knowledge_base` retry), which caused 8+ second round-trip stalls on live calls:
  - Updated `search_knowledge_base` tool description and system prompt directives to enforce executing at most one search tool per turn.
  - Instructed the LLM to synthesize its spoken response immediately after the initial search rather than launching sequential retries over the network.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### Deepgram Nova-3 Telephony STT Configuration & Dynamic Locale Routing

- **fix:** Resolved Deepgram STT misrecognizing Indian names (such as "Jaideep" -> "Debbie", "Navodaya" -> "Noodle"):
  - **Removed Krisp Filter:** Removed external audio isolation filter on SIP audio so un-attenuated speech frequencies pass directly to Deepgram's native neural acoustic denoising.
  - **Deepgram `smart_format=True` & `punctuate=True`:** Enabled Inverse Text Normalization (ITN), Named Entity Recognition (NER), proper noun capitalization, and punctuation per Deepgram streaming documentation.
  - **`numerals=True`:** Enables digit formatting for numbers, dates, times, and phone numbers.
  - **Dynamic Locale Routing (`resolve_stt_language`):** Automatically provisions `en-IN` for Indian callers (+91), `en-US` for US/Canada (+1), `en-GB` for UK (+44), `en-AU` for Australia (+61), while supporting explicit language overrides (`hi`, `es`, `fr`).
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py)

### Pipeline Error Alerting (LLM/STT/TTS Provider Failures)

- **feat:** Added `session.on("error")` handler (`on_session_error`) in `mantra/agent.py` to send crash email alerts for pipeline errors that LiveKit's `AgentSession` swallows internally (e.g., LLM 402 "Insufficient Balance", STT/TTS provider failures) — previously only entrypoint-level exceptions triggered `send_crash_email`.
- **feat:** Rate-limited alerts to one email per call per 300s cooldown window, with cumulative error count included in the email context.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### Hybrid KB Search Concurrency, Org Pre-fetch Caching & Fast-Race Latency Optimization

- **perf:** Optimized Knowledge Base hybrid retrieval latency without altering search quality or removing semantic vector capabilities:
  - **Org KB Pages Pre-fetch Caching:** Added `prefetch_org_pages()` to `PostgresKnowledgeBase` and `prefetch()` to `KnowledgeRetriever`. When a call connects, the worker pre-fetches all KB pages for the session's `kb_ids` and stores them in memory (`_org_pages_cache`), eliminating DB queries on repeated turns and `list_available()` fallbacks.
  - **Fast-Race Embedding Timeout (350ms Cap):** In `mantra/knowledge_base.py`, wrapped `_embed_query` in `_raced_embed` with a strict 350ms timeout. If Google Gemini API returns vector embeddings in <350ms, full hybrid RRF fusion executes; if external API lags, it automatically falls back to instant PostgreSQL FTS instead of stalling the live call.
  - **In-Memory Query Embedding Cache:** Added `_query_cache` in `mantra/gemini_embeddings.py` to return embeddings in 0.0ms for identical or repeated questions during a call.
  - **Concurrent Hybrid Execution (`asyncio.gather`):** In `mantra/knowledge_base.py`, executes PostgreSQL FTS and query embedding concurrently.
  - **Pre-Warmed Database Connection Pool & Schemas:** Added `warmup()` method to `PostgresKnowledgeBase` and `AssistantFunctions` to pre-allocate asyncpg pools, cache schema flags, and pre-initialize clients.
- Files: [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py), [mantra/retriever.py](file:///home/fardeen/lkt/mantra/retriever.py), [mantra/gemini_embeddings.py](file:///home/fardeen/lkt/mantra/gemini_embeddings.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

## 2026-08-20

### Native Deepgram Nova-3 Multi & TurnDetector UX Calibration

- **fix:** Resolved conversational latency, dropped user transcripts, and false interruption loops in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py):
  - **Native STT Engine:** Switched `stt_engine` to native `deepgram.STT(model="nova-3", language="multi", endpointing_ms=25, no_delay=True)`, supporting both English and Hindi with full native support for streaming, `aligned_transcript="word"`, and `interim_results` without custom socket multiplexing overhead.
  - **Fixed Endpointing Delay:** Switched `endpointing` mode from `"dynamic"` to `"fixed"` with `min_delay: 0.25s` (`max_delay: 1.50s`), preventing `DynamicEndpointing` delay spikes and shaving ~150ms off turn latency.
  - **Turn Detection:** Removed premature `unlikely_threshold=0.35` override from `inference.TurnDetector()`, returning to server-calibrated defaults.
  - **Interruption Calibration:** Updated `min_words` from `1` → `2`, set `min_duration: 0.40s`, enabled `resume_false_interruption: True`, and set `false_interruption_timeout: 1.5s` with `backchannel_boundary: (1.0, 1.0)`.
  - **Silero VAD:** Tuned `min_speech_duration: 0.10s`, `min_silence_duration: 0.25s`, `prefix_padding_duration: 0.20s` for snappy turn transitions.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

## 2026-08-22

### Doctor & Healthcare Provider Availability Tool Integration

- **feat:** Added `check_doctor_availability` function tool to `AssistantFunctions` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py):
  - Allows LLM voice agent to dynamically check real-time doctor working hours, open consultation times, and scheduling mid-call for both inbound and outbound calls.
  - Automatically queries `livekit-mcp` HTTP endpoint (`/api/tools/call`) with caller's phone number, organization ID, requested date, and optional doctor name.
  - Returns localized open appointment slots tailored to the caller's timezone (EDT, GMT, IST, GST).
- **feat:** Registered `fnc_ctx.check_doctor_availability` in `agent_tools` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py).
- **feat:** Added `LIVEKIT_MCP_URL` and `LIVEKIT_MCP_JWT_TOKEN` configuration to [.env](file:///home/fardeen/lkt/.env).
- **docs:** Created [obsidian/Features/Doctor Availability Tool.md](file:///home/fardeen/lkt/obsidian/Features/Doctor%20Availability%20Tool.md) and updated [obsidian/Features/Feature Index.md](file:///home/fardeen/lkt/obsidian/Features/Feature%20Index.md).
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [.env](file:///home/fardeen/lkt/.env), [obsidian/Features/Doctor Availability Tool.md](file:///home/fardeen/lkt/obsidian/Features/Doctor%20Availability%20Tool.md), [obsidian/Features/Feature Index.md](file:///home/fardeen/lkt/obsidian/Features/Feature%20Index.md)

## 2026-08-18

### New TTS Voices Integration (Sia, Sneha, Kavita, Katie, Cathy)

- **feat:** Added 5 new TTS voices to `VOICE_MAPPING` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), bringing total selectable voices to 13:
  - `sia` → `4459a9a5-69d6-4680-b970-e13dc51845b6`
  - `sneha` → `6b02ffe5-e3cb-48c0-a023-c72f85953375`
  - `kavita` → `56e35e2d-6eb6-4226-ab8b-9776515a7094`
  - `katie` → `f786b574-daa5-4673-aa0c-cbe3e8534c02`
  - `cathy` → `e8e5fffb-252c-436d-b842-8879b84445b6`
- **docs:** Updated voice table in [obsidian/Features/Voice Agent.md](file:///home/fardeen/lkt/obsidian/Features/Voice%20Agent.md) and [obsidian/Architecture/Components.md](file:///home/fardeen/lkt/obsidian/Architecture/Components.md).
- **fix:** Fixed inbound call missing summary bug in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py):
  - Updated `call_status` determination for inbound calls: treats inbound calls as `user_joined = True` so `analyze_call()` runs and generates the summary.
  - Used `target_llm = post_call_llm or llm_engine` with guaranteed transcript snippet fallback so summary generation never returns empty or `None`.
  - Preserved strict schema contract for `ai_summary` as the sole summary key in `CALL_DATA_INBOUND_UPDATE` and `CALL_DATA_UPDATE` webhook payloads.
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### English & Hindi Only Restriction (Preserved Regional Stubs)

- **feat:** Restricted active language operations strictly to English and Hindi (`SUPPORTED_LANGUAGES = {"en", "hi"}`):
  - Preserved and cleanly commented out Kannada (`kn`), Telugu (`te`), and Marathi (`mr`) in [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py) and [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py) for effortless future re-enablement.
  - Guarded `LanguageHysteresisTracker.evaluate_transition()` to reject any transition candidate not in `SUPPORTED_LANGUAGES`.
  - Streamlined `MultilingualParallelSTT` and [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py) to open only 2 parallel Deepgram WebSockets (`en`, `hi`) instead of 5, reducing network bandwidth and Deepgram API socket overhead.
  - Updated prompt directives in [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py) with strict English/Hindi enforcement.
- Files: [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### Process ID & Stage ID Reconciliation Guard for KB Inbound Calls

- **fix:** Fixed critical process ID and stage ID unmapping bug on inbound calls where `used_kb_process_ids` forcefully set `call_payload["process_id"]` to the first accessed KB page process ID before post-call LLM analysis ran, ignoring `derived_process_id` returned by the LLM and causing unmapped `process_id` and `stage_id`/`new_stage_id` pairs (reported in org_id 124).
- **feat:** Implemented `reconcile_process_and_stage_id(process_id, stage_id, process_stage_data)` helper in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py):
  - Cross-references `stage_id` against `process_stage_data` (supporting both `stages` and `stageDetails` formats, string/int IDs).
  - Automatically overrides and aligns `process_id` to match the exact process owning `stage_id`.
  - Safely coerces process and stage IDs to clean integers.
- **fix:** Updated `used_kb_stage_ids` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py) to parse `process_stage_data` arrays in accessed KB page metadata.
- **fix:** Updated `finalize()` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py): stores KB tracking hints as `kb_tracked_process_id`/`kb_tracked_stage_id` hints without locking `call_payload["process_id"]`, prioritizes `derived_process_id` from LLM analysis, and executes `reconcile_process_and_stage_id` before constructing the `CALL_DATA_INBOUND_UPDATE` webhook payload.
- **fix:** Fixed multi-process KB ingestion bug in [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py): previously `psd[0]` only parsed the first process in `process_stage_data`, discarding subsequent processes and their stages. Updated to iterate across all processes in `process_stage_data` and construct complete `process_assignments` (containing all processes & stage IDs).
- **fix:** Added `client_name` extraction to `SessionRecorder.analyze_call()` in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py) and `finalize()` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py). Automatically extracts caller's name from spoken conversation transcript for inbound calls and updates `call_payload["client_name"]` in the outgoing `CALL_DATA_INBOUND_UPDATE` webhook payload.
- **fix:** Added `"langdetect>=1.0.9"` dependency to [pyproject.toml](file:///home/fardeen/lkt/pyproject.toml) and updated [uv.lock](file:///home/fardeen/lkt/uv.lock) to fix Docker build failure (`ModuleNotFoundError: No module named 'langdetect'`).
- **feat:** Implemented local PostgreSQL DB call retry tracking in `save_call_log_to_db` in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py): automatically tracks attempt count (`attempt_number`), retry count (`retry_count`), and exact timestamps (`attempted_at`) in the `attempts` JSONB array column of `call_logs` without modifying external outgoing webhook payload contracts.
- Files: [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py), [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py), [pyproject.toml](file:///home/fardeen/lkt/pyproject.toml)

## 2026-08-17

### Instant Language Mirroring & Synchronous Turn Alignment

- **feat:** Implemented `MantraMultilingualAgent` subclassing `Agent` in [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py) overriding `llm_node`. Synchronously inspects the latest user utterance and executes `LanguageManager.process_user_utterance()` immediately before LLM text generation, ensuring system prompt directives and Cartesia TTS options are updated on the exact turn the user speaks without polling lag.
- **perf:** Optimized language switching and speech turnaround latency:
  - Added lexical length and token weighting to `_score_transcript` in [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py), ensuring full spoken sentences in the true language decisively outrank short 1-syllable phantom hallucinations on inactive language streams.
  - Tuned Deepgram child stream `endpointing_ms` (from 100ms to 250ms) and LiveKit `endpointing.min_delay` (from 0.12s to 0.20s), eliminating premature false turn commitments and aborted LLM generations.
  - Eliminated 3.8s KB retrieval retry latency by fixing the `asyncpg` type binding in [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py).
- **fix:** Fixed `TypeError: 'method' object is not iterable` in `llm_node` by calling `chat_ctx.messages()` method properly with type-safe fallback.
- **fix:** Resolved PostgreSQL `asyncpg` type mismatch error in Knowledge Base retriever queries (`expected str, got int`) by stringifying `kb_ids` and `tags` across [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py), and [mantra/retriever.py](file:///home/fardeen/lkt/mantra/retriever.py).
- **fix:** Removed all hardcoded keyword dictionaries (`LANGUAGE_ENTITIES`, `INTENT_INDICATORS`) and character trigram tables. Replaced with clean standardized Unicode script profiling and statistical ML (`langdetect`).
- Files: [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py), [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py), [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py), [mantra/retriever.py](file:///home/fardeen/lkt/mantra/retriever.py)

### All-Ears Multilingual Parallel STT Architecture & Universal Speech Capture

- **feat:** Implemented [`MultilingualParallelSTT`](file:///home/fardeen/lkt/mantra/language_manager.py) and [`MultilingualParallelStream`](file:///home/fardeen/lkt/mantra/language_manager.py) in [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py). Broadcasts incoming microphone audio across 5 dedicated Deepgram streaming WebSockets (`en`, `mr`, `kn`, `te`, `hi`) in parallel, eliminating single-language acoustic filtering.
- **feat:** Integrated `MultilingualParallelSTT` into [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py#L990-L994). The agent is now "all ears" from the start of every call, capturing English, Marathi, Kannada, Telugu, or Hindi seamlessly without requiring initial language pre-configuration or explicit switch requests.
- **feat:** Added real-time transcript arbitration between parallel streams: prioritizes native Indic scripts (`kn`, `te`, `devanagari`) over English hallucinations, eliminates duplicate turns, and synchronizes with `LanguageManager`.
- Files: [mantra/language_manager.py](file:///home/fardeen/lkt/mantra/language_manager.py), [mantra/agent.py](file:///home/fardeen/lkt/mantra/agent.py)

### Multi-Attempt Call Retry Storage & Dashboard Timeline UI

- **feat:** Updated `save_call_log_to_db()` in [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py#L48-L125) to auto-create and append retry attempt objects into the `attempts` `JSONB` column on `call_logs`. Preserves every attempt's timestamp (`attempted_at`), status, AI job ID, duration, summary, and payload without overwriting previous attempts.
- **feat:** Updated `/api/v1/dashboard/calls` endpoint in [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py#L3673-L3755) to return `attempts` array and `attempts_count` for each call record.
- **feat:** Updated Dashboard UI in [static/dashboard.html](file:///home/fardeen/lkt/static/dashboard.html) and [static/dashboard.js](file:///home/fardeen/lkt/static/dashboard.js): added attempt count badges on call history table rows and a detailed **Retry & Attempt History Timeline** modal section displaying exact attempt timestamps, status badges, durations, and AI summaries.
- Files: [mantra/utils.py](file:///home/fardeen/lkt/mantra/utils.py), [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py), [static/dashboard.html](file:///home/fardeen/lkt/static/dashboard.html), [static/dashboard.js](file:///home/fardeen/lkt/static/dashboard.js)

### KB Ingestion JSON & Form Payload Compatibility

- **fix:** Fixed issue where HTTP `POST /api/v1/kb/ingest` failed with `400 Bad Request: org_id is required` when sending text KB payloads with `Content-Type: application/json`.
- **feat:** Updated `ingest_kb_data` in [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py#L952-L985) to inspect request content type and seamlessly parse both `application/json` and `multipart/form-data` / `application/x-www-form-urlencoded` payloads.
- **feat:** Updated `parse_list` helper to handle list data types directly when passed in JSON body payloads.
- **fix:** Fixed `asyncpg.exceptions.UndefinedColumnError: column "embedding" of relation "kb_pages" does not exist` during KB ingestion (`POST /api/v1/kb/ingest`). `PostgresKnowledgeBase.add_page()` in [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py#L270-L310) now checks `self._supports_embeddings(conn)` before attempting to insert into the `embedding` column, with automatic exception fallback to standard FTS insertion if the pgvector `embedding` column does not exist in the database.
- Files: [mantra/ui_server.py](file:///home/fardeen/lkt/mantra/ui_server.py), [mantra/knowledge_base.py](file:///home/fardeen/lkt/mantra/knowledge_base.py)

## 2026-08-14

### Native Script & Dynamic Multilingual Auto-Switching Hardening

- **feat:** Updated prompt rules in `mantra/agent.py` to write responses in the native script of each language (Hindi in Devanagari हिन्दी, Kannada in ಕನ್ನಡ, Telugu in తెలుగు, Marathi in Devanagari मराठी).
- **fix:** Fixed issue where Deepgram STT failed to recognize spoken Kannada/Telugu/Marathi due to 10ms micro-endpointing and restrictive prompt rules.
- **perf:** Tuned Deepgram STT `endpointing_ms=100` for stable multilingual recognition and code-switching without cutting off regional phonetic units.
- **prompt:** Added high-priority `DYNAMIC MULTILINGUAL SWITCHING` rule into `CRITICAL OVERRIDING RULES` in `mantra/agent.py`. Ensures the agent instantly switches whenever the caller speaks in those languages, overriding static prompt restrictions.
- **feat:** Added dynamic STT language resolution when regional languages (`kn`, `te`, `mr`) are specified in call payload.
- Files: `mantra/agent.py`

### Premature `end_call` Trigger Guard & Prompt Hardening

- **fix:** Fixed race condition where DeepSeek LLM called `end_call` tool concurrently with the opening greeting on turn 1, disconnecting calls prematurely after 3 seconds.
- **feat:** Added runtime state checks inside `AssistantFunctions.end_call()`: ignores `end_call` if `not user_has_spoken` and `not initial_greeting_done`, returning a prompt to continue the conversation.
- **prompt:** Updated `end_call` tool docstring and softened overly assertive `ENDING THE CALL` system prompt instructions in `mantra/agent.py` to prevent LLM bias towards immediate call termination.
- Files: `mantra/agent.py`

## 2026-08-13

### Kannada, Telugu & Marathi Language Integration & Instant Switch Latency Optimization

- **feat:** Added native language matching rules and TTS payload resolution for Kannada (`kn`), Telugu (`te`), and Marathi (`mr`) in `mantra/agent.py`.
- **perf:** Language-Switching Latency Tuning (`mantra/agent.py`):
  - Added `INSTANT LANGUAGE SWITCH RULE` to prompt instructions preventing LLM preamble hesitations ("Sure, I can speak Kannada") and forcing instant first-word replies in the requested language.
  - Tightened VAD `min_silence_duration` from 350ms → 250ms (`0.25`s) and endpointing `max_delay` from 700ms → 500ms (`0.5`s) to eliminate turn-detector silence delays on short 2-3 word language switch requests.
- **details:** Deepgram `nova-3` STT operates in `language="multi"` mode to auto-transcribe Kannada, Telugu, and Marathi speech. Cartesia TTS language is dynamically resolved from call payload (`"language": "kn"` / `"language": "te"` / `"language": "mr"`). Configured system prompt script rule forcing DeepSeek to output Kannada in Latin-script **Kanglish**, Telugu in **Telugish**, and Marathi in **Marathish**.
- Files: `mantra/agent.py`

## 2026-08-12

### Outbound SIP Dispatch Latency Optimization

- **perf:** Fixed 4-second API response latency on `/api/v1/sip/plivo/create-and-call` by setting `wait_until_answered=False` on `CreateSIPParticipantRequest`.
- **behavior:** Previously, the HTTP POST request blocked for ~3.5 to 5 seconds waiting for the recipient's phone to physically ring and be picked up over the cellular network. With `wait_until_answered=False`, the API dispatches the SIP call asynchronously and returns `200 OK` (`status: success`) immediately in **<100ms**.
- Files: `mantra/ui_server.py`

### Smart In-Progress Call Deduplication Rejection System

- **feat:** Implemented fail-safe Smart In-Progress Call Deduplication (`lock:call:{call_id}`) across `mantra/ui_server.py` and `mantra/agent.py`:
  - `handle_outbound_call_webhook` (`/api/v1/webhooks/telephony`) & `create_and_call_plivo` (`/api/v1/sip/plivo/create-and-call`) acquire a 30-second TTL lock (`ex=30`).
  - Sub-second duplicate webhooks arriving while a call is currently in-progress are rejected with `{"status": "ignored", "message": "Duplicate request for call_id ... already in progress"}`.
  - When the call completes (or SIP fails), `finalize()` in `agent.py` and `_deliver_call_failure()` in `ui_server.py` delete `lock:call:{call_id}` immediately.
  - Updated Operations Dashboard UI (`static/dashboard.html`, `static/dashboard.js`): added styled status badges and filter dropdown options for `Ignored`, `Incomplete`, and `Busy` statuses.
- Files: `mantra/ui_server.py`, `mantra/agent.py`, `static/dashboard.html`, `static/dashboard.js`

### LiveKit TurnDetector Tuning & Inactivity Monitor Hardening

- **fix:** TurnDetector & Endpointing Misconfiguration (`mantra/agent.py`):
  - Previously, `endpointing` was set to `min_delay: 0.1` and `max_delay: 0.35` (100ms–350ms), overriding LiveKit turn detector recommendations with an ultra-short window that forcefully cut off users mid-sentence whenever they took a brief breath or pause.
  - Reconfigured `endpointing` to recommended LiveKit defaults for `inference.TurnDetector()` (`min_delay: 0.3`, `max_delay: 2.5`, `mode: "dynamic"`). The turn detector now accurately evaluates end-of-turn acoustic/semantic cues without premature interruptions.
- **fix:** VAD Sensitivity Tuning (`mantra/agent.py`):
  - Raised Silero VAD `min_speech_duration` from `0.08`s (80ms) to `0.15`s (150ms) and `min_silence_duration` to `0.35`s (350ms) to eliminate false VAD triggers caused by telephony line clicks, pops, or breathing.
  - Switched `interruption` mode to `"adaptive"` (dropped VAD-mode `resume_false_interruption` / `false_interruption_timeout` overrides) to prevent VAD line-noise false-interruption loops from stuttering agent audio output.
- **fix:** Premature Initial Greeting Inactivity Prompt & Disconnect (`mantra/agent.py`):
  - Fixed bug where `on_user_state` set `initial_greeting_done = True` on early SIP connect line noise, starting the inactivity timer while the agent was still preparing/playing its greeting.
  - Added `greeting_started` flag set when the agent first enters the `speaking` state; `initial_greeting_done` is now set strictly AFTER the agent finishes speaking its opening greeting (guarded by `greeting_started`), and `on_user_state` no longer sets it directly.
  - Increased gentle inactivity nudge threshold from 5.0s → 15.0s, and total silence disconnect threshold from 10.0s → 30.0s. Prevents agent from prematurely saying "hii are you still on the line" at the start of a call or during natural pauses.
- **fix:** Start-of-Call Latency (`mantra/agent.py`):
  - Removed redundant artificial `asyncio.sleep(0.5)` and `asyncio.sleep(0.3)` delays prior to initial greeting generation, and reduced the remote-participant join sleep from 0.5s to 0.05s — unified to a single 0.05s WebRTC track-binding delay, cutting ~0.8s–1.0s of dead air when participants join.
- Files: `mantra/agent.py`

## 2026-08-11

### Post-Call Stage Transition & CRM Stage Details Fix

- **bug:** `SessionRecorder.analyze_call()` in `mantra/utils.py` completely excluded `AVAILABLE CRM STAGES` (`stage_details`) from the LLM prompt whenever `process_stage_data` (KB process stage data) was present. Because of this, for outbound calls and calls with CRM stage lists (e.g. stages 227, 228, 229, 230, 273, 274), the post-call LLM was shown only KB process stage data and could not match the transcript to the valid campaign CRM stage IDs, defaulting `new_stage_id` to `current_stage_id` (no stage transition).
- **fix:** Updated `SessionRecorder.analyze_call()` to always include `AVAILABLE CRM STAGES` (`stage_details`) in the prompt alongside `AVAILABLE PROCESSES` when present, and added explicit prompt instructions directing the LLM to select `new_stage_id` from `AVAILABLE CRM STAGES` based on the outcome (appointment confirmed, call back/follow up, not interested, treatment done, failed).
- **fix:** Moved inbound KB process/stage ID extraction (`used_kb_process_ids` / `used_kb_stage_ids`) in `mantra/agent.py` to BEFORE `SessionRecorder.analyze_call()` executes so `current_stage_id` is properly populated for inbound calls prior to analysis.
- **feat:** Updated stage resolution and `call_status` logic in `mantra/agent.py`: `payload_new_stage_id` is never `null` when an initial stage ID exists — if the stage is not updated, `payload_new_stage_id` sends the initial `stage_id`. `call_status` is `"Completed"` if `payload_new_stage_id != initial_stage_id` (stage updated), and `"Incomplete"` if `payload_new_stage_id == initial_stage_id` (stage stayed the same).
- Files: `mantra/agent.py`

## 2026-08-10

### Post-Call Analysis Hardening — `next_call_on` & Stage Transition Injection

- **bug:** `next_call_on` was empty (`""`) in post-call payloads even when the transcript requested a callback (e.g. "Call me back in 10 minutes"). Root cause: the relative-callback regex fallback only ran in the success path of `SessionRecorder.analyze_call()`. When the LLM returned non-JSON output (common with flash models), the exception path hard-set `next_call_on = None` and never ran the regex.
- **bug:** Stage transitions were lost on the same LLM failures — a call that clearly booked a demo (stage "said yes to book demo") was sent as `new_stage_id: null` with `call_status: "Incomplete"` because the exception path hard-set `new_stage_id = fallback_stage_id` (current stage).
- **fix:** Added `parse_relative_callback()` helper in `mantra/utils.py` (module-level, shared by both paths) — handles "in 10 minutes", "1 hour", "N days", Hindi "10 minute baad", and "tomorrow at 3 PM"/"tomorrow". Runs in the success path (when LLM returns null) AND in the exception path.
- **fix:** Added `infer_stage_from_transcript()` helper in `mantra/utils.py` — deterministic keyword matching (word-boundary, negation-guarded) of transcript/summary against stage descriptions. Runs in the exception path and as a fallback when the LLM reports no transition, so demo bookings still advance to the correct stage.
- **fix:** Hardened JSON parsing in `analyze_call()` — handles ` ``` ``` `/` ```json ```` fences and extracts the balanced `{...}` object from prose-wrapped/mangled LLM output instead of failing outright.
- Files: `mantra/utils.py`

### Post-Call Analysis — LLM Sole Source of Truth

- **refactor:** Removed all regex/heuristic fallbacks from `SessionRecorder.analyze_call()` (`mantra/utils.py`) — the post-call LLM is now the sole source of truth for AI-decided fields. Deleted `parse_relative_callback()` and `infer_stage_from_transcript()`, removed the top-level `import re`, and dropped the `next_call_on` regex fallback, the `process_id`/`new_stage_id` auto-fill from `process_stage_data`, and transcript keyword stage inference in both the success and exception paths.
- **behavior:** `process_id` and current `stage_id` now come exclusively from the webhook payload (outbound) or KB tracking (`used_kb_process_ids` / `used_kb_stage_ids`, inbound). `new_stage_id`, `ai_summary`, `user_intent`, `next_call_on`, `appointment_date_time`, `doctor`, `hospital_location`, and `sentiment_score` come from the LLM only.
- **refactor:** Exception path now falls back cleanly: `summary` via `generate_summary()`, `new_stage_id = current_stage_id` (no transition), `process_id = None`, `next_call_on = None`.
- **perf:** `generate_summary()` bounded by 45s `wait_for`; `analyze_call` LLM inner timeout raised 25s → 60s (utils.py) and outer timeout 30s → 70s (agent.py) to fit `deepseek-v4-pro` (benchmarked 12–22s on a demo-booking transcript).
- Files: `mantra/utils.py`, `mantra/agent.py`

### Inbound Call Post-Call `user_intent` Payload Field (Booked, Cancelled, Rescheduled)

- **feat:** Extended `user_intent` field in post-call `CALL_DATA_INBOUND_UPDATE` webhook payload for inbound calls to support 3 distinct appointment outcomes: `"APPOINTMENT_BOOKED"`, `"APPOINTMENT_CANCELLED"`, and `"APPOINTMENT_RESCHEDULED"`.
- **feat:** LLM call analysis in `SessionRecorder.analyze_call()` (`mantra/utils.py`) evaluates conversation history against KB process/stage descriptions:
  - `"APPOINTMENT_BOOKED"` iff an appointment was successfully booked during the call AND the outcome stage corresponds to appointment booking/confirmation.
  - `"APPOINTMENT_CANCELLED"` iff an existing appointment was cancelled during the call AND the outcome stage corresponds to appointment cancellation.
  - `"APPOINTMENT_RESCHEDULED"` iff an existing appointment was rescheduled to a new date/time during the call AND the outcome stage corresponds to appointment rescheduling.
  - `null` in all other cases (e.g. general inquiry, no appointment action).
- **feat:** Updated `finalize()` in `mantra/agent.py` to allow `"APPOINTMENT_BOOKED"`, `"APPOINTMENT_CANCELLED"`, or `"APPOINTMENT_RESCHEDULED"` in `CALL_DATA_INBOUND_UPDATE` payload sent to MantraAssist backend.
- **fix:** Inbound Greeting & Name Assumption:
  - Removed hardcoded `Identify yourself: 'Mantra Care'` instruction in `mantra/agent.py` which caused agents on inbound calls to greet with "Hi this is Mantra Care". Instructed agent to strictly follow custom prompt brand identity (e.g. `MantraAssist` or `Arushi`).
  - Stopped injecting stored DB `client_name` (e.g., `'Anurag'` from `org_configs`) into `ADDITIONAL CALL CONTEXT` on inbound calls so the agent never assumes an incoming caller's identity before they introduce themselves.
  - Added Turn-by-Turn Inbound Flow: Turn 1 (Greeting & "How can I help?"), Turn 2 (Caller states intent → Agent acknowledges and asks for caller's name before proceeding), Turn 3+ (Agent addresses request using caller's name naturally).
- **feat:** Post-Call Stage & Status Logic for No Transition:
  - When no stage transition occurs during a call (i.e. `new_stage_id` remains the same as initial `stage_id` or no useful outcome was achieved), `mantra/agent.py` now sends `new_stage_id: null` (empty data) instead of repeating `stage_id`.
  - Sets `call_status` to `"Incomplete"` in the webhook payload (`CALL_DATA_UPDATE` & `CALL_DATA_INBOUND_UPDATE`) when no new stage transition was made.
- **feat:** E.164 Inbound Phone Formatting:
  - Added `format_e164_phone_number()` helper function in `mantra/utils.py`.
  - Updated `CALL_DATA_INBOUND_UPDATE` payload in `mantra/agent.py` to ensure `client_phone_number` is formatted with country code and a leading `+` (e.g. `+918360625862`).
- **fix:** Mandatory Initial Greeting Generation on Connect:
  - Removed conditional `if not call_state.get("user_has_spoken")` check during call startup in `mantra/agent.py`.
  - Previously, if the caller said "Hello" while WebRTC audio/STT was initializing, `user_has_spoken` became `True` and bypassed `session.generate_reply()`, causing 18 seconds of silence if STT missed the first utterance.
  - Now, `session.generate_reply()` is **always** triggered immediately upon call connect, guaranteeing zero silence on every call.
- **fix:** Relative Callback `next_call_on` Calculation:
  - Enhanced Task 3 prompt in `SessionRecorder.analyze_call()` (`mantra/utils.py`) to instruct relative date/time calculations (e.g., "call back in 10 minutes", "in 1 hour", "tomorrow at 3 PM") relative to current server time.
  - Added automatic Python regex fallback in `analyze_call()` that detects relative callback requests (e.g. `call me back in 10 minutes`) from call summary/transcript and calculates `next_call_on = current_time + timedelta(...)` automatically if the LLM output is missing.
- Files: `mantra/agent.py`, `mantra/amd.py`, `mantra/utils.py`

## 2026-08-09

### KB Retrieval Fix — Hybrid FTS + Semantic (pgvector/Gemini) + Tiered Fallback

- **bug:** Fixed org 77 inbound call failing to find "diagnostic codes" despite the KB page containing "diagnostic code". Root cause: `kb_pages.text_search` used the `simple` config with `websearch_to_tsquery` AND semantics — `'diagnostic' & 'codes'` never matched the stored `code` (no stemming, no OR fallback). Verified in scratch DB: `diagnostic codes` → 0 rows, `code`/`diagnostic` → 1 row.
- **feat:** Migration `006_kb_english_vector.py` — rebuilds `text_search` with the `english` config (stemming), enables the `vector` extension, adds `embedding vector(1536)`, and creates an HNSW index (`embedding vector_cosine_ops`). 1536 dims because pgvector HNSW/IVFFlat caps at 2000 dimensions (Gemini `output_dimensionality=1536` verified working).
- **feat:** New `mantra/gemini_embeddings.py` — lazy `google.genai.Client` singleton, `embed_texts()` (batch 100, 3 retries, exp backoff), `embed_text()`, `embedding_enabled()`. Uses `gemini-embedding-2` @ 1536 dims. NOTE: batch embeddings must use `types.Content(parts=[...])` — plain strings returned only 1 embedding for 2 inputs.
- **feat:** `mantra/knowledge_base.py` now performs tiered search: Tier A strict FTS (`english`) blended with pgvector cosine via Reciprocal Rank Fusion (`_blend_results`), soft-tag retry, Tier B loose OR query, Tier C tag-only, Tier D `list_available()` document listing. New query builders: `build_loose_search_query`, `build_vector_search_query`, `build_tag_search_query`, `build_list_docs_query`. `add_page()`/`ingest_text()` embed chunks up front with graceful FTS-only fallback when embeddings unavailable. Abstract `list_available` added.
- **feat:** `mantra/retriever.py` rewritten — tiered `retrieve()` with session cache; when nothing matches, returns the list of available documents (with tags) instead of a bare "no results", so the LLM can ask a better follow-up or answer truthfully. `_format_no_results` builds the doc list.
- **feat:** New `tools/backfill_embeddings.py` — idempotent/resumable backfill of `embedding` for existing rows (`--kb-id`, `--batch-size`, `--limit`, `--dry-run`). URL-encodes the DB password in the DSN.
- **feat:** Backfill logic extracted into `PostgresKnowledgeBase.backfill_embeddings()` and exposed two ways for Docker-only deployments: `POST /api/v1/kb/backfill-embeddings` HTTP endpoint (`mantra/ui_server.py`, body JSON `kb_id`/`batch_size`/`limit`/`dry_run`) and a `backfill` mode in `entrypoint.sh` (`docker run <image> backfill [args]`). Also added `mantra/migrations/006_kb_english_vector.sql` (transactional SQL variant of the migration). Both paths verified against scratch DB.
- **verified:** End-to-end scratch repro of the exact failure: org 77 page with `diagnostic code`, query "diagnostic codes" → page found; "diagnostics" → found (stemming); "what is there in your knowledge base" → truthful doc listing; "appointment booking" → doc listing (no hallucination). `get_kb_ids_for_org` / `get_collection_details_for_org` confirmed working. 1536-dim embedding stored + HNSW index validated.
- **unchanged:** `mantra/agent.py` — the `search_knowledge_base` tool already flows through `retriever.retrieve()`, so no code change needed there. Prod DB (52.7.20.203) untouched — migration SQL + backfill commands provided to the user to run.
- Files: `mantra/knowledge_base.py`, `mantra/retriever.py`, `mantra/ui_server.py`, `mantra/gemini_embeddings.py` (new), `mantra/migrations/006_kb_english_vector.py` (new), `mantra/migrations/006_kb_english_vector.sql` (new), `tools/backfill_embeddings.py` (new), `entrypoint.sh`

## 2026-08-07

### KB Process & Stage Persistence for Inbound Webhooks

- **feat:** Migration `005_kb_process_stage.py` — added `process_id`, `stage_id`, `stage_ids`, `process_assignments`, `process_description`, and `stage_description` columns to `kb_collections`, and `stage_id` to `org_configs`.
- **feat:** Updated `/api/v1/kb/ingest` in `mantra/ui_server.py` to parse Zod-schema aligned `process_assignments` (`[{"process_id": int, "stage_ids": [int]}]`) as well as explicit `process_id` and `stage_id` parameters, storing them directly on `kb_collections` and `kb_pages.page_meta`.
- **fix:** Updated `normalize_datetime()` in `mantra/utils.py` to output ISO-8601 UTC timestamp format (`YYYY-MM-DDTHH:MM:SSZ`, e.g. `"2026-08-04T10:59:36Z"`), returning `null` when no callback is scheduled for both inbound and outbound webhooks.
- **fix:** Updated `SessionRecorder.analyze_call()` in `mantra/utils.py` to evaluate `next_call_on` using a 3-tier priority: (1) User spoken callback time during the call, (2) Default stage callback delay instruction from `process_stage_data`/`stageDetails` in the call payload, (3) `null` if neither is present.
- **fix:** Updated 5-second silence prompt in `mantra/agent.py` to use natural Hindi phrasing (`"हेलो, क्या आप लाइन पर हैं?"`).
- Files: `mantra/utils.py`, `mantra/agent.py`

### AMD Exception Handling & Unevaluated LLM Compatibility

- **fix:** Updated `detect_voicemail()` in `mantra/amd.py` to handle early audio stream closures (`RuntimeError("amd closed before a result was available")`) as clean `info` logs rather than logging noisy multiline Python stack traces.
- **fix:** Added `suppress_compatibility_warning=True` to `AMD(...)` instantiation in `mantra/amd.py` for unevaluated LLMs like `deepseek-v4-flash`.
- Files: `mantra/amd.py`

## 2026-08-06

### Grafana Memory & CPU Arc Gauges

- **feat:** Added authentic Grafana Arc Gauge widgets for **Memory** and **CPU Utilization** to `static/network.html`.
- **feat:** Rendered 180° semi-circular arc gauge canvas with outer thin threshold ring (Green <70%, Orange 70-90%, Red >90%), inner progress arc fill, and centered dynamic value text (`114 MB`, `14.2%`).
- Files: `static/network.html`

### Call Retry Redis Deduplication Lock & DB Persistence

- **fix:** Resolved duplicate suppression bug where retrying a call within 5-10 minutes of a previous attempt logged `"delivered"` but skipped sending HTTP POST requests to the n8n backend.
- **fix:** Enhanced `_claim_backend_delivery` and `send_to_backend` in `mantra/utils.py` to deduplicate by `call_id + ai_call_id` (`f"backend_sent:{call_id}_{ai_call_id}"`).
- **fix:** Removed automatic `force=True` on `CALL_RETRY` inside `send_to_backend()`, eliminating double HTTP POST deliveries caused by the background Redis queue worker (`mantra:pending_webhooks`).
- **fix:** Updated `handle_outbound_call_webhook` in `mantra/ui_server.py` to automatically clear `backend_sent` and `lock:call:{call_id}` locks when a retry dispatch arrives.
- **feat:** Added automatic PostgreSQL persistence inside `send_to_backend()` in `mantra/utils.py` so every delivered webhook payload (including retries) is saved/upserted into `call_logs` and audited in `call_events`.
- Files: `mantra/utils.py`, `mantra/ui_server.py`

### Operations Dashboard DB Sync & Search

- **feat:** Updated `/api/v1/dashboard/calls` endpoint in `mantra/ui_server.py` to support `search` filtering (across call ID, caller/called phone numbers, and call log JSON text) and `status` filtering (`Completed`, `Busy`, `No Answer`, `Error`, `Incomplete`).
- **feat:** Updated `static/dashboard.html` and `static/dashboard.js` with search bar, status selector dropdown, manual "Sync DB" button, and auto-sync triggers on SSE call-end events and periodic 15s intervals.
- **feat:** Enhanced Call Details Inspect Modal in `static/dashboard.html` and `static/dashboard.js` to parse and render full AI summaries and turn-by-turn conversation transcripts (with styled 🤖 AI Agent and 👤 Caller speech bubbles, unicode Hindi/English support, and raw JSON fallback).

### Redis Operations & Queue Monitor

- **feat:** Created `/redis` route in `mantra/ui_server.py` serving `static/redis.html`.
- **feat:** Added Redis API endpoints: `/api/v1/redis/info` (server info, memory, clients, queue count, active count), `/api/v1/redis/queue` (inspect `queue:pending` sorted set items & payloads), `/api/v1/redis/active-details` (inspect `calls:active` hash, status, lock TTLs), `/api/v1/redis/keys` (scan and list keys by pattern with type and TTL), `/api/v1/redis/key-detail` (full value inspector), `/api/v1/redis/key` (delete key).
- **feat:** Created `static/redis.html` UI with summary metric cards, queue inspector table, active calls hash viewer, live key explorer, and JSON value inspector modal.

### Grafana-Style Network Telemetry Dashboard

- **feat:** Redesigned `static/network.html` with an authentic Grafana Dark Theme aesthetic (`#111217` canvas, `#181b1f` panels, Grafana orange/blue/green/red color palette).
- **feat:** Added top control toolbar with time range selector (`Last 5m`, `15m`, `30m`), refresh interval selector (`2s`, `5s`, `10s`, `Off`), and manual refresh.
- **feat:** Added single stat cards for Request Rate (RPS), Avg Response Latency (ms) + estimated p95, Error Rate %, RAM Memory usage, and CPU seconds.
- **feat:** Added time-series line charts for Throughput (RPS), Latency Distribution (Avg/p95), HTTP Status Code Breakdown over time (`2xx`, `3xx`, `4xx`, `5xx`), and Top API Endpoints volume bar chart.
- **feat:** Added high-density Grafana endpoint metrics grid table with status code class filtering (`2xx`, `3xx`, `4xx`, `5xx`), live search filtering, and human-readable API endpoint labels (mapping raw technical paths like `/api/v1/stream` → **Real-Time SSE Stream** and `/api/v1/redis/active-details` → **Redis Active Calls Inspector**).

### Redis Webhook Worker Failover & Read-Only Replica Reconnection Fix

- **fix:** Updated `process_pending_webhooks()` worker in `mantra/ui_server.py` to handle Redis failovers (Master → Replica transitions) and socket read timeouts.
- **fix:** Added exception handlers for `ReadOnlyError` ("You can't write against a read only replica") and `ResponseError` ("UNBLOCKED force unblock..."). When failover occurs, the worker logs a warning, closes/disconnects the stale Redis connection pool (`await client.aclose()`), resets `client = None`, and reconnects cleanly on the next iteration.
- **fix:** Configured `socket_timeout=15`, `socket_connect_timeout=5`, `health_check_interval=15`, and `retry_on_timeout=True` on `redis.from_url` to prevent socket hanging and ensure swift reconnects.
- Files: `mantra/ui_server.py`, `static/dashboard.html`, `static/dashboard.js`, `static/redis.html`, `static/network.html`, `static/index.html`, `static/kb_chat.html`

## 2026-08-05

### Standard-String Timestamps for `next_call_on`

- **fix:** Renamed `normalize_to_iso8601` → `normalize_datetime` in `mantra/utils.py`. It now returns `next_call_on` as a standard server-local time string (`YYYY-MM-DD HH:MM:SSZ`, `Z` suffix appended) instead of ISO-8601 (`YYYY-MM-DDTHH:MM:SS`).
- **fix:** `analyze_call` LLM prompt in `mantra/utils.py` no longer hardcodes IST — `next_call_on` / `appointment_date_time` are now instructed to be emitted in the server's local time (`YYYY-MM-DD HH:MM:SS`), matching the `datetime.now()`-based `current_time_str`.
- **fix:** Updated `mantra/agent.py` import and both `CALL_DATA_INBOUND_UPDATE` / `CALL_DATA_UPDATE` webhook payloads to use the renamed helper, so `next_call_on` is delivered as a plain string in server-local time.
- Files: `mantra/utils.py`, `mantra/agent.py`

## 2026-08-04

### Post-Call Webhook Delivery & Finalize Refactoring

- **fix:** Added `_finalized` single-execution guard in `finalize()` in `mantra/agent.py` to prevent duplicate post-call execution and double-webhook delivery.
- **fix:** Guarded all `fnc_ctx` and `recorder` accesses in `finalize()` against uninitialized/None states, preventing `AttributeError` / `UnboundLocalError` from terminating finalization early.
- **fix:** Added 15s timeout on `SessionRecorder.analyze_call` and 10s timeout on `upload_to_s3` in `mantra/agent.py` so slow LLM or S3 requests never block post-call backend delivery.
- **fix:** Added 12s timeout to `stream.collect()` in `mantra/utils.py` `analyze_call` method.
- **fix:** Reduced Redis claim TTL in `_claim_backend_delivery` from 3600s (1 hour) to 300s (5 minutes) so failed/interrupted claims don't lock `call_id` retries for an hour.
- **feat:** Created `scripts/reprocess_unsent_calls.py` to automatically scan PostgreSQL for connected calls that missed backend webhook delivery and re-deliver their payloads.
- Files: `mantra/agent.py`, `mantra/utils.py`, `scripts/reprocess_unsent_calls.py`

### Webhook Schema & Inbound Call KB Analysis

- **fix:** Enhanced `get_process_stage_data_for_kb_ids` in `mantra/knowledge_base.py` to query both `kb_pages` (`page_meta->process_stage_data`) and `kb_collections` fallback (`process_description`, `stage_description`), ensuring KB process/stage structures are always present for inbound call analysis.
- **fix:** Fixed missing `process_id` in `SessionRecorder.analyze_call` return dictionary in `mantra/utils.py` — previously `analyze_call` dropped `process_id`, causing `derived_process_id` to evaluate to `None` and downstream PDO prepared statement parameter errors.
- **fix:** Added automatic fallback extraction for `process_id` and `stage_id` from `process_stage_data` in `analyze_call` when LLM returns null or fails to parse.
- **fix:** Inbound calls perform full KB analysis using `SessionRecorder.analyze_call` on conversation history against `process_stage_data` (sourced from accessed KB pages or queried directly from `kb_pages` / `kb_collections` in DB by `kb_ids`).
- **fix:** The derived `process_id` and `stage_id` / `new_stage_id` are populated in the standard `CALL_DATA_INBOUND_UPDATE` payload and sent to the n8n backend webhook without altering the payload schema.
- Files: `mantra/agent.py`, `mantra/utils.py`, `mantra/knowledge_base.py`

## 2026-08-03

### Trunk-Based Call Capacity Gating

- **refactor:** Replaced per-provider capacity (`PROVIDER_MAX_CONCURRENCY`) with per-trunk capacity. Each trunk gets an independent limit derived from its provider's default (Plivo=2, Zadarma=3, VoiceLink=5, Twilio=3).
- **refactor:** Room naming changed from `call_{provider}_{call_id}` → `call_{trunk_id}_{call_id}` across webhook handler, Plivo outbound endpoint, and dispatcher.
- **feat:** `_resolve_trunk_limit(trunk_id)` — resolves trunk→provider→limit via in-memory `_TRUNK_TO_PROVIDER` cache, falling back to `_get_provider_from_trunk()` (LiveKit API + Redis cache). Unknown trunks default to limit 1.
- **feat:** `_trunk_at_capacity(trunk_id)` and `_active_per_trunk(rooms, trunk_id)` replace `_provider_at_capacity` and `_active_per_provider`. Counting uses room name prefix matching on `call_{trunk_id}_`.
- **feat:** `_extract_trunk_ids(rooms)` — parses trunk IDs from room names (`call_ST_abc_123` → `ST_abc` by `rsplit("_", 1)`).
- **refactor:** Health check now reports per-trunk capacity (`trunk_capacity_{trunk_id}`) instead of per-provider.
- **refactor:** Middleware gate now uses `_trunk_at_capacity(trunk_id)` — two Plivo trunks each get independent 2-call pool.
- **chore:** Renamed `PROVIDER_MAX_CONCURRENCY` → `PROVIDER_DEFAULT_CONCURRENCY` (twilio limit changed 2→3).
- Files: `mantra/ui_server.py` (lines 64-138, 556-567, 672-690, 2350-2376, 2875), `mantra/dispatcher.py` (lines 51, 187)

### Zombie Room Cleanup

- **feat:** `cleanup_zombie_rooms()` in `mantra/dispatcher.py` — runs every 60s in the dispatcher loop. Lists LiveKit rooms, deletes `call_*` rooms with `num_participants == 0` via `delete_room()`.
- **feat:** One-shot startup cleanup in `ui_server.py` lifespan — identical logic, runs immediately after startup health check. Writes warning log for each zombie deleted.
- Files: `mantra/dispatcher.py` (lines 117-136, 169), `mantra/ui_server.py` (lines 214-232)

### DB Migration — Call Metadata Columns

- **feat:** Added `caller_number` (VARCHAR 20), `called_number` (VARCHAR 20), `trunk_id` (VARCHAR 100) to `call_logs`. Index on `trunk_id`.
- **feat:** `save_call_log_to_db()` in `mantra/utils.py` now accepts and upserts all three columns.
- **feat:** Agent `finalize()` extracts `caller_number` ← `call_payload.call_from`, `called_number` ← `call_payload.client_phone`, `trunk_id` ← `call_payload.call_from_id`.
- **feat:** `_log_blocked_call()` now passes `caller_number` from `payload.call_from` alongside existing `trunk_id` and `phone`.
- Migration: `migrations/add_trunk_fields.sql`

### DB Migration — kb_collections Process/Stage Descriptions

- **feat:** Added `process_description` (TEXT), `stage_description` (TEXT) to `kb_collections`.
- **feat:** Ingest endpoint (`/api/v1/kb/ingest`) extracts first process's `description`/`name` and first stage's `description`/`name` from `process_stage_data` JSON.
- **feat:** `get_or_create_collection()` (abstract + Postgres impl) accepts and upserts both columns.
- **feat:** `list_collections()` returns the new columns.
- Migration: `migrations/add_trunk_fields.sql`
- Files: `mantra/knowledge_base.py` (lines 94, 255, 270), `mantra/ui_server.py` (lines 909-932)

## 2026-08-02

### Inbound webhook int coercion + language matching

- **fix:** `CALL_DATA_INBOUND_UPDATE` coerces `org_id` / `process_id` / `new_stage_id` string→int when present; missing stays `null`.
- **fix:** Language matching prompt + STT `language=multi` so agent does not stick in Hindi after one Hindi filler.

## 2026-08-01

### Inbound SIP Setup — Plivo Zentrunk Trunk Reuse & Retry Self-Healing

- **fix:** Plivo Zentrunk inbound trunk is now found by its deterministic name (`Inbound via LiveKit ({domain})`) as well as by `primary_uri_uuid` in `_update_plivo_sip_forwarding()`. Previously a trunk created for an earlier number was missed by the URI-only match, so setup for a new number tried to create a duplicate and Plivo rejected it with "A trunk with the same name ... already exists" — even though no trunk was created by this attempt.
- **feat:** `_plivo_list_all()` — paginated helper for Plivo list endpoints (URI + trunk), so lookups no longer miss objects past the default 20-item page.
- **feat:** When a reused Zentrunk trunk points to a stale URI, it is repointed (`primary_uri_uuid`) to the current LiveKit SIP domain.
- **fix:** Retries after a failed provider config no longer return a false `409 number_already_configured`. The 409 gate now verifies, for Plivo, that the number is genuinely linked to the domain's Zentrunk trunk (`_plivo_number_is_linked_to_zentrunk()`); if not, setup falls through and completes idempotently reusing the existing LiveKit trunk + dispatch rule.
- **fix:** `org_configs` is now written only after provider forwarding succeeds, so a DB row reflects an actually-configured number instead of a partially-failed setup.

### Per-Provider Call Capacity & Health Gating

- **feat:** Per-provider concurrency limits in `ui_server.py` — `PROVIDER_MAX_CONCURRENCY` (`plivo: 2`, `zadarma: 3`, `voice_link: 5`), env-overridable via `PLIVO_MAX_CONCURRENCY` / `ZADARMA_MAX_CONCURRENCY` / `VOICELINK_MAX_CONCURRENCY`.
- **feat:** `/health` now reports per-provider and global capacity — returns `{"healthy": false}` when any provider is at its limit or total live `call_*` rooms reach `MAX_CALL_CONCURRENCY` (5, `CARTESIA_MAX_CONCURRENCY` fallback). Health check keys: `provider_capacity_{provider}`, `capacity_max_concurrency`.
- **feat:** Middleware `health_gate_middleware` (POST dispatch paths only) — per-provider gate for `/api/v1/webhooks/telephony` returns empty `503` when the call's provider is saturated; global gate returns `503` when live rooms ≥ `MAX_CALL_CONCURRENCY`; dependency gate still blocks `503` on infra failure. Provider saturation never blocks another provider's traffic.
- **feat:** Provider embedded in LiveKit room name for zero-Redis tracking — `call_{provider}_{call_id}` (e.g. `call_plivo_t1`, `call_voice_link_v1`); unknown trunks → `call_unknown_{id}` (not counted, not blocked).
- **feat:** `_log_blocked_call()` → `save_call_log_to_db(status="Busy")` with structured JSON (provider, `blocked: true`, `reason: provider_at_concurrency_limit`, active/max, trunk, phone, timestamp) when the per-provider gate rejects a call.
- **refactor:** Split `_run_dependency_checks()` (infra only, used by the coarse gate) from `_run_health_checks()` (deps + capacity, used by `/health`); `BYPASS_HEALTH_CHECKS` honored in both.
- **refactor:** `_get_provider_from_trunk()` returns `str | None` — removed `DEFAULT_PROVIDER="zadarma"`; all providers resolved equally via address inference + `voicelink_client` fallback; only non-None results cached in Redis.
- **fix:** `_active_call_rooms()` now re-raises LiveKit errors (fail-closed) instead of silently returning `[]` — previously caused intermittent gate bypass (200 instead of 503); middleware converts exceptions to `503`, `/health` records `capacity_max_concurrency: "error: ..."`.
- **fix:** Plivo SIP failure statuses now surface as `503` from the webhook — `handle_outbound_call_webhook` awaits `trigger_sip()` instead of fire-and-forget, and returns an empty `503` (matching the capacity gate) when the SIP call fails (408→`No Answer`, 486→`Busy`, other→`Incomplete`). Previously the webhook returned `200` even when the call never connected. The SIP failure classification is still written to Redis `sip_error_status:{call_id}` and the room is deleted.
- **chore:** Production cleanup — removed test/demo tooling (`tools/test_provider_capacity.py`, `tools/seed_rooms.py`, `tools/demo_7_calls.sh`, `tools/live_capacity_smoke.sh`) and `docs/CAPACITY_SYSTEM.md`; restored `_check_postgres()` in dependency checks; removed stale blank lines / dead code.

## 2026-07-30

### Voicelink Integration & SIP Fixes

- **fix:** Resolved `NameError: name 'voicelink_client' is not defined` by adding module-level client/session declarations and lifespan initialization.
- **feat:** Added `voice_link` / `voicelink` inbound SIP setup support and `_update_voicelink_sip_forwarding()` handler.
- **feat:** Updated inbound SIP trunk name default fallback to `{provider} {number}`.
- **feat:** Added Redis-backed trunk-to-provider caching in `_get_provider_from_trunk()` with `voicelink_client` fallback lookup for existing trunks.
- **fix:** Added `voice_link` branch in outbound SIP client selection to route through `voicelink_client` (proxied), preventing 408 SIP timeout errors.
- **refactor:** Cleaned up `_get_provider_from_trunk()` logic for cleaner execution and error logging.

### Outbound Call Walkthrough

- **doc:** Created `Architecture/Outbound Call Walkthrough.md` — full end-to-end trace from webhook payload through post-call processing with payload samples, code references, sequence diagram, failure modes, and design properties table

## 2026-07-29

### Multi-KB per Org — KB Collections

- **feat:** Added `kb_collections` table — each row = one document = one KB collection for an org. `kb_pages.kb_id` now stores the collection UUID instead of `org_id`. New migration: `003_kb_collections.py`.
- **feat:** `_resolve_from_db()` in `agent.py` now queries `kb_collections` to get all collection UUIDs for the org, plus the `org_id` as fallback for legacy data. Agent searches across all collections.
- **feat:** `/api/v1/kb/ingest` endpoint now creates/finds a `kb_collection` by `(org_id, document_id)` and stores pages under the collection UUID. Old data with `kb_pages.kb_id = org_id` still works via fallback.
- **feat:** New API endpoints: `GET /api/v1/kb-collections?org_id=X`, `GET /api/v1/kb-collections/{id}`, `DELETE /api/v1/kb-collections/{id}` for collection management.
- **feat:** `PostgresKnowledgeBase` gains 4 new methods: `get_or_create_collection`, `list_collections`, `delete_collection`, `get_kb_ids_for_org`.
- **refactor:** `delete_by_document` now removes pages by `document_id` across all KBs (not filtered by `kb_id`), and also cleans up the `kb_collections` row.
- **doc:** Updated `Database.md` with `kb_collections` table schema and KB resolution flow.

### Inbound KB Document Tracking

- **feat:** Added `accessed_pages_meta` tracking to `KnowledgeRetriever` — every `retrieve()` call now appends `page_meta` from returned pages, enabling per-document metadata extraction
- **feat:** Added `used_kb_process_ids` property to `AssistantFunctions` — reads tracked `page_meta` and returns unique `process_id` values from KB documents actually searched during the call
- **feat:** Inbound `finalize()` now overlays `process_id` from tracked KB pages into `call_payload` — the `CALL_DATA_INBOUND_UPDATE` webhook carries the `process_id` of the specific document the agent queried, not the org-level `org_configs.process_id`
- Files: `mantra/retriever.py` (lines 11, 44-46), `mantra/agent.py` (lines 298-314, 1248-1256)

## 2026-07-27

### TTS Fix & Payload Cleanup

- **fix:** TTS model changed from `cartesia/sonic-3` to `sonic-3` (LiveKit native inference, no Cartesia dependency)
- **fix:** Removed `emotion` extra_kwarg from TTS config (not supported by LiveKit inference)
- **fix:** Removed Cartesia API health check (TTS now uses LiveKit inference only)
- **fix:** Reverted TOS `Post-call processing complete` telemetry back to fire-and-forget (`create_bg_task`) — synchronous wait caused unnecessary blocking
- **feat:** Added `called_on` field to both n8n webhook payload and TOS telemetry data (maps from `call_initiated_at`)
- **chore:** Added payload body logging in `send_to_backend()` for easier debugging

## 2026-07-26

### Post-Call Data & Timestamps

- **fix:** `normalize_to_iso8601` outputs `YYYY-MM-DDTHH:mm:ss` (local, no offset) for `next_call_on` — matches MA reschedule format requirement
- **fix:** Post-call TOS telemetry (`[Agent Worker] Post-call processing complete`) now awaited directly instead of fire-and-forget bg task — ensures delivery of summary, transcript flag, stage, and timestamps to TOS
- **feat:** Added `call_initiated_at` (set by UI server on webhook receipt), `agent_joined_at`, `human_joined_at` as explicit fields in n8n webhook payload and TOS telemetry
- **feat:** `_telemetry()` helper in `entrypoint()` accepts `wait=True` for critical telemetry that must not be lost

### TOS Telemetry Cleanup

- **feat:** `report_telemetry()` now accepts optional `data` dict for structured payloads
- **feat:** Post-call processing sends rich TOS payload with `s3_recording`, `has_transcript`, `summary`, `new_stage_id`, `call_status`, `duration_seconds`, `backend_delivered`, `next_call_on`, appointment fields
- **refactor:** Stripped verbose debug logging from `report_telemetry()` (no more request body/headers printed)
- **refactor:** Stage telemetry messages use natural language (`"Call ended by agent"`, `"Customer joined the call"`, `"Agent voice engine ready"`)
- **chore:** Removed `log_io.py` colorama logging + `colorama` dependency (clean for production)

### Timezone Fix

- **fix:** `next_call_on` (scheduled call time) now sent in UTC in both `agent.py` and `utils.py` fallbacks
- **fix:** `email_alerts.py` crash timestamp uses local time (was labelled `UTC` but showed local)

## Earlier 2026-07-26

### TOS Telemetry & Health Gate

- **feat:** Added `report_telemetry()` to `mantra/utils.py` — POSTs structured telemetry logs to TOS endpoint (`/api/telemetry/{task_id}/log`). Used across all three services.
  - `AssistantFunctions.__init__` now parses `tos_task_id` from `job_metadata` and provides `_telemetry()` helper for tool callbacks.
  - Agent `entrypoint()` now reports: `agent_started`, `room_connected`, `participant_joined`, `voice_engine_initialized`, `post_processing_started`, `data_sent_to_backend`, `call_complete`.
  - Dispatcher reports: `call_dequeued`, `call_dispatched`, `dispatch_failed`.
  - UI server reports: `webhook_received`, `agent_dispatched`, `sip_call_initiating`, `sip_call_connected`, `sip_call_failed`.
- **feat:** Added `health_gate_middleware` to `ui_server.py` — blocks dispatch requests (`POST /dispatch-test`, `/api/v1/webhooks/telephony`, SIP trunk endpoints) with HTTP 503 if any critical service is down.
- **feat:** Comprehensive startup healthcheck — runs parallel checks on LiveKit, Redis, Deepgram, TTS (LiveKit native sonic-3), MantraAssist backend, PostgreSQL, S3 on server start.
- **feat:** Redis deduplication lock (`lock:call:{call_id}`, TTL 600s) on `handle_outbound_call_webhook` and `create_and_call_plivo` to prevent concurrent duplicate webhooks.
- **feat:** Room participant check in SIP failure handler — before cleanup, verifies SIP participant isn't already in room (duplicate guard from race condition fix v2).
- **fix:** `send_to_backend` URL corrected from `/webhooks/n8n` to `/api/v1/webhooks/n8n`.
- **refactor:** Removed Redis concurrency management (`calls:active`, `calls:status`) from `agent.py` — call tracking responsibility shifted to dispatcher + telemetry.
- **refactor:** Added persistent `httpx.AsyncClient` to UI server lifespan for all health checks.
- **refactor:** `get_db_connection` now prefers `DATABASE_URL` env var over individual PG env vars.
- **chore:** Logger handler guard in `ui_server.py` — prevents duplicate handler attachment.
- **chore:** `logger.propagate` set to `True` in `ui_server.py` for consistent log visibility.

## 2026-07-25

- **feat:** Comprehensive `/health` readiness endpoint — returns `healthy` / `stay` only when ALL services pass
  - Checks: LiveKit API, Redis, PostgreSQL, Deepgram STT, TTS (LiveKit native sonic-3), n8n backend, TOS endpoint, S3 bucket
  - Runs all checks in parallel with individual timeouts
  - Returns HTTP 200 + `"status": "healthy"` when every service is reachable
  - Returns HTTP 503 + `"status": "stay"` with per-service error breakdown otherwise
  - Files: `mantra/ui_server.py`

## 2026-07-24

- **fix:** Three-layer race condition hardening for outbound call webhooks
  - Increased Redis dedup lock TTL 30s → 600s to prevent late duplicates from passing through
  - Added room participant check in `trigger_sip` exception handler — skips cleanup if SIP participant already in room (duplicate guard)
  - Fixed agent `call_status` logic: only trust Redis SIP error if `user_joined` is False — prevents stale duplicate error from overriding real "Completed" status
  - Files: `mantra/ui_server.py` (lock TTL, room check), `mantra/agent.py` (Redis trust gate)

## 2026-07-24

- **fix:** Raised concurrency limits — `AgentServer(num_idle_processes)` 1→20, `livekit.toml` replicas 1→2, `MAX_CONCURRENCY`/`LIVEKIT_MAX_ROOMS`/`AGENT_MAX_WORKERS` 5→20 across `.env`, `.env.local`. Root cause: agent deployment was pinned to 1 replica with 1 idle worker, capping effective concurrency at ~1-2 calls regardless of service-side limits.
- **doc:** Updated Environment.md capacity section

## 2026-07-23

- **fix:** Plivo inbound call — migrated from Plivo Application XML to Plivo Zentrunk SIP trunking. `_update_plivo_sip_forwarding` now creates Zentrunk origination URI → inbound trunk → links number via Plivo API. Deprecated `_build_plivo_xml`, `/api/v1/sip/plivo-xml`, `/api/v1/sip/plivo-dial-status`. Root cause: Plivo `<User>` Dial sends SIP INVITE that LiveKit rejects (UNALLOCATED_NUMBER); Zentrunk sends authenticated INVITE directly to LiveKit's SIP domain, matching the inbound trunk's numbers array.
- **doc:** Updated Obsidian vault: Current Sprint, Changelog

## 2026-07-22

- **analysis:** Inbound call + KB prod-readiness review on live Plivo call (org 66)
- **bug:** MCP server fails at startup — `module 'livekit.agents.llm.mcp' has no attribute 'CstdioServerParameters'` — DB tool unavailable to agent
- **bug:** KB retriever returned zero results for org 66 across 5 queries — likely empty `kb_pages` table, not a code issue
- **bug:** Post-call webhook to n8n returns 404 — ngrok endpoint lacks `/webhooks/n8n` route
- **bug:** Handoff TTS glitch — residual `"..."` utterance causes `APIError` traceback after `transfer_to_human` (race between tool return and silence instructions)
- **ops:** `AWS_S3_BUCKET_NAME` not set — recordings skipped
- **doc:** Confirmed `transfer_to_human` is fully implemented (not commented out as TODO claimed)
- **doc:** Updated Obsidian vault: Current Sprint, TODO, Components, Voice Agent, Architecture Overview

## 2026-07-21

- **fix:** Inbound call webhook payload now includes `direction`, `inbound_context` (org_id, kb_id, phone_number, provider) so MantraAssist backend can correlate inbound call results
- **fix:** Resolved inbound context (org_id, kb_id, etc.) now flows through to `finalize()` via `_effective_call_metadata` closure variable instead of being lost during metadata re-parse
- **fix:** MCP `call_logs` tool was broken (no SQL executed) — rewritten to properly upsert into `call_logs`
- **fix:** `test_inbound_call` and `create_dispatch_rule` endpoints now normalize `phone` → `phone_number` so agent can resolve inbound context
- **safety:** All new code paths wrapped in try/except with `non-fatal` logging; direction defaults to `"outbound"` so outbound system is completely unaffected
- **logging:** Added structured logging for webhook payload construction (direction, call_status, call_id) and inbound context addition

## 2026-07-18

- **feat:** DB Inbound Context Resolution: Added `org_configs` table and integration in `/api/v1/sip/inbound/setup` to map incoming phone numbers to organizations in the database. The agent now queries this DB first for context (prompt, voice, KB scope), supplementing the MantraAssist API. Added `/api/v1/org-configs` CRUD endpoints for backend management.
- **feat:** Add color-coded logging for inbound SIP setup requests/responses in `mantra/ui_server.py`
- **fix:** Knowledge Ingestion Encoding: Stripped null bytes (`\x00`) recursively from metadata and text inputs in `PostgresKnowledgeBase.add_page` and `delete_by_document` to prevent `CharacterNotInRepertoireError` (invalid byte sequence for UTF8).
- **feat:** Ingestion Logger: Added request parameters logging at the start of `/api/v1/kb/ingest` in `mantra/ui_server.py`.
- **refactor:** Env Loading: Updated `ui_server.py`, `dispatcher.py`, and migrations to load `.env` first and override with `.env.local` using `override=True` to resolve environment conflicts (e.g., Redis host/port).

## 2026-07-16

- **feat:** Added local inbound mappings fallback — `inbound_mappings.json` for testing KB + inbound call integration without the external MantraAssist backend
  - `resolve_inbound_context()` now falls back to local JSON config when the backend is unreachable
  - Set `LOCAL_INBOUND_MAPPINGS=1` env var to skip backend entirely and use local mappings only
- **doc:** Synced Obsidian vault with actual codebase state after KB audit
  - `Features/Knowledge Base.md` — Corrected from "pgvector + upfront prompt injection" to "PostgreSQL FTS + function tool RAG"; added known gaps, tag filtering docs, and accurate schema
  - `Architecture/Components.md` — Fixed line counts (agent.py: 1513, ui_server.py: 2143), added KB module section
  - `Architecture/Data Flow.md` — Added KB context resolution steps to inbound call flow
  - `Context/Repository Map.md` — Fixed line counts, added `knowledge_base.py` and `retriever.py`
  - `Context/Stack.md` — Updated PostgreSQL description to include KB FTS
  - `Home.md` — Fixed stats (7 modules, 6,206 total lines)

## 2026-07-03

- **feat:** Knowledge Base: Implemented absolute override 5-rule framework to force agent to answer factual questions directly (overriding strict prompt constraints like "never give advice")
- **refactor:** Knowledge Base: Made all prompt rules completely generic and industry-agnostic, removing hardcoded references to specific verticals like OCD/ERP

## 2026-06-30

- **doc:** Created `obsidian/` — comprehensive Obsidian knowledge base (48 files)
  - `Architecture/` — 8 files (overview, components, data flow, APIs, DB, infra, decisions, deps)
  - `Features/` — 10 files (index + 9 feature pages covering all modules)
  - `Development/` — 7 files (sprint, TODO, backlog, bugs, changelog, releases, roadmap)
  - `Agents/` — 6 files (master, backend, frontend, devops, docs, QA agent guides)
  - `Knowledge/` — 7 files (standards, conventions, best practices, commands, debugging, env, glossary)
  - `Context/` — 5 files (project summary, stack, repo map, external services)
  - `Templates/` — 3 file templates
  - `Inbox/` — placeholder
- **doc:** Updated root `README.md` to reference the Obsidian vault

## 2026-06-15

- **refactor:** Renamed `CARTESIA_MAX_CONCURRENCY` to `MAX_CONCURRENCY` + env var fallback
- **refactor:** Migrated Cartesia TTS to LiveKit Inference, removed redundant API key management
- **feat:** Webhook-based call log storage, updated DB query schemas
- **feat:** Dynamic tone and style configurations for agent prompts

## 2026-05

- **feat:** Emotional tone optimization for Cartesia voice synthesis
- **feat:** `end_call` tool with graceful disconnect + safety net
- **feat:** Automated crash email notifications
- **feat:** Gemini LLM integration
- **feat:** DeepSeek LLM integration
- **feat:** Plivo India proxy routing
- **feat:** Redis queue-based dispatcher system
- **feat:** Dashboard with SSE real-time metrics
- **feat:** JWT authentication
- **refactor:** Bilingual STT (Deepgram Hindi model)
- **refactor:** Custom ColorFormatter for multi-process logging
