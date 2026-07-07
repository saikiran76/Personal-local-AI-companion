"""
MCP FileSystem Server — exposes file system operations as MCP tools.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously in a thread to avoid Windows ProactorEventLoop bugs.
"""

import json
import sys
import threading
from pathlib import Path


def handle_request(request: dict) -> dict:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "filesystem", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read contents of a file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "write_file",
                        "description": "Write content to a file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                    {
                        "name": "list_directory",
                        "description": "List files in a directory",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "read_file":
            path = Path(arguments.get("path", ""))
            if path.exists():
                content = path.read_text(encoding="utf-8")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": content}],
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -1, "message": f"File not found: {path}"},
            }

        elif tool_name == "write_file":
            path = Path(arguments.get("path", ""))
            content = arguments.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Written to {path}"}],
                },
            }

        elif tool_name == "list_directory":
            path = Path(arguments.get("path", "."))
            if path.is_dir():
                entries = [
                    {"name": p.name, "is_dir": p.is_dir()}
                    for p in sorted(path.iterdir())
                ]
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(entries)}],
                    },
                }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """Run the MCP server over stdio — synchronous reads to avoid ProactorEventLoop."""
    # Read lines from stdin synchronously in the main thread.
    # This avoids connect_read_pipe() which crashes on Windows ProactorEventLoop.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            continue
        except BrokenPipeError:
            break
        except Exception:
            break


if __name__ == "__main__":
    main()
