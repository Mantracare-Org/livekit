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
