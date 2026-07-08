"""MCP Client Manager — direct JSON-RPC over stdio, no ProactorEventLoop dependency."""

import asyncio
import json
import logging
import subprocess
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
        MCPServerConfig(
            name="email",
            command=python,
            args=["-m", "mcp_servers.email.server"],
        ),
    ]


class DirectStdioTransport:
    """
    Direct JSON-RPC transport over subprocess stdin/stdout.

    Avoids the MCP SDK's stdio_client which uses connect_read_pipe()
    and crashes on Windows ProactorEventLoop when the subprocess dies.

    This implementation:
    - Spawns the server as a subprocess
    - Writes JSON-RPC requests to its stdin
    - Reads JSON-RPC responses from its stdout
    - Runs in a background thread to avoid blocking the event loop
    """

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None):
        self._command = command
        self._args = args
        self._env = env
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    async def start(self):
        """Spawn the server subprocess."""
        import os

        full_env = {**os.environ, **(self._env or {})}

        try:
            self._process = subprocess.Popen(
                [self._command] + self._args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            logger.info(
                "Started MCP server: %s (PID: %d)",
                self._args[-1],
                self._process.pid,
            )
        except Exception as e:
            logger.error("Failed to start MCP server %s: %s", self._args, e)
            raise

    async def send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process or self._process.poll() is not None:
            return {"error": "Server not running"}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params:
            request["params"] = params

        request_bytes = (json.dumps(request) + "\n").encode("utf-8")

        loop = asyncio.get_event_loop()

        try:
            # Write request (in thread to avoid blocking)
            await loop.run_in_executor(None, self._write, request_bytes)

            # Read response (in thread to avoid blocking)
            response = await loop.run_in_executor(None, self._read_response)
            return response

        except Exception as e:
            logger.error("Communication error with MCP server: %s", e)
            return {"error": str(e)}

    def _write(self, data: bytes):
        """Write to subprocess stdin."""
        if self._process and self._process.stdin:
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def _read_response(self) -> dict:
        """Read a single JSON-RPC response from subprocess stdout."""
        if not self._process or not self._process.stdout:
            return {"error": "No stdout pipe"}

        try:
            line = self._process.stdout.readline()
            if not line:
                return {"error": "Server closed connection"}
            return json.loads(line.decode("utf-8").strip())
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON from server: {e}"}
        except Exception as e:
            return {"error": f"Read error: {e}"}

    def kill(self):
        """Kill the subprocess."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None


class MCPClientManager:
    """
    Manages connections to multiple MCP servers.

    Uses direct JSON-RPC over stdio (no MCP SDK dependency) to avoid
    the Windows ProactorEventLoop bug with connect_read_pipe().
    """

    def __init__(self, server_configs: list[MCPServerConfig] | None = None):
        self.configs = server_configs or _get_default_server_configs()
        self._transports: dict[str, DirectStdioTransport] = {}
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
            len(self._transports),
            len(self._tools),
        )

    async def _connect_server(self, config: MCPServerConfig):
        """Spawn an MCP server and initialize a session with it."""
        logger.info(
            "Connecting to MCP server: %s (%s %s)",
            config.name, config.command, config.args,
        )

        transport = DirectStdioTransport(
            command=config.command,
            args=config.args,
            env=config.env or None,
        )
        await transport.start()

        # Initialize the session
        init_response = await transport.send_request("initialize")
        if "error" in init_response:
            raise RuntimeError(f"MCP init failed: {init_response['error']}")

        logger.info("MCP server '%s' initialized: %s", config.name, init_response.get("result", {}))

        # Discover tools
        tools_response = await transport.send_request("tools/list")
        if "error" in tools_response:
            raise RuntimeError(f"MCP tools/list failed: {tools_response['error']}")

        tools = tools_response.get("result", {}).get("tools", [])
        for tool in tools:
            self._tools[tool["name"]] = {
                "server": config.name,
                "schema": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {}),
                },
            }
            logger.info("Discovered tool: %s (server: %s)", tool["name"], config.name)

        self._transports[config.name] = transport

    def get_tool_schemas(self) -> list[dict]:
        """Return all discovered tool schemas for the LLM system prompt."""
        return [info["schema"] for info in self._tools.values()]

    def get_tool_schema(self, tool_name: str) -> dict | None:
        """Return the schema for a specific tool, or None if not found."""
        info = self._tools.get(tool_name)
        return info["schema"] if info else None

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
        import json as json_mod

        if tool_name not in self._tools:
            return json_mod.dumps({"error": f"Unknown tool: {tool_name}"})

        tool_info = self._tools[tool_name]
        server_name = tool_info["server"]
        transport = self._transports.get(server_name)

        if not transport:
            return json_mod.dumps({"error": f"MCP server '{server_name}' not connected"})

        logger.info(
            "Calling tool %s on server %s with args: %s",
            tool_name, server_name, arguments,
        )

        response = await transport.send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

        if "error" in response:
            return json_mod.dumps({"error": response["error"]})

        # Extract text content from result
        result = response.get("result", {})
        content_parts = []
        for item in result.get("content", []):
            if isinstance(item, dict) and "text" in item:
                content_parts.append(item["text"])
            else:
                content_parts.append(str(item))

        return "\n".join(content_parts) if content_parts else json_mod.dumps({"success": True})

    async def disconnect_all(self):
        """Kill all MCP server subprocesses."""
        for name, transport in self._transports.items():
            try:
                transport.kill()
                logger.info("Disconnected from MCP server: %s", name)
            except Exception as e:
                logger.warning("Error disconnecting from %s: %s", name, e)

        self._transports.clear()
        self._tools.clear()
        self._connected = False
