"""MCP Client Manager — uses official Anthropic MCP SDK with stdio transport."""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str]
    enabled: bool = True
    env: dict[str, str] = field(default_factory=dict)


def _get_default_server_configs() -> list[MCPServerConfig]:
    """Build default MCP server configs using the venv Python."""
    backend_dir = Path(__file__).resolve().parent.parent
    python = str(backend_dir / ".venv" / "Scripts" / "python.exe")
    if sys.platform != "win32":
        python = str(backend_dir / ".venv" / "bin" / "python")

    return [
        MCPServerConfig(
            name="filesystem",
            command=python,
            args=["-m", "mcp_servers.filesystem.server"],
        ),
        MCPServerConfig(
            name="notes",
            command=python,
            args=["-m", "mcp_servers.notes.server"],
        ),
        MCPServerConfig(
            name="browser",
            command=python,
            args=["-m", "mcp_servers.browser.server"],
        ),
    ]


class MCPClientManager:
    """
    Manages connections to multiple MCP servers using the official MCP SDK.

    Uses stdio transport to communicate with each server process.
    Discovers tools and routes calls to the correct server.
    """

    def __init__(self, server_configs: list[MCPServerConfig] | None = None):
        self.configs = server_configs or _get_default_server_configs()
        self._sessions: dict[str, Any] = {}  # server_name -> ClientSession
        self._tools: dict[str, dict] = {}  # tool_name -> {server, schema}
        self._connected = False

    async def connect_all(self):
        """Connect to all enabled MCP servers."""
        for config in self.configs:
            if not config.enabled:
                continue
            try:
                await self._connect_server(config)
            except Exception as e:
                logger.error("Failed to connect to MCP server '%s': %s", config.name, e)

        self._connected = True
        logger.info(
            "Connected to %d MCP servers, discovered %d tools",
            len(self._sessions),
            len(self._tools),
        )

    async def _connect_server(self, config: MCPServerConfig):
        """Spawn an MCP server and initialize a session with it."""
        logger.info("Connecting to MCP server: %s (%s %s)", config.name, config.command, config.args)

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env or None,
        )

        # Create the stdio transport and session
        # stdio_client returns a context manager that yields (read_stream, write_stream)
        transport = stdio_client(server_params)
        read_stream, write_stream = await transport.__aenter__()

        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()

        # Initialize the session
        await session.initialize()
        self._sessions[config.name] = {
            "session": session,
            "transport": transport,
        }

        # Discover tools
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            self._tools[tool.name] = {
                "server": config.name,
                "schema": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                },
            }
            logger.info("Discovered tool: %s (server: %s)", tool.name, config.name)

    def get_tool_schemas(self) -> list[dict]:
        """Return all discovered tool schemas for the LLM system prompt."""
        return [info["schema"] for info in self._tools.values()]

    def get_tool_definitions_for_llm(self) -> str:
        """Format tool schemas as text for the LLM system prompt."""
        if not self._tools:
            return "No tools available."

        lines = ["You have access to the following tools:"]
        for name, info in self._tools.items():
            schema = info["schema"]
            params = schema.get("inputSchema", {}).get("properties", {})
            required = schema.get("inputSchema", {}).get("required", [])

            param_strs = []
            for pname, pinfo in params.items():
                req_marker = " (required)" if pname in required else ""
                param_strs.append(f"    - {pname}: {pinfo.get('type', 'any')}{req_marker}")

            lines.append(f"\n**{name}**: {schema.get('description', '')}")
            if param_strs:
                lines.append("  Parameters:")
                lines.extend(param_strs)

        lines.append(
            "\nTo use a tool, respond with a JSON block on its own line:\n"
            '  {"tool": "tool_name", "arguments": {"param": "value"}}\n'
            "Only use tools when they are needed to fulfill the request."
        )
        return "\n".join(lines)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by name, routing to the correct MCP server."""
        if tool_name not in self._tools:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        tool_info = self._tools[tool_name]
        server_name = tool_info["server"]
        session_data = self._sessions.get(server_name)

        if not session_data:
            return json.dumps({"error": f"MCP server '{server_name}' not connected"})

        session = session_data["session"]
        logger.info("Calling tool %s on server %s with args: %s", tool_name, server_name, arguments)

        try:
            result = await session.call_tool(tool_name, arguments)

            # Extract text content from result
            content_parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    content_parts.append(item.text)
                else:
                    content_parts.append(str(item))

            return "\n".join(content_parts) if content_parts else json.dumps({"success": True})

        except Exception as e:
            logger.error("Tool call failed: %s: %s", tool_name, e)
            return json.dumps({"error": f"Tool call failed: {e}"})

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        for name, data in self._sessions.items():
            try:
                await data["session"].__aexit__(None, None, None)
                await data["transport"].__aexit__(None, None, None)
                logger.info("Disconnected from MCP server: %s", name)
            except Exception as e:
                logger.warning("Error disconnecting from %s: %s", name, e)

        self._sessions.clear()
        self._tools.clear()
        self._connected = False
