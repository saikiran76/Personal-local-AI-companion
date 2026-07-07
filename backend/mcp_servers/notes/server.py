"""
MCP Notes Server — manages user notes as an MCP tool server.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously to avoid Windows ProactorEventLoop bugs.
"""

import json
import sys
from pathlib import Path


NOTES_DIR = Path.home() / ".desktop-companion" / "notes"


def handle_request(request: dict) -> dict:
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
                "serverInfo": {"name": "notes", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "create_note",
                        "description": "Create a new note with title and content",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["title", "content"],
                        },
                    },
                    {
                        "name": "list_notes",
                        "description": "List all saved notes",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "search_notes",
                        "description": "Search notes by content",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "delete_note",
                        "description": "Delete a note by ID",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"note_id": {"type": "string"}},
                            "required": ["note_id"],
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        NOTES_DIR.mkdir(parents=True, exist_ok=True)

        if tool_name == "create_note":
            title = arguments.get("title", "Untitled")
            content = arguments.get("content", "")
            note_id = title.lower().replace(" ", "_")[:50]
            note_path = NOTES_DIR / f"{note_id}.md"
            note_path.write_text(f"# {title}\n\n{content}", encoding="utf-8")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Note created: {note_id}"}],
                },
            }

        elif tool_name == "list_notes":
            notes = []
            for p in sorted(NOTES_DIR.glob("*.md")):
                notes.append({"id": p.stem, "title": p.stem.replace("_", " ").title()})
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(notes)}],
                },
            }

        elif tool_name == "search_notes":
            query = arguments.get("query", "").lower()
            results = []
            for p in NOTES_DIR.glob("*.md"):
                text = p.read_text(encoding="utf-8")
                if query in text.lower():
                    results.append({"id": p.stem, "title": p.stem.replace("_", " ").title()})
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(results)}],
                },
            }

        elif tool_name == "delete_note":
            note_id = arguments.get("note_id", "")
            note_path = NOTES_DIR / f"{note_id}.md"
            if note_path.exists():
                note_path.unlink()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Deleted note: {note_id}"}],
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -1, "message": f"Note not found: {note_id}"},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """Run the MCP server over stdio — synchronous reads."""
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
