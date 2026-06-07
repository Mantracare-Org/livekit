import asyncio
import os
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP
import asyncpg
import datetime
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

mcp = FastMCP("Postgres-Database-Server")

DB_USER = os.getenv("POSTGRES_USER", "user")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
DB_NAME = os.getenv("POSTGRES_DB", "main_db")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5433")

async def get_db_connection():
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
        logger.error(f"Error connecting to database: {e}")
        return None

# ──────────────────────────────────────────
# EXISTING TOOLS (schema introspection)
# ──────────────────────────────────────────

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
    """Get the schema details of a specific table.

    Args:
        table_name: The name of the table to describe.
    """
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
    """Execute a read-only SQL query and return the results.

    Only SELECT and WITH (CTE) queries are allowed for safety.

    Args:
        query: The SQL query to execute (must start with SELECT or WITH).
    """
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
    """Insert a new call log entry into the call_logs table.

    The call_logs table is expected to have columns matching the keys of log_data.
    Common columns include: lead_id, call_id, client_phone, direction, call_status,
    call_transcript, ai_summary, recording_url, call_duration_seconds,
    next_call_on, ai_call_id, new_stage_id, notes, metadata,
    client_custom_fields, call_custom_fields.

    Args:
        log_data: Dictionary of column_name -> value pairs to insert.
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        columns = ", ".join(log_data.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(log_data)))
        values = []
        for val in log_data.values():
            if isinstance(val, str):
                try:
                    if len(val) >= 10 and (val[4] == "-" and val[7] == "-"):
                        values.append(datetime.datetime.fromisoformat(val.replace("Z", "+00:00")))
                    elif val.strip().strip("*-• ").lower() in ["none", "null", "n/a", "na", ""] or "none" in val.lower():
                        values.append(None)
                    else:
                        values.append(val)
                except (ValueError, TypeError):
                    values.append(None)
            else:
                values.append(val)

        query = f"INSERT INTO call_logs ({columns}) VALUES ({placeholders}) RETURNING id"

        try:
            row = await conn.fetchrow(query, *values)
        except asyncpg.exceptions.ForeignKeyViolationError as fk_err:
            if "lead_id" in str(fk_err) and "lead_id" in log_data:
                logger.info(f"Foreign Key violation on lead_id={log_data['lead_id']}. Retrying without lead_id...")
                keys_list = list(log_data.keys())
                idx = keys_list.index("lead_id")
                keys_list.pop(idx)
                values.pop(idx)
                columns = ", ".join(keys_list)
                placeholders = ", ".join(f"${i+1}" for i in range(len(keys_list)))
                retry_query = f"INSERT INTO call_logs ({columns}) VALUES ({placeholders}) RETURNING id"
                row = await conn.fetchrow(retry_query, *values)
                await conn.close()
                return f"Successfully inserted call log with ID: {row['id']} (lead_id omitted due to FK violation)"
            raise fk_err

        await conn.close()
        return f"Successfully inserted call log with ID: {row['id']}"
    except Exception as e:
        if conn:
            await conn.close()
        return f"Error inserting call log: {e}"


# ──────────────────────────────────────────
# APPOINTMENT BOOKING TOOLS
# ──────────────────────────────────────────
#
# These tools expect a schema commonly found in healthcare CRM systems.
# Expected tables: patients / leads, doctors, hospitals, appointments.
# Column mappings can be adjusted once the actual schema is confirmed
# from MantraAssist.

@mcp.tool()
async def get_patient_info(identifier: str) -> str:
    """Look up patient information by phone number, patient ID, or lead ID.

    Searches across patients/leads tables to find the caller's record.
    Returns patient name, phone, ID, and any relevant context.

    Expected tables: patients or leads (with columns: id, name, phone, etc.)

    Args:
        identifier: Phone number (with or without +), patient ID, or lead ID.
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
        await conn.close()
    except Exception as e:
        return f"Error: {e}"

    # Normalize phone: strip + and spaces
    search_term = identifier.strip().lstrip("+").replace(" ", "")

    # Try common patient/lead table names
    patient_tables = [t for t in table_names if t in ("patients", "leads", "patient", "lead")]
    if not patient_tables:
        # Try fuzzy match
        patient_tables = [t for t in table_names if "patient" in t.lower() or "lead" in t.lower()]
    if not patient_tables:
        return "No patients or leads table found in the database. Expected table names: 'patients' or 'leads'."

    results = []
    for table in patient_tables:
        table = table  # already a string
        try:
            conn2 = await get_db_connection()
            if not conn2:
                continue

            # Describe the table to find relevant columns
            cols = await conn2.fetch(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            # Build a query based on available columns
            select_cols = ["id"]
            for col in col_names:
                if col in ("name", "full_name", "patient_name", "client_name", "first_name"):
                    select_cols.append(col)
                elif col in ("phone", "mobile", "phone_number", "contact_number", "client_phone"):
                    select_cols.append(col)
                elif col in ("email",):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))  # dedupe, preserve order
            select_expr = ", ".join(select_cols)

            # Search by phone or ID
            row = None
            phone_cols = [c for c in col_names if c in ("phone", "mobile", "phone_number", "contact_number", "client_phone")]
            for pcol in phone_cols:
                query = f"SELECT {select_expr} FROM {table} WHERE {pcol} LIKE $1 LIMIT 1"
                row = await conn2.fetchrow(query, f"%{search_term}")
                if row:
                    break

            if not row:
                id_cols = [c for c in col_names if c in ("id", "lead_id", "patient_id", "client_id")]
                for icol in id_cols:
                    query = f"SELECT {select_expr} FROM {table} WHERE {icol}::text = $1 LIMIT 1"
                    row = await conn2.fetchrow(query, search_term)
                    if row:
                        break

            if row:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))

            await conn2.close()
        except Exception as e:
            logger.error(f"Error searching {table}: {e}")
            continue

    if results:
        return "Found patient(s):\n" + "\n".join(results)
    return f"No patient found matching '{identifier}'. Searched tables: {', '.join(patient_tables)}."


@mcp.tool()
async def get_hospitals() -> str:
    """List all hospital locations available for appointments.

    Expected table: hospitals or locations (with columns: id, name, address, etc.)
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    hospital_tables = [t for t in table_names if t in ("hospitals", "locations", "centers", "branches")]
    if not hospital_tables:
        hospital_tables = [t for t in table_names if "hospital" in t.lower() or "center" in t.lower() or "branch" in t.lower() or "location" in t.lower()]

    if not hospital_tables:
        await conn.close()
        return "No hospitals/locations table found. Expected table names: 'hospitals', 'locations', 'centers', 'branches'."

    results = []
    for table in hospital_tables:
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            select_cols = ["id"]
            for col in col_names:
                if col in ("name", "hospital_name", "center_name", "location_name", "branch_name"):
                    select_cols.append(col)
                elif col in ("address", "city", "location", "area"):
                    select_cols.append(col)
                elif col in ("phone", "contact"):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))
            select_expr = ", ".join(select_cols)

            rows = await conn.fetch(f"SELECT {select_expr} FROM {table} ORDER BY name LIMIT 50")
            for row in rows:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))
        except Exception as e:
            logger.error(f"Error listing {table}: {e}")
            continue

    await conn.close()
    if results:
        return "Available hospitals/locations:\n" + "\n".join(results)
    return "No hospitals found."


@mcp.tool()
async def get_doctors(hospital: str = "") -> str:
    """List doctors, optionally filtered by hospital/location name.

    Expected table: doctors (with columns: id, name, specialty, hospital_id, etc.)

    Args:
        hospital: Optional hospital name or location to filter by.
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    doctor_tables = [t for t in table_names if t in ("doctors", "doctor", "physicians")]
    if not doctor_tables:
        doctor_tables = [t for t in table_names if "doctor" in t.lower() or "physician" in t.lower()]

    if not doctor_tables:
        hospital_tables_for_join = [t for t in table_names if t in ("hospitals", "locations", "centers", "branches")]
        if not hospital_tables_for_join:
            hospital_tables_for_join = [t for t in table_names if "hospital" in t.lower() or "center" in t.lower() or "branch" in t.lower() or "location" in t.lower()]

        # Maybe doctors are stored differently - check for doctor-related columns in other tables
        for table in table_names:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]
            if "doctor" in col_names or "doctor_name" in col_names or "doctor_id" in col_names:
                doctor_tables.append(table)

    if not doctor_tables:
        await conn.close()
        return ("No doctors table found. Expected table names: 'doctors', 'physicians'. "
                "Also checked for doctor columns in other tables.")

    results = []
    for table in doctor_tables:
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            select_cols = ["id"]
            for col in col_names:
                if col in ("name", "doctor_name", "full_name", "first_name"):
                    select_cols.append(col)
                elif col in ("specialty", "specialization", "department", "speciality"):
                    select_cols.append(col)
                elif col in ("hospital", "hospital_name", "hospital_id", "location", "center"):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))
            select_expr = ", ".join(select_cols)

            if hospital:
                # Try to filter by hospital name/location
                filter_cols = [c for c in col_names if c in ("hospital", "hospital_name", "location", "center")]
                if filter_cols:
                    filter_conditions = " OR ".join(f"{c} ILIKE $1" for c in filter_cols)
                    rows = await conn.fetch(
                        f"SELECT {select_expr} FROM {table} WHERE {filter_conditions} ORDER BY name LIMIT 50",
                        f"%{hospital}%"
                    )
                else:
                    rows = await conn.fetch(f"SELECT {select_expr} FROM {table} ORDER BY name LIMIT 50")
            else:
                rows = await conn.fetch(f"SELECT {select_expr} FROM {table} ORDER BY name LIMIT 50")

            for row in rows:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))
        except Exception as e:
            logger.error(f"Error listing doctors from {table}: {e}")
            continue

    await conn.close()
    if results:
        return "Available doctors:\n" + "\n".join(results)
    return "No doctors found."


@mcp.tool()
async def get_available_slots(doctor_id: str, date: str) -> str:
    """Check available appointment slots for a doctor on a given date.

    Expected table: appointments or slots (with columns: doctor_id, date, time, status, etc.)

    Args:
        doctor_id: The doctor's ID (as stored in the doctors table).
        date: Date to check in YYYY-MM-DD format (e.g., "2026-06-10").
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    # Look for appointments table or doctor_schedules/slots
    appt_tables = [t for t in table_names if t in ("appointments", "slots", "doctor_schedules", "doctor_slots")]
    if not appt_tables:
        appt_tables = [t for t in table_names if "appointment" in t.lower() or "slot" in t.lower() or "schedule" in t.lower()]

    if not appt_tables:
        await conn.close()
        return ("No appointments or slots table found. Expected table names: "
                "'appointments', 'slots', 'doctor_schedules', 'doctor_slots'.")

    results = []
    for table in appt_tables:
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            select_cols = ["id"]
            for col in col_names:
                if col in ("doctor_id", "doctor", "doctor_name"):
                    select_cols.append(col)
                elif col in ("date", "slot_date", "appointment_date", "day"):
                    select_cols.append(col)
                elif col in ("time", "slot_time", "start_time", "appointment_time", "from_time", "to_time"):
                    select_cols.append(col)
                elif col in ("status", "slot_status", "is_available", "available", "is_booked"):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))
            select_expr = ", ".join(select_cols)

            # Find which column holds the doctor ID
            doctor_cols = [c for c in col_names if c in ("doctor_id", "doctor", "doctor_name")]

            # Find date columns
            date_cols = [c for c in col_names if c in ("date", "slot_date", "appointment_date", "day")]

            # Find status columns (to check availability)
            status_cols = [c for c in col_names if c in ("status", "slot_status", "is_available", "available", "is_booked")]

            # Build query
            conditions = []
            params = []
            param_idx = 1

            if doctor_cols:
                conditions.append(f"{doctor_cols[0]}::text = ${param_idx}")
                params.append(doctor_id)
                param_idx += 1

            if date_cols:
                conditions.append(f"{date_cols[0]}::text LIKE ${param_idx}")
                params.append(f"%{date}%")
                param_idx += 1

            where_clause = " AND ".join(conditions) if conditions else "TRUE"

            rows = await conn.fetch(
                f"SELECT {select_expr} FROM {table} WHERE {where_clause} ORDER BY time NULLS LAST LIMIT 50",
                *params
            )

            for row in rows:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))

            if not rows:
                results.append(f"No slots found for doctor_id='{doctor_id}' on date '{date}' in table '{table}'.")
        except Exception as e:
            logger.error(f"Error checking slots in {table}: {e}")
            results.append(f"Error querying {table}: {e}")
            continue

    await conn.close()
    if results:
        return "Available slots:\n" + "\n".join(results)
    return f"No available slots found for doctor '{doctor_id}' on '{date}'."


@mcp.tool()
async def create_appointment(
    patient_id: str,
    doctor_id: str,
    slot_time: str,
    hospital: str = "",
    notes: str = "",
    patient_name: str = "",
    patient_phone: str = "",
) -> str:
    """Book a new appointment for a patient with a doctor.

    Expected table: appointments (with columns: patient_id, doctor_id,
    appointment_date, appointment_time, hospital, notes, status, etc.)

    Args:
        patient_id: The patient/lead ID.
        doctor_id: The doctor's ID.
        slot_time: Appointment date and time (e.g., "2026-06-10 10:30" or "2026-06-10T10:30:00").
        hospital: Hospital/location name or ID (optional).
        notes: Additional notes for the appointment (optional).
        patient_name: Patient's name for reference (optional).
        patient_phone: Patient's phone number for reference (optional).
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    # Look for appointments table
    appt_tables = [t for t in table_names if t in ("appointments",)]
    if not appt_tables:
        appt_tables = [t for t in table_names if "appointment" in t.lower()]

    if not appt_tables:
        await conn.close()
        return ("No appointments table found. Cannot create appointment. "
                "Expected table: 'appointments'.")

    table = appt_tables[0]

    try:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            table
        )
        col_names = [c['column_name'] for c in cols]

        # Parse slot_time into date and time parts
        slot_time = slot_time.strip()
        date_part = slot_time
        time_part = ""

        if " " in slot_time:
            date_part, time_part = slot_time.split(" ", 1)
        elif "T" in slot_time:
            date_part, time_part = slot_time.split("T", 1)
            time_part = time_part[:5]  # HH:MM

        # Build insert dynamically based on available columns
        insert_data = {}

        # Map patient_id to the right column
        patient_cols = [c for c in col_names if c in ("patient_id", "lead_id", "client_id", "patient")]
        if patient_cols:
            insert_data[patient_cols[0]] = patient_id
        else:
            insert_data["patient_id"] = patient_id

        # Map doctor_id
        doctor_cols = [c for c in col_names if c in ("doctor_id", "doctor")]
        if doctor_cols:
            insert_data[doctor_cols[0]] = doctor_id
        else:
            insert_data["doctor_id"] = doctor_id

        # Map date
        date_cols = [c for c in col_names if c in ("appointment_date", "date", "slot_date")]
        if date_cols:
            insert_data[date_cols[0]] = date_part
        else:
            insert_data["appointment_date"] = date_part

        # Map time
        if time_part:
            time_cols = [c for c in col_names if c in ("appointment_time", "time", "slot_time", "start_time")]
            if time_cols:
                insert_data[time_cols[0]] = time_part
            else:
                insert_data["appointment_time"] = time_part

        # Map hospital
        if hospital:
            hosp_cols = [c for c in col_names if c in ("hospital", "location", "center", "hospital_name", "branch")]
            if hosp_cols:
                insert_data[hosp_cols[0]] = hospital
            else:
                insert_data["hospital"] = hospital

        # Map notes
        if notes:
            notes_cols = [c for c in col_names if c in ("notes", "description", "remarks", "comments")]
            if notes_cols:
                insert_data[notes_cols[0]] = notes
            else:
                insert_data["notes"] = notes

        # Map status
        status_cols = [c for c in col_names if c in ("status", "appointment_status")]
        if status_cols:
            insert_data[status_cols[0]] = "scheduled"
        else:
            insert_data["status"] = "scheduled"

        # Map patient_name
        if patient_name:
            name_cols = [c for c in col_names if c in ("patient_name", "client_name", "full_name")]
            if name_cols:
                insert_data[name_cols[0]] = patient_name

        # Map patient_phone
        if patient_phone:
            phone_cols = [c for c in col_names if c in ("patient_phone", "client_phone", "phone", "mobile")]
            if phone_cols:
                insert_data[phone_cols[0]] = patient_phone

        # Check if created_at exists
        if "created_at" in col_names:
            insert_data["created_at"] = datetime.datetime.now()

        # Execute insert
        columns = ", ".join(insert_data.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(insert_data)))
        values = list(insert_data.values())

        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id"
        row = await conn.fetchrow(query, *values)

        await conn.close()

        appointment_id = row['id']
        return (f"Successfully created appointment (ID: {appointment_id}) "
                f"for patient '{patient_id}' with doctor '{doctor_id}' on {slot_time}. "
                f"Table: {table}")
    except Exception as e:
        if conn:
            await conn.close()
        return f"Error creating appointment: {e}"


@mcp.tool()
async def update_appointment(appointment_id: str, updates: dict) -> str:
    """Update an existing appointment (reschedule, cancel, change doctor, etc.).

    Expected table: appointments

    Common update fields:
    - status: "cancelled", "rescheduled", "completed", "confirmed"
    - appointment_date: new date (YYYY-MM-DD)
    - appointment_time: new time (HH:MM)
    - doctor_id: different doctor
    - hospital: different location
    - notes: additional notes

    Args:
        appointment_id: The appointment ID to update.
        updates: Dictionary of column -> new value pairs.
    """
    if not updates:
        return "Error: No updates provided."

    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    appt_tables = [t for t in table_names if t in ("appointments",)]
    if not appt_tables:
        appt_tables = [t for t in table_names if "appointment" in t.lower()]

    if not appt_tables:
        await conn.close()
        return "No appointments table found."

    table = appt_tables[0]

    try:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            table
        )
        col_names = [c['column_name'] for c in cols]

        # Find the primary key column
        id_col = "id" if "id" in col_names else col_names[0]

        # Build SET clause
        set_parts = []
        values = []
        param_idx = 1
        for key, val in updates.items():
            if key in col_names:
                set_parts.append(f"{key} = ${param_idx}")
                values.append(val)
                param_idx += 1

        if not set_parts:
            await conn.close()
            return f"Error: None of the provided fields exist in the {table} table."

        values.append(appointment_id)
        query = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {id_col}::text = ${param_idx}"

        result = await conn.execute(query, *values)
        await conn.close()

        return f"Appointment {appointment_id} updated successfully. Fields changed: {', '.join(updates.keys())}."
    except Exception as e:
        if conn:
            await conn.close()
        return f"Error updating appointment: {e}"


@mcp.tool()
async def get_appointments(patient_id: str = "", doctor_id: str = "", date: str = "", status: str = "") -> str:
    """Query appointments with optional filters.

    Expected table: appointments

    Args:
        patient_id: Filter by patient/lead ID (optional).
        doctor_id: Filter by doctor ID (optional).
        date: Filter by date (YYYY-MM-DD) (optional).
        status: Filter by status (e.g., "scheduled", "completed", "cancelled") (optional).
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    appt_tables = [t for t in table_names if t in ("appointments",)]
    if not appt_tables:
        appt_tables = [t for t in table_names if "appointment" in t.lower()]

    if not appt_tables:
        await conn.close()
        return "No appointments table found. Expected table: 'appointments'."

    results = []
    for table in appt_tables:
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            select_cols = ["id"]
            for col in col_names:
                if col in ("patient_id", "lead_id", "client_id", "patient", "patient_name"):
                    select_cols.append(col)
                elif col in ("doctor_id", "doctor", "doctor_name"):
                    select_cols.append(col)
                elif col in ("appointment_date", "date", "slot_date", "appointment_time", "time", "slot_time"):
                    select_cols.append(col)
                elif col in ("hospital", "location", "center"):
                    select_cols.append(col)
                elif col in ("status", "appointment_status"):
                    select_cols.append(col)
                elif col in ("notes", "description"):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))
            select_expr = ", ".join(select_cols)

            # Build WHERE clause
            conditions = []
            params = []
            param_idx = 1

            if patient_id:
                patient_cols = [c for c in col_names if c in ("patient_id", "lead_id", "client_id", "patient")]
                if patient_cols:
                    pat_conditions = " OR ".join(f"{c}::text = ${param_idx}" for c in patient_cols)
                    conditions.append(f"({pat_conditions})")
                    params.append(patient_id)
                    param_idx += 1

            if doctor_id:
                doctor_cols = [c for c in col_names if c in ("doctor_id", "doctor")]
                if doctor_cols:
                    doc_conditions = " OR ".join(f"{c}::text = ${param_idx}" for c in doctor_cols)
                    conditions.append(f"({doc_conditions})")
                    params.append(doctor_id)
                    param_idx += 1

            if date:
                date_cols = [c for c in col_names if c in ("appointment_date", "date", "slot_date")]
                if date_cols:
                    date_conditions = " OR ".join(f"{c}::text LIKE ${param_idx}" for c in date_cols)
                    conditions.append(f"({date_conditions})")
                    params.append(f"%{date}%")
                    param_idx += 1

            if status:
                status_cols = [c for c in col_names if c in ("status", "appointment_status")]
                if status_cols:
                    stat_conditions = " OR ".join(f"{c}::text ILIKE ${param_idx}" for c in status_cols)
                    conditions.append(f"({stat_conditions})")
                    params.append(status)
                    param_idx += 1

            where_clause = " AND ".join(conditions) if conditions else "TRUE"

            rows = await conn.fetch(
                f"SELECT {select_expr} FROM {table} WHERE {where_clause} ORDER BY appointment_date NULLS LAST, time NULLS LAST LIMIT 50",
                *params
            )

            for row in rows:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))

            if not rows:
                results.append(f"No appointments found matching the criteria in '{table}'.")
        except Exception as e:
            logger.error(f"Error querying appointments from {table}: {e}")
            results.append(f"Error querying {table}: {e}")
            continue

    await conn.close()
    if results:
        return "Appointments:\n" + "\n".join(results)
    return "No appointments found."


@mcp.tool()
async def get_call_history(identifier: str, limit: int = 5) -> str:
    """Retrieve call history for a patient/lead by ID or phone number.

    Expected table: call_logs (with columns: lead_id, client_phone, direction,
    call_status, call_duration_seconds, ai_summary, created_at, etc.)

    Args:
        identifier: Patient/lead ID or phone number.
        limit: Maximum number of recent calls to return (default 5, max 20).
    """
    conn = await get_db_connection()
    if not conn:
        return "Error: Could not connect to database"

    limit = min(max(limit, 1), 20)

    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [r['table_name'] for r in tables]
    except Exception as e:
        await conn.close()
        return f"Error: {e}"

    log_tables = [t for t in table_names if t in ("call_logs", "call_log", "call_history")]
    if not log_tables:
        log_tables = [t for t in table_names if "call_log" in t.lower() or "call_history" in t.lower()]

    if not log_tables:
        await conn.close()
        return "No call_logs table found. Expected table: 'call_logs'."

    results = []
    for table in log_tables:
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table
            )
            col_names = [c['column_name'] for c in cols]

            # Select meaningful columns
            select_cols = ["id"]
            for col in col_names:
                if col in ("call_id",):
                    select_cols.append(col)
                elif col in ("lead_id", "client_id", "patient_id"):
                    select_cols.append(col)
                elif col in ("client_phone", "phone", "caller"):
                    select_cols.append(col)
                elif col in ("direction",):
                    select_cols.append(col)
                elif col in ("call_status", "status"):
                    select_cols.append(col)
                elif col in ("call_duration_seconds", "duration"):
                    select_cols.append(col)
                elif col in ("ai_summary", "summary"):
                    select_cols.append(col)
                elif col in ("created_at", "timestamp", "call_time", "date"):
                    select_cols.append(col)

            select_cols = list(dict.fromkeys(select_cols))
            select_expr = ", ".join(select_cols)

            # Build WHERE by searching lead_id and phone columns
            id_cols = [c for c in col_names if c in ("lead_id", "client_id", "patient_id")]
            phone_cols = [c for c in col_names if c in ("client_phone", "phone", "caller")]

            conditions = []
            params = []
            param_idx = 1

            for icol in id_cols:
                conditions.append(f"{icol}::text = ${param_idx}")
            id_param = identifier if param_idx == 1 else None

            if phone_cols:
                phone_param = f"%{identifier.lstrip('+')}%"
                for pcol in phone_cols:
                    conditions.append(f"{pcol}::text LIKE ${param_idx + 1 if id_cols else param_idx}")

            where_clause = " OR ".join(conditions) if conditions else "TRUE"
            all_params = []
            if id_cols:
                all_params.append(identifier)
            if phone_cols:
                all_params.append(f"%{identifier.lstrip('+')}%")

            order_col = "created_at" if "created_at" in col_names else ("timestamp" if "timestamp" in col_names else "id")
            order_direction = "DESC NULLS LAST"

            rows = await conn.fetch(
                f"SELECT {select_expr} FROM {table} WHERE {where_clause} ORDER BY {order_col} {order_direction} LIMIT {limit}",
                *all_params
            )

            for row in rows:
                parts = [f"[{table}]"]
                for col in select_cols:
                    parts.append(f"{col}: {row[col]}")
                results.append(" | ".join(parts))

            if not rows:
                results.append(f"No call history found for '{identifier}' in '{table}'.")
        except Exception as e:
            logger.error(f"Error querying call history from {table}: {e}")
            results.append(f"Error querying {table}: {e}")
            continue

    await conn.close()
    if results:
        return "Call history:\n" + "\n".join(results)
    return f"No call history found for '{identifier}'."


@mcp.tool()
async def get_db_status() -> str:
    """Check if the database connection is working and report basic stats.

    Returns connection status, table count, and the list of available tables.
    Useful for debugging and health checks.
    """
    conn = await get_db_connection()
    if not conn:
        return "Database: DISCONNECTED"

    try:
        table_count = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        )
        table_names = [r['table_name'] for r in tables]

        # Get row counts for each table
        row_counts = {}
        for t in table_names:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
                row_counts[t] = count
            except Exception:
                row_counts[t] = "?"

        await conn.close()

        parts = [f"Database: CONNECTED ({DB_HOST}:{DB_PORT}/{DB_NAME})",
                 f"Tables ({table_count}):"]
        for t in table_names:
            parts.append(f"  - {t} ({row_counts[t]} rows)")

        return "\n".join(parts)
    except Exception as e:
        await conn.close()
        return f"Database: CONNECTED but error getting stats: {e}"


if __name__ == "__main__":
    mcp.run()
