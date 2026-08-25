"""Model Context Protocol (MCP) Client for LiveKit Voice Agent.

Communicates with MCP Servers using the official Model Context Protocol (JSON-RPC 2.0)
over SSE transport via the Anthropic MCP Python SDK, with automatic HTTP fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger("mantra.mcp_client")


@dataclass
class MCPToolInfo:
    """Metadata for an MCP tool discovered from the server."""
    name: str
    description: str
    input_schema: Dict[str, Any]


class MantraMCPClient:
    """Official MCP Client for LiveKit Agent using JSON-RPC 2.0 over SSE transport."""

    def __init__(
        self,
        server_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: float = 8.0,
    ):
        self.base_url = (server_url or os.getenv("LIVEKIT_MCP_URL", "http://localhost:8000")).rstrip("/")
        self.auth_token = auth_token or os.getenv("LIVEKIT_MCP_JWT_TOKEN", "")
        # Append token query param if available for seamless SSE transport
        if self.auth_token:
            self.sse_url = f"{self.base_url}/sse?token={self.auth_token}"
        else:
            self.sse_url = f"{self.base_url}/sse"

        self.timeout = timeout
        self._headers: Dict[str, str] = {}
        if self.auth_token:
            self._headers["Authorization"] = f"Bearer {self.auth_token}"

    async def list_tools(self) -> List[MCPToolInfo]:
        """Query the MCP server for available tools via JSON-RPC tools/list."""
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._headers if self._headers else None) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        return [
                            MCPToolInfo(
                                name=t.name,
                                description=t.description or "",
                                input_schema=getattr(t, "inputSchema", {}) or {},
                            )
                            for t in tools_result.tools
                        ]
        except Exception as e:
            logger.warning(f"[MCP] Could not query tools from {self.sse_url}: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool on the MCP server via official JSON-RPC tools/call over SSE with HTTP fallback."""
        # 1. Try MCP SSE JSON-RPC
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(self.sse_url, headers=self._headers if self._headers else None) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        logger.info(f"[MCP] Calling '{tool_name}' with args {arguments} via MCP SSE JSON-RPC")
                        res = await session.call_tool(tool_name, arguments=arguments)
                        text_blocks = [
                            c.text for c in res.content
                            if hasattr(c, "text") and c.text
                        ]
                        output = "\n".join(text_blocks) if text_blocks else "No details returned."
                        logger.info(f"[MCP] Tool '{tool_name}' returned: {output[:120]}...")
                        return output
        except Exception as sse_err:
            logger.warning(f"[MCP] SSE JSON-RPC failed ({sse_err}), falling back to direct HTTP /api/tools/call...")

        # 2. HTTP Fallback Endpoint
        try:
            http_url = f"{self.base_url}/api/tools/call"
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                resp = await http_client.post(
                    http_url,
                    json={"name": tool_name, "arguments": arguments},
                    headers=self._headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", "")
                    logger.info(f"[MCP-HTTP] Tool '{tool_name}' returned: {result[:120]}...")
                    return result if result else "No availability information found."
                else:
                    logger.error(f"[MCP-HTTP] Status {resp.status_code}: {resp.text}")
        except Exception as http_err:
            logger.error(f"[MCP-HTTP] Direct HTTP call failed: {http_err}")

        return "Unable to retrieve doctor schedule at the moment. Please offer to take a callback request."


# Global singleton instance
_mcp_client_instance: Optional[MantraMCPClient] = None


def get_mcp_client() -> MantraMCPClient:
    """Return global MantraMCPClient singleton instance."""
    global _mcp_client_instance
    if _mcp_client_instance is None:
        _mcp_client_instance = MantraMCPClient()
    return _mcp_client_instance
