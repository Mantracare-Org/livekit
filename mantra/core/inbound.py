"""Inbound/outbound call context resolution and upfront KB/process formatting.

Extracted verbatim from the former mantra/agent.py module.
"""
import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

from mantra.core.common import get_global_kb
from mantra.utils import format_e164_phone_number

logger = logging.getLogger("mantra.inbound")


async def _resolve_from_db(phone_number: str) -> dict | None:
    """
    Look up inbound call context from the PostgreSQL org_configs table.
    """
    try:
        clean_number = phone_number.replace("+", "")
        kb = get_global_kb()
        pool = await kb._get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM org_configs WHERE phone_number IN ($1, $2) AND is_active = true",
                phone_number, clean_number
            )

        if row:
            result = dict(row)
            if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
                try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
                except: pass

            # Get all KB collection IDs for this org (includes org_id fallback for legacy data)
            try:
                kb_ids = await kb.get_kb_ids_for_org(result["org_id"])
            except Exception as e:
                logger.error(f"Failed to fetch kb_ids for org {result.get('org_id')}: {e}")
                kb_ids = [result.get("org_id")]

            process_id = None
            stage_id = None
            try:
                col_details = await kb.get_collection_details_for_org(result["org_id"])
                if col_details:
                    process_id = col_details.get("process_id")
                    stage_id = col_details.get("stage_id")
            except Exception as e:
                logger.error(f"Failed to fetch collection details for org {result.get('org_id')}: {e}")

            return {
                "org_id": result.get("org_id"),
                "kb_id": result.get("org_id"),
                "kb_ids": kb_ids,
                "kb_tags": result.get("kb_tags", []),
                "prompt": result.get("prompt"),
                "voice": result.get("voice"),
                "model": result.get("model"),
                "process_id": process_id,
                "stage_id": stage_id,
                "transfer_numbers": result.get("transfer_numbers", {}),
                "client_name": result.get("client_name"),
            }
        return None
    except Exception as e:
        logger.error(f"Failed to query DB for phone number {phone_number}: {e}")
        return None


async def _resolve_from_mantra_backend(phone_number: str) -> dict | None:
    """
    Call MantraAssist backend to resolve inbound call context from the dialed phone number.
    Returns org_id, kb_id, kb_tags, prompt, voice, model, process_id, transfer_numbers, client_name.
    Returns None if the backend is unreachable or returns an error.
    """
    base_url = os.getenv("MANTRAASSIST_BACKEND_URL", "").rstrip("/")
    if not base_url:
        logger.error("MANTRAASSIST_BACKEND_URL not set — cannot resolve inbound call context")
        return None

    url = f"{base_url}/v1/telephony/resolve-inbound-call"
    logger.info(f"Resolving inbound call context for phone_number={phone_number} via {url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"phone_number": phone_number},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        f"Resolved inbound context: org_id={data.get('org_id')}, "
                        f"kb_id={data.get('kb_id')}, kb_tags={data.get('kb_tags')}"
                    )
                    return data
                else:
                    resp_text = await resp.text()
                    logger.error(
                        f"MantraAssist resolve-inbound-call returned {resp.status}: {resp_text}"
                    )
                    return None
    except asyncio.TimeoutError:
        logger.error("MantraAssist resolve-inbound-call timed out (10s)")
        return None
    except Exception as e:
        logger.error(f"Failed to resolve inbound call context: {e}")
        return None


async def resolve_inbound_context(phone_number: str) -> dict | None:
    """
    Resolves inbound call context from the PostgreSQL org_configs table (DB only, no HTTP).
    """
    logger.info(f"[DIAG] resolve_inbound_context: looking up {phone_number} in DB...")
    config = await _resolve_from_db(phone_number)
    if config:
        logger.info(f"[DIAG] resolve_inbound_context: DB HIT — org_id={config.get('org_id')}, kb_ids={config.get('kb_ids')}, client={config.get('client_name')}")
    else:
        logger.warning(f"[DIAG] resolve_inbound_context: DB MISS for {phone_number} — using dispatch rule defaults")
    return config


def _extract_inbound_caller_phone(meta_payload: dict | None, fallback_phone: str | None = None) -> str:
    """Prefer the true caller number over the org-bound inbound DID."""
    candidates: list[str] = []
    if isinstance(meta_payload, dict):
        for key in (
            "call_from",
            "caller_number",
            "client_phone_number",
            "client_phone",
            "from_number",
            "source_number",
            "phone_number",
        ):
            value = meta_payload.get(key)
            if value:
                candidates.append(str(value))
    if fallback_phone:
        candidates.insert(0, str(fallback_phone))

    for candidate in candidates:
        value = str(candidate).strip()
        if not value:
            continue
        if value.lower().startswith("sip_"):
            value = value[4:]
        normalized = format_e164_phone_number(value)
        if normalized:
            return normalized
    return ""


def _extract_livekit_caller_phone(participants) -> str:
    """Extract the inbound caller number from LiveKit SIP participant metadata."""
    attribute_keys = (
        "sip.phoneNumber",
        "sip.from",
        "sip.callerNumber",
        "sip.sourceNumber",
        "phone_number",
        "caller_number",
        "from_number",
    )
    for participant in participants:
        attributes = getattr(participant, "attributes", {}) or {}
        for key in attribute_keys:
            value = attributes.get(key)
            if value:
                normalized = _extract_inbound_caller_phone({"phone_number": value})
                if normalized:
                    return normalized

        identity = getattr(participant, "identity", "")
        normalized = _extract_inbound_caller_phone({"phone_number": identity})
        if normalized:
            return normalized
    return ""


def _extract_recognized_client_name(result: Any) -> str | None:
    """Extract a client name from the MCP/backend response, including null results."""
    if result is None:
        return None

    if isinstance(result, str):
        value = result.strip()
        if not value or value.lower() in {"null", "none", "{}", "[]"}:
            return None
        try:
            result = json.loads(value)
        except json.JSONDecodeError:
            return value

    if not isinstance(result, dict):
        return None

    for key in ("client_name", "name", "full_name"):
        value = result.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    for key in ("data", "result", "lead", "client"):
        nested_name = _extract_recognized_client_name(result.get(key))
        if nested_name:
            return nested_name
    return None


def _extract_recognized_client_metadata(result: Any) -> dict[str, list]:
    """Extract structured client metadata while tolerating nested MCP responses."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return {"ai_summaries": [], "custom_fields": []}

    if not isinstance(result, dict):
        return {"ai_summaries": [], "custom_fields": []}

    for key in ("data", "result", "lead", "client"):
        nested = result.get(key)
        if isinstance(nested, dict):
            metadata = _extract_recognized_client_metadata(nested)
            if any(metadata.values()):
                return metadata

    metadata = result.get("client_metadata")
    if not isinstance(metadata, dict):
        return {"ai_summaries": [], "custom_fields": []}
    return {
        "ai_summaries": metadata.get("ai_summaries", []) if isinstance(metadata.get("ai_summaries"), list) else [],
        "custom_fields": metadata.get("custom_fields", []) if isinstance(metadata.get("custom_fields"), list) else [],
    }


async def recognize_inbound_client(phone_number: str, org_id: str | int) -> dict[str, Any] | None:
    """Resolve a known client's identity and metadata before an inbound greeting.

    Use the actual caller's phone number to identify the client, scoped by org_id.
    The org-bound DID is only used for routing and should not be sent to the
    client-recognition tool.
    """
    if not phone_number or org_id in (None, ""):
        return None

    try:
        from mantra.mcp_client import get_mcp_client

        async with asyncio.timeout(3):
            result = await get_mcp_client().call_tool(
                "recognize_client",
                {"org_id": org_id, "phone_number": format_e164_phone_number(phone_number)},
            )

        client_name = _extract_recognized_client_name(result)
        client_metadata = _extract_recognized_client_metadata(result)
        if client_name:
            logger.info("Client recognition returned client_name=%s for org_id=%s", client_name, org_id)
        else:
            logger.info("Client recognition returned no matching client for org_id=%s", org_id)
        if not client_name:
            return None
        return {
            "client_name": client_name,
            "client_metadata": client_metadata,
        }
    except asyncio.TimeoutError:
        logger.warning("Client recognition timed out for org_id=%s", org_id)
    except json.JSONDecodeError:
        logger.warning("Client recognition returned malformed MCP data for org_id=%s", org_id)
    except Exception as error:
        logger.warning("Client recognition via MCP failed for org_id=%s: %s", org_id, error)

    return None


async def resolve_outbound_context(org_id: str) -> dict | None:
    """
    Resolves outbound call KB context from PostgreSQL using org_id.
    Fetches all kb_ids for the org and kb_tags from org_configs.
    """
    if not org_id:
        return None
    org_id = str(org_id).strip()
    logger.info(f"[DIAG] resolve_outbound_context: looking up org_id={org_id} in DB...")
    try:
        kb = get_global_kb()
        pool = await kb._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT org_id, kb_tags, prompt, voice, model FROM org_configs WHERE org_id = $1 AND is_active = true",
                org_id,
            )
        try:
            kb_ids = await kb.get_kb_ids_for_org(org_id)
        except Exception as e:
            logger.error(f"Failed to fetch kb_ids for outbound org {org_id}: {e}")
            kb_ids = [org_id]
        kb_tags = []
        if row and row["kb_tags"]:
            kb_tags = row["kb_tags"] if isinstance(row["kb_tags"], list) else []
        logger.info(f"[DIAG] resolve_outbound_context: DB HIT — org_id={org_id}, kb_ids={kb_ids}, kb_tags={kb_tags}")
        return {
            "org_id": org_id,
            "kb_ids": kb_ids,
            "kb_tags": kb_tags,
            "prompt": row["prompt"] if row and row["prompt"] else None,
            "voice": row["voice"] if row and row["voice"] else None,
            "model": row["model"] if row and row["model"] else None,
        }
    except Exception as e:
        logger.error(f"Failed to resolve outbound context for org_id {org_id}: {e}")
        try:
            kb = get_global_kb()
            kb_ids = await kb.get_kb_ids_for_org(org_id)
            return {"org_id": org_id, "kb_ids": kb_ids, "kb_tags": []}
        except Exception:
            return {"org_id": org_id, "kb_ids": [org_id], "kb_tags": []}


def format_upfront_kb_context(pages: list) -> str:
    if not pages:
        return ""
    text = "\n\n<!-- UPFRONT_KB_START -->\n=== ORGANIZATION KNOWLEDGE BASE (PRE-LOADED FOR INSTANT ZERO-LATENCY ANSWERS) ===\n"
    total_len = 0
    for i, page in enumerate(pages, 1):
        content = page.content_in_text if hasattr(page, "content_in_text") else str(getattr(page, "content", ""))
        title = page.title if hasattr(page, "title") else f"Doc {i}"
        entry = f"\n[DOCUMENT: {title}]\n{content}\n"
        if total_len + len(entry) > 12000:
            break
        text += entry
        total_len += len(entry)
    text += "\nDIRECTIVE: Use the pre-loaded Knowledge Base information above for general informational questions only. Never use it for doctor availability, appointment slots, booking, rescheduling, cancellation, or doctor working hours; those requests must use the dedicated MCP availability tool.\n<!-- UPFRONT_KB_END -->"
    return text


def format_upfront_process_context(processes: list) -> str:
    if not processes:
        return ""

    text = "\n\n<!-- UPFRONT_PROCESS_CONTEXT_START -->\n=== ORGANIZATION PROCESS AND STAGE INFORMATION ===\n"
    total_len = 0
    for process in processes:
        if not isinstance(process, dict):
            continue
        process_name = process.get("name") or process.get("process_name") or "Process"
        description = process.get("description") or process.get("process_description") or ""
        entry = f"\n[PROCESS: {process_name}]\n{description}\n"
        stages = process.get("stages") or process.get("stageDetails") or []
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_name = stage.get("name") or stage.get("stage_name") or "Stage"
            stage_description = stage.get("description") or stage.get("stage_description") or ""
            entry += f"[STAGE: {stage_name}]\n{stage_description}\n"
        if total_len + len(entry) > 12000:
            break
        text += entry
        total_len += len(entry)

    if total_len == 0:
        return ""
    text += "\nDIRECTIVE: Use this process and stage information to answer specific caller questions directly. Do not invent details that are not present here.\n<!-- UPFRONT_PROCESS_CONTEXT_END -->"
    return text