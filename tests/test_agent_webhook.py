"""
Tests for agent.py post-call webhook payload construction.
Verifies that inbound and outbound calls both get the correct webhook event.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mantra.utils import normalize_to_iso8601


def make_webhook_payload(
    call_payload: dict,
    call_status: str,
    transcript_data: str,
    summary_text: str,
    recording_url: str,
    duration: int,
    next_call_on: str | None,
    new_stage_id,
    client_custom_fields: dict,
    call_state: dict,
    ctx_job_id: str,
) -> dict:
    """Replicates the webhook payload construction from agent.py finalize()."""
    direction = call_payload.get("direction", "outbound")
    webhook_payload = {
        "event": "CALL_DATA_UPDATE",
        "data": {
            "client_id": call_payload.get("lead_id"),
            "call_id": call_payload.get("call_id") or call_payload.get("voice_id"),
            "call_status": call_status,
            "status": call_status,
            "direction": direction,
            "call_transcript": transcript_data,
            "ai_summary": summary_text,
            "summary": summary_text,
            "recording_url": recording_url,
            "call_duration_seconds": duration,
            "next_call_on": normalize_to_iso8601(next_call_on),
            "ai_call_id": ctx_job_id,
            "new_stage_id": new_stage_id,
            "process_id": call_payload.get("process_id"),
            "notes": "",
            "metadata": call_payload.get("metadata", {}),
            "client_custom_fields": client_custom_fields,
            "call_custom_fields": call_payload.get("call_custom_fields", {}),
            "client_phone": call_payload.get("client_phone")
            or call_payload.get("phone"),
            "trunk_id": call_payload.get("trunk_id"),
            "url": "",
            "timeline": call_state.get("timeline", []),
        },
    }

    if direction == "inbound":
        inbound_context = {
            "org_id": call_payload.get("org_id"),
            "kb_id": call_payload.get("kb_id"),
            "phone_number": call_payload.get("phone_number"),
            "provider": call_payload.get("provider"),
        }
        inbound_context = {k: v for k, v in inbound_context.items() if v is not None}
        if inbound_context:
            webhook_payload["data"]["inbound_context"] = inbound_context
            webhook_payload["data"].update(inbound_context)

    return webhook_payload


def test_outbound_call_webhook():
    """Outbound call should NOT have inbound_context."""
    call_payload = {
        "call_id": "call_123",
        "lead_id": "lead_456",
        "client_phone": "+911234567890",
        "trunk_id": "ST_123",
        "direction": "outbound",
        "process_id": "proc_789",
    }
    payload = make_webhook_payload(
        call_payload=call_payload,
        call_status="Completed",
        transcript_data='[{"user": "Hi"}]',
        summary_text="Call completed successfully.",
        recording_url="https://s3.example.com/rec.mp3",
        duration=120,
        next_call_on=None,
        new_stage_id=5,
        client_custom_fields={},
        call_state={"timeline": []},
        ctx_job_id="job_001",
    )

    assert payload["event"] == "CALL_DATA_UPDATE"
    assert payload["data"]["direction"] == "outbound"
    assert payload["data"]["call_id"] == "call_123"
    assert payload["data"]["client_id"] == "lead_456"
    assert "inbound_context" not in payload["data"]
    assert payload["data"]["call_status"] == "Completed"


def test_inbound_call_webhook_with_context():
    """Inbound call should include direction='inbound' and inbound_context."""
    call_payload = {
        "call_id": "inbound_call_001",
        "direction": "inbound",
        "phone_number": "+918031321203",
        "org_id": "org_mantracare",
        "kb_id": "org_mantracare",
        "provider": "plivo",
        "process_id": "proc_inbound",
    }
    payload = make_webhook_payload(
        call_payload=call_payload,
        call_status="Completed",
        transcript_data='[{"user": "I need help"}]',
        summary_text="Inbound call handled.",
        recording_url="",
        duration=90,
        next_call_on=None,
        new_stage_id=3,
        client_custom_fields={},
        call_state={"timeline": []},
        ctx_job_id="job_inbound_001",
    )

    assert payload["event"] == "CALL_DATA_UPDATE"
    assert payload["data"]["direction"] == "inbound"
    assert "inbound_context" in payload["data"]
    assert payload["data"]["inbound_context"]["org_id"] == "org_mantracare"
    assert payload["data"]["inbound_context"]["phone_number"] == "+918031321203"
    assert payload["data"]["inbound_context"]["provider"] == "plivo"
    # Also verify top-level merge
    assert payload["data"]["org_id"] == "org_mantracare"
    assert payload["data"]["phone_number"] == "+918031321203"


def test_inbound_call_webhook_no_org_id():
    """Inbound call without org_id should still include phone_number in inbound_context."""
    call_payload = {
        "call_id": "inbound_call_002",
        "direction": "inbound",
        "phone_number": "+918031321203",
    }
    payload = make_webhook_payload(
        call_payload=call_payload,
        call_status="No Answer",
        transcript_data="",
        summary_text="Call failed.",
        recording_url="",
        duration=0,
        next_call_on=None,
        new_stage_id=None,
        client_custom_fields={},
        call_state={"timeline": []},
        ctx_job_id="job_inbound_002",
    )

    assert payload["data"]["direction"] == "inbound"
    # phone_number is set so inbound_context should exist with phone_number only
    assert "inbound_context" in payload["data"]
    assert payload["data"]["inbound_context"]["phone_number"] == "+918031321203"
    # org_id should not be in the inbound_context (was None, filtered out)
    assert "org_id" not in payload["data"]["inbound_context"]


def test_webhook_includes_all_required_outbound_fields():
    """Verify the webhook payload has all required fields for outbound calls."""
    call_payload = {
        "call_id": "call_out_001",
        "lead_id": "lead_001",
        "client_phone": "+919999999999",
        "trunk_id": "ST_ABC",
        "direction": "outbound",
        "stage_id": 1,
        "stageDetails": [{"stage_id": 1, "description": "Initial"}],
        "client_custom_fields": {},
        "call_custom_fields": {},
        "metadata": {"source": "webhook"},
        "process_id": "proc_001",
    }
    payload = make_webhook_payload(
        call_payload=call_payload,
        call_status="Completed",
        transcript_data="[]",
        summary_text="Test",
        recording_url="",
        duration=60,
        next_call_on=None,
        new_stage_id=2,
        client_custom_fields={},
        call_state={"timeline": [{"event": "started"}]},
        ctx_job_id="job_out_001",
    )

    data = payload["data"]
    required = [
        "client_id", "call_id", "call_status", "status", "direction",
        "call_transcript", "ai_summary", "summary", "recording_url",
        "call_duration_seconds", "next_call_on", "ai_call_id", "new_stage_id",
        "process_id", "notes", "metadata", "client_custom_fields",
        "call_custom_fields", "client_phone", "trunk_id", "url", "timeline",
    ]
    for field in required:
        assert field in data, f"Missing required field: {field}"


def test_inbound_call_webhook_uses_resolved_context():
    """Inbound call with resolved context from org_configs should include all fields."""
    call_payload = {
        "call_id": "inbound_ctx_001",
        "direction": "inbound",
        "phone_number": "+918031321203",
        "org_id": "org_test",
        "kb_id": "org_test",
        "kb_tags": ["support", "billing"],
        "prompt": "You are a support assistant",
        "voice": "arushi",
        "model": "deepseek",
        "process_id": "proc_onboard",
        "transfer_numbers": {"support": "+911234567890"},
        "client_name": "Test User",
        "provider": "zadarma",
        "trunk_id": "ST_INBOUND_001",
    }
    payload = make_webhook_payload(
        call_payload=call_payload,
        call_status="Completed",
        transcript_data='[{"user": "hello"}]',
        summary_text="Call completed",
        recording_url="https://s3.example.com/rec.mp3",
        duration=45,
        next_call_on=None,
        new_stage_id=3,
        client_custom_fields={},
        call_state={"timeline": []},
        ctx_job_id="job_ctx_001",
    )

    assert payload["data"]["direction"] == "inbound"
    assert payload["data"]["inbound_context"]["org_id"] == "org_test"
    assert payload["data"]["inbound_context"]["kb_id"] == "org_test"
    assert payload["data"]["inbound_context"]["phone_number"] == "+918031321203"
    assert payload["data"]["inbound_context"]["provider"] == "zadarma"
    assert payload["data"]["org_id"] == "org_test"
    assert payload["data"]["phone_number"] == "+918031321203"
