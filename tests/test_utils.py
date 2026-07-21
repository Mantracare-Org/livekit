"""
Tests for mantra/utils.py helper functions.
"""

import json
import os
import sys
import pytest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mantra.utils import (
    normalize_to_iso8601,
    SessionRecorder,
)


class TestNormalizeToISO8601:
    def test_normal_valid(self):
        result = normalize_to_iso8601("2026-06-10 15:30:00")
        assert result == "2026-06-10T15:30:00.000Z"

    def test_none_input(self):
        assert normalize_to_iso8601(None) is None

    def test_empty_string(self):
        assert normalize_to_iso8601("") is None

    def test_already_iso_like(self):
        result = normalize_to_iso8601("2026-06-10T15:30:00")
        # Won't match the format, so passes through unchanged
        assert result == "2026-06-10T15:30:00"

    def test_invalid_string(self):
        assert normalize_to_iso8601("not-a-date") == "not-a-date"


class TestSessionRecorder:
    def test_build_transcript_empty(self):
        result = SessionRecorder.build_transcript([])
        assert result == "[]"

    def test_build_transcript_with_messages(self):
        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockRole:
            def __init__(self, name):
                self.name = name

        messages = [
            MockMsg(MockRole("user"), "Hello"),
            MockMsg(MockRole("assistant"), "Hi there!"),
            MockMsg(MockRole("user"), "I need help"),
        ]
        result = SessionRecorder.build_transcript(messages)
        parsed = json.loads(result)
        assert len(parsed) == 3
        assert parsed[0] == {"user": "Hello"}
        assert parsed[1] == {"bot": "Hi there!"}
        assert parsed[2] == {"user": "I need help"}

    def test_build_transcript_skips_system_messages(self):
        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockRole:
            def __init__(self, name):
                self.name = name

        messages = [
            MockMsg(MockRole("user"), "Hi"),
            MockMsg(MockRole("assistant"), "[System: this should be skipped]"),
            MockMsg(MockRole("user"), "Hello again"),
        ]
        result = SessionRecorder.build_transcript(messages)
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_build_transcript_with_handoff(self):
        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockRole:
            def __init__(self, name):
                self.name = name

        messages = [MockMsg(MockRole("user"), "Transfer me")]
        handoff = {"department": "support", "reason": "User requested"}
        result = SessionRecorder.build_transcript(messages, handoff)
        parsed = json.loads(result)
        assert len(parsed) == 3  # user msg + handoff dict + info dict
        assert "handoff" in parsed[1]

    def test_parse_summary_data_basic(self):
        summary = """Call Summary: Patient called for appointment.
Sentiment Score: 0.75
Next Call Date: None
Appointment Date & Time: 2026-06-15 10:30:00
Doctor: Dr. Sharma
Hospital Location: Mumbai Central"""
        sentiment, next_call, custom = SessionRecorder.parse_summary_data(summary)
        assert sentiment == 0.75
        assert next_call is None  # "None" string should be recognized
        assert custom["appointment_date_time"] == "2026-06-15 10:30:00"
        assert custom["doctor"] == "Dr. Sharma"
        assert custom["hospital_location"] == "Mumbai Central"

    def test_parse_summary_data_empty(self):
        sentiment, next_call, custom = SessionRecorder.parse_summary_data("")
        assert sentiment == 0.5
        assert next_call is None
        assert custom["appointment_date_time"] == ""
        assert custom["doctor"] == ""
        assert custom["hospital_location"] == ""
