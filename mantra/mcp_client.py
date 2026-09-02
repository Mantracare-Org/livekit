"""Model Context Protocol (MCP) Client for LiveKit Voice Agent.

Communicates with MCP Servers using the official Model Context Protocol (JSON-RPC 2.0)
over SSE transport via the Anthropic MCP Python SDK, with automatic HTTP fallback.
Supports dynamic OAuth 2.1 token acquisition via Mantra Auth.
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
        self.base_url = (server_url or os.getenv("LIVEKIT_MCP_URL")).rstrip("/")
        self.auth_server_url = os.getenv("AUTH_SERVER_URL").rstrip("/")
        self.client_id = os.getenv("OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("OAUTH_CLIENT_SECRET")
        
        self.auth_token = auth_token or os.getenv("LIVEKIT_MCP_JWT_TOKEN")
        self.timeout = timeout

    async def _ensure_auth_token(self) -> Optional[str]:
        """Dynamically fetch OAuth access token from Mantra Auth using client credentials if token is missing."""
        if self.auth_token:
            return self.auth_token

        if not self.client_id or not self.client_secret:
            logger.debug("[MCP] No auth_token or OAuth client credentials configured.")
            return None

        token_url = f"{self.auth_server_url}/api/oauth/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            req_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "ngrok-skip-browser-warning": "true",
            }
            async with httpx.AsyncClient(timeout=4.0, headers=req_headers) as http_client:
                resp = await http_client.post(token_url, data=payload)
                if resp.status_code == 200:
                    token_data = resp.json()
                    self.auth_token = token_data.get("access_token", "")
                    logger.info("[MCP] Successfully acquired OAuth access token from Auth Server.")
                    return self.auth_token
                else:
                    logger.warning(f"[MCP] Failed to fetch OAuth token ({resp.status_code}): {resp.text[:150]}")
        except Exception as e:
            logger.warning(f"[MCP] Error contacting Auth Server for OAuth token: {e}")

        return None

    def _get_connection_params(self, token: Optional[str]) -> tuple[str, Dict[str, str]]:
        """Construct SSE URL and Authorization headers."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/event-stream, */*",
            "ngrok-skip-browser-warning": "true",
        }
        if token:
            sse_url = f"{self.base_url}/sse?token={token}"
            headers["Authorization"] = f"Bearer {token}"
        else:
            sse_url = f"{self.base_url}/sse"
        return sse_url, headers

    async def list_tools(self) -> List[MCPToolInfo]:
        """Query the MCP server for available tools via JSON-RPC tools/list."""
        token = await self._ensure_auth_token()
        sse_url, headers = self._get_connection_params(token)

        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(sse_url, headers=headers if headers else None) as (read_stream, write_stream):
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
            logger.warning(f"[MCP] Could not query tools from {sse_url}: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool on the MCP server via official JSON-RPC tools/call over SSE with HTTP fallback."""
        token = await self._ensure_auth_token()
        sse_url, headers = self._get_connection_params(token)

        # 1. Try MCP SSE JSON-RPC
        try:
            async with asyncio.timeout(self.timeout):
                async with sse_client(sse_url, headers=headers if headers else None) as (read_stream, write_stream):
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
                    headers=headers if headers else None,
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
