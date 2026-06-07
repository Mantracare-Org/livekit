# MCP Database Server

Model Context Protocol server for PostgreSQL database access, providing tools for schema introspection, call logging, and appointment booking.

## Local Setup

1. **Install Dependencies**:

   ```bash
   uv add "mcp[cli]" asyncpg python-dotenv
   ```

2. **Environment Variables**:

   ```env
   POSTGRES_USER=user
   POSTGRES_PASSWORD=password
   POSTGRES_DB=main_db
   POSTGRES_PORT=5433
   POSTGRES_HOST=localhost
   ```

3. **Run the Server**:
   ```bash
   uv run python mcp/server.py
   ```

Or use the dev script which starts everything together:
```bash
./dev.sh
```

## Production

When deploying to production (e.g., via Docker):

1. **Update Environment Variables**:
   - `POSTGRES_HOST`: Change from `localhost` to `postgres` (or your database service name/endpoint).
   - `POSTGRES_PORT`: Change from `5433` to `5432` (internal container port).

2. **Secrets Management**:
   Use CI/CD secrets or a secrets manager for `POSTGRES_PASSWORD` instead of hardcoding it in `.env`.

3. **Docker Networking**:
   Ensure the MCP server container is in the same network as the `postgres` container.

Start the MCP server via the container:
```bash
docker exec <container> uv run python mcp/server.py
```

## Available Tools

### Schema Introspection
| Tool | Description |
|------|-------------|
| `list_tables` | Lists all public tables |
| `describe_table(table_name)` | Shows columns, types, and nullability for a table |
| `execute_query(query)` | Executes a read-only SELECT/WITH query |
| `get_db_status` | Checks connection status and reports table stats |

### Call Logging
| Tool | Description |
|------|-------------|
| `insert_call_log(log_data)` | Inserts a row into the `call_logs` table with FK violation retry |
| `get_call_history(identifier, limit=5)` | Retrieves recent call history for a patient/lead by ID or phone |

### Appointment Booking
| Tool | Description |
|------|-------------|
| `get_patient_info(identifier)` | Looks up patient by phone number, patient ID, or lead ID |
| `get_hospitals()` | Lists all hospital locations |
| `get_doctors(hospital="")` | Lists doctors, optionally filtered by hospital |
| `get_available_slots(doctor_id, date)` | Checks available appointment slots for a doctor on a date |
| `create_appointment(...)` | Books a new appointment (patient, doctor, time, hospital, notes) |
| `update_appointment(appointment_id, updates)` | Updates/reschedules/cancels an existing appointment |
| `get_appointments(patient_id, doctor_id, date, status)` | Queries appointments with optional filters |

## Database Schema

The tools are designed to work with a typical healthcare CRM schema.
Expected tables include:

- `patients` / `leads` — Patient information (id, name, phone)
- `doctors` — Doctor profiles (id, name, specialty, hospital)
- `hospitals` / `locations` — Hospital/location info (id, name, address)
- `appointments` — Appointment records (patient_id, doctor_id, date, time, status)
- `call_logs` — Call history records (lead_id, call_id, direction, status, etc.)

The tools auto-discover table/column names at runtime and adapt queries accordingly.
No hardcoded schema is assumed beyond these naming conventions.
