"""
MCP Browser Server — browser automation via MCP protocol.
"""

import json
import sys
import webbrowser


async def handle_request(request: dict) -> dict:
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
                "serverInfo": {"name": "browser", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "open_browser",
                        "description": "Open a URL in the default browser",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    },
                    {
                        "name": "search_web",
                        "description": "Open a search query in the default browser",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "open_browser":
            url = arguments.get("url", "")
            webbrowser.open(url)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Opened: {url}"}],
                },
            }

        elif tool_name == "search_web":
            query = arguments.get("query", "")
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            webbrowser.open(url)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Searched for: {query}"}],
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


if __name__ == "__main__":
    import asyncio

    async def main():
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode().strip())
                response = await handle_request(request)
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
            except json.JSONDecodeError:
                continue

    asyncio.run(main())
