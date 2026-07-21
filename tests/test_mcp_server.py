"""
Tests for the MCP server tools (mcp/server.py).
Verifies tool definitions and logic.
"""

import json
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test env vars
os.environ["POSTGRES_USER"] = "test_user"
os.environ["POSTGRES_PASSWORD"] = "test_pass"
os.environ["POSTGRES_DB"] = "test_db"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"

# Import our local mcp/server.py directly (avoiding the installed mcp package)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "mantra_mcp_server",
    os.path.join(os.path.dirname(__file__), "..", "mcp", "server.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mcp = mod.mcp


class TestMCPServerTools:
    """Verify tool registration and basic behavior."""

    def test_tools_are_registered(self):
        """Verify all expected tools exist."""
        # FastMCP exposes tools via _tool_manager._tools
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        expected = [
            "list_tables",
            "describe_table",
            "execute_query",
            "call_logs",
            "get_patient_info",
            "get_hospitals",
            "get_doctors",
            "get_available_slots",
            "create_appointment",
            "update_appointment",
            "get_appointments",
            "get_call_history",
            "get_db_status",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"

    def test_call_logs_tool_exists(self):
        """call_logs tool should be registered."""
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        assert "call_logs" in tool_names

    def test_execute_query_tool_exists(self):
        """execute_query tool should be registered."""
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        assert "execute_query" in tool_names

    def test_list_tables_tool_exists(self):
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        assert "list_tables" in tool_names

    def test_get_db_status_tool_exists(self):
        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        assert "get_db_status" in tool_names
