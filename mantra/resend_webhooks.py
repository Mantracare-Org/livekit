#!/usr/bin/env python3
"""
Replay failed n8n/summary webhook deliveries from the webhook_deliveries outbox.

Every delivery attempt is stored in `webhook_deliveries` (see migration 008).
Failed rows stay there, so a payload lost to a network blip, a backend outage or
a deploy can be re-sent later without waiting for a new call.

Usage:
    # list failed deliveries, newest first (default view — safe, read-only)
    uv run python -m mantra.resend_webhooks list

    # filter
    uv run python -m mantra.resend_webhooks list --call-id 284597
    uv run python -m mantra.resend_webhooks list --state failed --limit 20

    # show one payload in full
    uv run python -m mantra.resend_webhooks show 42

    # replay one row by id
    uv run python -m mantra.resend_webhooks resend 42

    # replay every currently-failed row
    uv run python -m mantra.resend_webhooks resend --all

    # replay but only print what would be sent
    uv run python -m mantra.resend_webhooks resend 42 --dry-run

Each replay appends a NEW row linked via replay_of, so the outbox keeps full
history: the original failure is never erased, and replay_count on the parent
tracks how many times it has been retried.

The payload is replayed from payload_raw (the exact bytes originally sent)
rather than re-serialized JSON, because the backend verifies an HMAC over the
raw body and JSONB does not preserve key order or number formatting.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from dotenv import load_dotenv

from mantra.utils import deliver_signed_payload, record_delivery_attempt, record_backend_delivery

load_dotenv(".env.local")
load_dotenv(".env")


def _conn_kwargs() -> dict:
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")
    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT")

    if not all([user, password, database, host, port]):
        print("ERROR: missing PostgreSQL env vars (POSTGRES_USER/PASSWORD/DB/HOST/PORT)", file=sys.stderr)
        sys.exit(2)

    return dict(user=user, password=password, database=database, host=host, port=int(port), timeout=10.0)


def _resolve_endpoint(row) -> str:
    """Prefer the endpoint the original attempt used; fall back to current config."""
    endpoint = row["endpoint"]
    if endpoint:
        return endpoint

    base_url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
    if not base_url:
        print("ERROR: MANTRAASSIST_BACKEND_URL not set and row has no stored endpoint", file=sys.stderr)
        sys.exit(2)
    return f"{base_url}/v1/webhooks/n8n/summary"


def _payload_str(row) -> str:
    """Exact bytes to (re)send. payload_raw is authoritative; JSON is the fallback."""
    if row["payload_raw"]:
        return row["payload_raw"]

    payload = row["payload"]
    if isinstance(payload, str):
        return payload
    return json.dumps(payload or {}, separators=(",", ":"), default=str)


async def cmd_list(args) -> int:
    conn = await asyncpg.connect(**_conn_kwargs())
    try:
        states = args.state or ["failed"]
        rows = await conn.fetch(
            """
            SELECT id, call_id, ai_call_id, event_type, send_state, http_status,
                   attempts, replay_count, replay_of, created_at, replayed_at, resolved_at,
                   left(coalesce(last_error,''), 90) AS err
            FROM webhook_deliveries
            WHERE send_state = ANY($1::varchar[])
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            states,
            args.limit,
        )

        call_ids = [args.call_id] if args.call_id else None
        if call_ids:
            rows = await conn.fetch(
                """
                SELECT id, call_id, ai_call_id, event_type, send_state, http_status,
                       attempts, replay_count, replay_of, created_at, replayed_at, resolved_at,
                       left(coalesce(last_error,''), 90) AS err
                FROM webhook_deliveries
                WHERE send_state = ANY($1::varchar[]) AND call_id = $2
                ORDER BY created_at DESC
                LIMIT $3;
                """,
                states,
                args.call_id,
                args.limit,
            )

        if not rows:
            print(f"No deliveries with state {states}.")
            return 0

        header = (f"{'id':>6}  {'call_id':<14} {'event':<26} {'state':<16} {'http':<5} "
                  f"{'try':<4} {'rep':<4} {'resolved':<9} {'created':<20} error")
        print(header)
        print("-" * len(header))
        for r in rows:
            http = "-" if r["http_status"] is None else str(r["http_status"])
            resolved = "yes" if r["resolved_at"] else "-"
            print(
                f"{r['id']:>6}  {r['call_id']:<14} {(r['event_type'] or '-'):<26} "
                f"{r['send_state']:<16} {http:<5} {r['attempts']:<4} {r['replay_count']:<4} "
                f"{resolved:<9} {str(r['created_at'])[:19]:<20} {r['err'] or ''}"
            )

        pending = [r for r in rows if r["send_state"] == "failed" and not r["resolved_at"]]
        print(f"\n{len(rows)} row(s); {len(pending)} still awaiting replay.")
        if pending:
            print("Replay one with:  python -m mantra.resend_webhooks resend <id>")
            print("Replay all with:  python -m mantra.resend_webhooks resend --all")
        return 0
    finally:
        await conn.close()


async def cmd_show(args) -> int:
    conn = await asyncpg.connect(**_conn_kwargs())
    try:
        row = await conn.fetchrow("SELECT * FROM webhook_deliveries WHERE id = $1;", args.id)
        if not row:
            print(f"No delivery with id {args.id}.", file=sys.stderr)
            return 1

        print(f"id            {row['id']}")
        print(f"call_id       {row['call_id']}")
        print(f"ai_call_id    {row['ai_call_id']}")
        print(f"event_type    {row['event_type']}")
        print(f"endpoint      {row['endpoint']}")
        print(f"send_state    {row['send_state']}")
        print(f"http_status   {row['http_status']}")
        print(f"attempts      {row['attempts']}")
        print(f"replay_of     {row['replay_of']}")
        print(f"replay_count  {row['replay_count']}")
        print(f"created_at    {row['created_at']}")
        print(f"replayed_at   {row['replayed_at']}")
        print(f"last_error    {row['last_error']}")
        print("\npayload_raw (exact bytes that were/will be sent):")
        print(row["payload_raw"] or "(none)")
        return 0
    finally:
        await conn.close()


async def _replay_one(conn, row, dry_run: bool, max_retries: int, endpoint_override: Optional[str] = None) -> bool:
    call_id = row["call_id"]
    endpoint = endpoint_override or _resolve_endpoint(row)
    payload_str = _payload_str(row)
    secret = os.getenv("MANTRAASSIST_WEBHOOK_SECRET", "")

    if dry_run:
        print(f"[dry-run] would POST {len(payload_str)} bytes to {endpoint} for call_id={call_id}")
        print(f"[dry-run] payload: {payload_str[:300]}{'...' if len(payload_str) > 300 else ''}")
        return True

    if not secret:
        print("WARNING: MANTRAASSIST_WEBHOOK_SECRET not set — replay will be rejected as unsigned", file=sys.stderr)

    print(f"Replaying delivery {row['id']} (call_id={call_id}) -> {endpoint}")
    ok, http_status, attempts_made, error = await deliver_signed_payload(
        endpoint, payload_str, secret, max_retries=max_retries
    )

    state = "sent" if ok else "failed"
    if ok:
        print(f"  OK  HTTP {http_status} after {attempts_made} attempt(s)")
    else:
        print(f"  FAIL  http={http_status} attempts={attempts_made} error={error}")

    new_id = await record_delivery_attempt(
        call_id=call_id,
        payload=None,
        state=state,
        endpoint=endpoint,
        payload_raw=payload_str,
        ai_call_id=row["ai_call_id"] or "",
        http_status=http_status,
        error=error,
        attempts=attempts_made,
        replay_of=row["id"],
        increment_replay=True,
        event_type=row["event_type"] or "",
    )

    await record_backend_delivery(
        call_id=call_id,
        payload=row["payload"],
        state=state,
        http_status=http_status,
        error=f"replay of delivery {row['id']}: {error}" if error else f"replay of delivery {row['id']}",
        attempts=attempts_made,
    )

    print(f"  recorded as delivery {new_id} (replay_of={row['id']})")

    if ok:
        # Mark the original failure resolved so `--all` stops picking it up.
        # History is preserved — only the replay pointer changes.
        await conn.execute(
            "UPDATE webhook_deliveries SET resolved_at = NOW() WHERE id = $1 AND resolved_at IS NULL;",
            row["id"],
        )
        print(f"  marked delivery {row['id']} resolved at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return ok


async def cmd_resend(args) -> int:
    conn = await asyncpg.connect(**_conn_kwargs())
    try:
        if args.all:
            if args.ids:
                print("ERROR: pass either ids or --all, not both", file=sys.stderr)
                return 2
            rows = await conn.fetch(
                """
                SELECT * FROM webhook_deliveries
                WHERE send_state = 'failed'
                  AND resolved_at IS NULL
                ORDER BY created_at ASC
                LIMIT $1;
                """,
                args.limit,
            )
        else:
            if not args.ids:
                print("ERROR: provide delivery id(s) or --all", file=sys.stderr)
                return 2
            rows = await conn.fetch(
                "SELECT * FROM webhook_deliveries WHERE id = ANY($1::bigint[]) ORDER BY created_at ASC;",
                list(args.ids),
            )
            found = {r["id"] for r in rows}
            missing = [i for i in args.ids if i not in found]
            if missing:
                print(f"ERROR: no such delivery id(s): {missing}", file=sys.stderr)
                return 1

        if not rows:
            print("Nothing to replay.")
            return 0

        print(f"Replaying {len(rows)} delivery/deliveries (max_retries={args.max_retries})...\n")
        results = []
        for row in rows:
            results.append(
                await _replay_one(conn, row, args.dry_run, args.max_retries, args.endpoint)
            )

        succeeded = sum(1 for r in results if r)
        print(f"\nDone: {succeeded}/{len(results)} succeeded.")
        if succeeded < len(results):
            print("Still-failed rows remain replayable — rerun with --all.", file=sys.stderr)
            return 1
        return 0
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mantra.resend_webhooks",
        description="List and replay failed n8n/summary webhook deliveries from the outbox.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list stored deliveries (default: failed only)")
    p_list.add_argument("--call-id", help="filter to a single call_id")
    p_list.add_argument(
        "--state",
        action="append",
        choices=["sent", "failed", "skipped_dedupe", "unconfigured", "pending"],
        help="delivery state(s) to show (repeatable, default: failed)",
    )
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="print one delivery in full, including exact payload bytes")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_resend = sub.add_parser("resend", help="replay one or more failed deliveries")
    p_resend.add_argument("ids", nargs="*", type=int, help="delivery id(s) to replay")
    p_resend.add_argument("--all", action="store_true", help="replay every currently-failed delivery")
    p_resend.add_argument("--limit", type=int, default=100, help="max rows for --all (default: 100)")
    p_resend.add_argument("--max-retries", type=int, default=3)
    p_resend.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    p_resend.add_argument(
        "--endpoint",
        help="override the target URL (default: the endpoint stored on the original attempt)",
    )
    p_resend.set_defaults(func=cmd_resend)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
