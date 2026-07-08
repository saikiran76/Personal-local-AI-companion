"""
MCP Email Server — email draft and client integration via MCP tools.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously in a thread to avoid Windows ProactorEventLoop bugs.
"""

import json
import sys
import webbrowser
from datetime import datetime
from email.utils import formatdate
from pathlib import Path


_DRAFTS_DIR = Path.home() / ".desktop-companion" / "drafts"
_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

_MAILTO_MAX_BODY = 2000  # practical safe limit for mailto: URLs


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
                "serverInfo": {"name": "email", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "draft_email",
                        "description": "Save an email draft as a .eml file. Use this when the user wants to compose or draft an email.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "to": {"type": "string"},
                                "subject": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["to", "subject", "body"],
                        },
                    },
                    {
                        "name": "open_email_client",
                        "description": "Open the default email client with pre-filled fields. Best for short emails under 2000 characters.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "to": {"type": "string"},
                                "subject": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["to"],
                        },
                    },
                    {
                        "name": "list_drafts",
                        "description": "List saved email drafts",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            result = _execute_tool(tool_name, arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": str(e)}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _execute_tool(tool_name: str, arguments: dict) -> dict:
    """Execute a tool and return the MCP result dict."""

    if tool_name == "draft_email":
        to = arguments.get("to", "")
        subject = arguments.get("subject", "(no subject)")
        body = arguments.get("body", "")

        # Create .eml content
        date_str = formatdate(localtime=True)
        eml_content = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: {date_str}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"\r\n"
            f"{body}\r\n"
        )

        # Save to drafts folder
        safe_subject = "".join(c if c.isalnum() or c in " -_" else "" for c in subject)[:50]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{safe_subject}.eml"
        draft_path = _DRAFTS_DIR / filename
        draft_path.write_text(eml_content, encoding="utf-8")

        return {"content": [{"type": "text", "text": f"Draft saved: {draft_path}"}]}

    elif tool_name == "open_email_client":
        to = arguments.get("to", "")
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")

        # Check body length
        if len(body) > _MAILTO_MAX_BODY:
            # Fall back to saving a draft instead
            return _execute_tool("draft_email", arguments)

        # Build mailto: URL
        import urllib.parse
        params = {}
        if subject:
            params["subject"] = subject
        if body:
            params["body"] = body

        query = urllib.parse.urlencode(params)
        mailto_url = f"mailto:{to}?{query}" if query else f"mailto:{to}"

        webbrowser.open(mailto_url)

        return {"content": [{"type": "text", "text": f"Opened email client for {to}"}]}

    elif tool_name == "list_drafts":
        drafts = []
        for p in sorted(_DRAFTS_DIR.glob("*.eml"), reverse=True):
            drafts.append({"filename": p.name, "path": str(p)})

        if not drafts:
            return {"content": [{"type": "text", "text": "No drafts found."}]}

        return {"content": [{"type": "text", "text": json.dumps(drafts)}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}


def main():
    """Run the MCP server over stdio — synchronous reads to avoid ProactorEventLoop."""
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
