# Database Schema

## PostgreSQL Database

The application uses a single table `call_logs` in an isolated database.

### Table: `call_logs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Auto-increment ID |
| `call_id` | TEXT UNIQUE NOT NULL | Unique call identifier |
| `call_log` | JSONB | Complete call data payload |
| `status` | TEXT | Call status (Completed, Busy, No Answer, Error, Incomplete) |
| `recording_url` | TEXT | S3 URL of recording |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | Record creation timestamp |

**Queries:**
- Insert/update: `INSERT ... ON CONFLICT (call_id) DO UPDATE SET ...`
- Dashboard metrics: Aggregation by status with duration averaging
- Call history: Paginated listing with JSONB field extraction

### Table: `kb_pages`

Vector-enabled table for Knowledge Base documents (uses `pgvector`).

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PRIMARY KEY | Auto-generated UUID |
| `kb_id` | TEXT | Maps to `org_id`. Identifies which org the document belongs to. |
| `title` | TEXT | Document title |
| `content` | TEXT | Markdown content of the document |
| `source_type` | TEXT | e.g. `website`, `document`, `manual` |
| `embedding` | VECTOR(1536) | OpenAI embedding vector |
| `page_meta` | JSONB | Additional metadata (e.g., tags, URLs) |
| `content_in_text` | TEXT | Plain text representation for indexing |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | Record creation timestamp |

### Table: `org_configs`

Maps an inbound phone number to an organization and provides its specific agent configuration.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PRIMARY KEY | Auto-generated UUID |
| `org_id` | TEXT | Identifier linking to the organization (and its KB) |
| `phone_number` | TEXT UNIQUE | The inbound DID number |
| `name` | TEXT | Display name for the mapping |
| `prompt` | TEXT | System prompt instructing the agent |
| `voice` | TEXT | Agent voice (e.g., 'arushi') |
| `model` | TEXT | LLM model (e.g., 'deepseek') |
| `kb_tags` | TEXT[] | Specific KB tags to restrict search within the org |
| `transfer_numbers` | JSONB | Mapping of departments to phone numbers |
| `client_name` | TEXT | Default caller name |
| `process_id` | TEXT | External process/workflow identifier |
| `sip_trunk_id` | TEXT | Associated LiveKit SIP Trunk ID |
| `dispatch_rule_id` | TEXT | Associated LiveKit SIP Dispatch Rule ID |
| `is_active` | BOOLEAN | Whether this mapping is currently active |
| `created_at` | TIMESTAMPTZ | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

### Connection

Managed via `asyncpg`. Connection parameters from environment:
```
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT
```

Default port mapping: `5433` (local `lkdb` docker-compose) / `5432` (container internal)

---

## Redis Data Structures

| Key Pattern | Type | Purpose | TTL |
|-------------|------|---------|-----|
| `queue:pending` | Sorted Set | Call queue (score = priority) | — |
| `calls:active` | Hash | `call_id → room_name` | — |
| `calls:status:{call_id}` | String | Per-call status | — |
| `sip_error_status:{call_id}` | String | SIP failure detail | 300s |

Connection via `REDIS_URL` env var.
