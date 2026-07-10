"""
MCP FileSystem Server — exposes file system operations as MCP tools.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously in a thread to avoid Windows ProactorEventLoop bugs.
"""

import fnmatch
import json
import logging
import shutil
import sys
import threading
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("filesystem")


# --- Path alias resolution ---

_PATH_ALIASES = {
    "downloads": Path.home() / "Downloads",
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "home": Path.home(),
}

# Extension-based categories for organize_directory
_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Video": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code": {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".sh", ".bat"},
}

# In-memory store for preview_organize plans
_organize_plans: dict[str, dict] = {}


def _resolve_path(path_str: str) -> Path:
    """Resolve a path string, expanding aliases like 'downloads' → ~/Downloads."""
    normalized = path_str.lower().strip().rstrip("/\\")
    if normalized in _PATH_ALIASES:
        return _PATH_ALIASES[normalized]
    return Path(path_str)


def _categorize_file(filename: str) -> str:
    """Determine category for a file based on extension."""
    ext = Path(filename).suffix.lower()
    for category, extensions in _CATEGORIES.items():
        if ext in extensions:
            return category
    return "Other"


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
                "serverInfo": {"name": "filesystem", "version": "0.2.0"},
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
                        "description": "Write content to a file, creating parent directories if needed",
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
                        "description": "List files and subdirectories in a directory",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "move_file",
                        "description": "Move or rename a file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                        },
                    },
                    {
                        "name": "copy_file",
                        "description": "Copy a file to a new location",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                        },
                    },
                    {
                        "name": "delete_file",
                        "description": "Delete a file (will not delete directories)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "glob_search",
                        "description": "Find files matching a glob pattern (e.g. '*.pdf', '**/*.jpg')",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "pattern": {"type": "string"},
                                "root_dir": {"type": "string"},
                            },
                            "required": ["pattern"],
                        },
                    },
                    {
                        "name": "mkdir",
                        "description": "Create a directory (and parent directories if needed)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "preview_organize",
                        "description": "Dry-run: preview how files in a directory would be organized by type into subdirectories. Returns a plan_id for execute_organize.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "rules": {"type": "object"},
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "execute_organize",
                        "description": "Execute a previously previewed organize plan (moves files into categorized subdirectories)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "plan_id": {"type": "string"},
                            },
                            "required": ["plan_id"],
                        },
                    },
                    {
                        "name": "read_pdf",
                        "description": "Extract text from a PDF file using pypdf. Returns page-by-page text content.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Path to the PDF file"},
                                "max_pages": {"type": "integer", "description": "Maximum pages to extract (1-30, default 10)"},
                            },
                            "required": ["path"],
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

    if tool_name == "read_file":
        path = _resolve_path(arguments.get("path", ""))
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8")
            return {"content": [{"type": "text", "text": content}]}
        return {"content": [{"type": "text", "text": f"File not found: {path}"}]}

    elif tool_name == "write_file":
        path = _resolve_path(arguments.get("path", ""))
        content = arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"content": [{"type": "text", "text": f"Written to {path}"}]}

    elif tool_name == "list_directory":
        path = _resolve_path(arguments.get("path", "."))
        if path.is_dir():
            entries = [
                {"name": p.name, "is_dir": p.is_dir()}
                for p in sorted(path.iterdir())
            ]
            return {"content": [{"type": "text", "text": json.dumps(entries)}]}
        return {"content": [{"type": "text", "text": f"Not a directory: {path}"}]}

    elif tool_name == "move_file":
        src = _resolve_path(arguments.get("source", ""))
        dst = _resolve_path(arguments.get("destination", ""))
        if not src.exists():
            return {"content": [{"type": "text", "text": f"Source not found: {src}"}]}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"content": [{"type": "text", "text": f"Moved {src} → {dst}"}]}

    elif tool_name == "copy_file":
        src = _resolve_path(arguments.get("source", ""))
        dst = _resolve_path(arguments.get("destination", ""))
        if not src.exists():
            return {"content": [{"type": "text", "text": f"Source not found: {src}"}]}
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        return {"content": [{"type": "text", "text": f"Copied {src} → {dst}"}]}

    elif tool_name == "delete_file":
        path = _resolve_path(arguments.get("path", ""))
        if not path.exists():
            return {"content": [{"type": "text", "text": f"Not found: {path}"}]}
        if path.is_dir():
            return {"content": [{"type": "text", "text": f"Cannot delete directory with delete_file. Use a file path: {path}"}]}
        path.unlink()
        return {"content": [{"type": "text", "text": f"Deleted {path}"}]}

    elif tool_name == "glob_search":
        pattern = arguments.get("pattern", "*")
        root_dir = _resolve_path(arguments.get("root_dir", "."))
        if not root_dir.is_dir():
            return {"content": [{"type": "text", "text": f"Not a directory: {root_dir}"}]}

        matches = []
        for p in root_dir.rglob("*"):
            if p.is_file() and fnmatch.fnmatch(p.name, pattern):
                matches.append(str(p))
                if len(matches) >= 100:
                    break

        return {"content": [{"type": "text", "text": json.dumps(matches)}]}

    elif tool_name == "mkdir":
        path = _resolve_path(arguments.get("path", ""))
        path.mkdir(parents=True, exist_ok=True)
        return {"content": [{"type": "text", "text": f"Created directory: {path}"}]}

    elif tool_name == "preview_organize":
        path = _resolve_path(arguments.get("path", "."))
        if not path.is_dir():
            return {"content": [{"type": "text", "text": f"Not a directory: {path}"}]}

        # Only top-level files, skip existing subdirectories
        files = [p for p in path.iterdir() if p.is_file()]

        planned_moves = []
        category_counts = {}
        for f in files:
            category = _categorize_file(f.name)
            dest_dir = path / category
            dest_path = dest_dir / f.name
            planned_moves.append({
                "src": str(f),
                "dest": str(dest_path),
                "category": category,
            })
            category_counts[category] = category_counts.get(category, 0) + 1

        plan_id = uuid.uuid4().hex[:12]
        summary = f"{len(files)} files → {len(category_counts)} folders"
        categories_summary = ", ".join(f"{k}: {v}" for k, v in sorted(category_counts.items()))

        _organize_plans[plan_id] = {
            "path": str(path),
            "moves": planned_moves,
            "summary": summary,
            "categories": category_counts,
        }

        return {"content": [{"type": "text", "text": json.dumps({
            "plan_id": plan_id,
            "summary": summary,
            "categories": categories_summary,
            "total_files": len(files),
            "planned_moves": planned_moves,
        })}]}

    elif tool_name == "execute_organize":
        plan_id = arguments.get("plan_id", "")
        plan = _organize_plans.get(plan_id)
        if not plan:
            return {"content": [{"type": "text", "text": f"Plan not found: {plan_id}. Run preview_organize first."}]}

        moved = 0
        skipped = 0
        errors = 0
        for move in plan["moves"]:
            src = Path(move["src"])
            dst = Path(move["dest"])
            try:
                if not src.exists():
                    skipped += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception:
                errors += 1

        # Clean up plan
        _organize_plans.pop(plan_id, None)

        return {"content": [{"type": "text", "text": json.dumps({
            "moved": moved,
            "skipped": skipped,
            "errors": errors,
            "categories": plan["categories"],
        })}]}

    elif tool_name == "read_pdf":
        path = _resolve_path(arguments.get("path", ""))
        if not path.exists():
            return {"content": [{"type": "text", "text": f"File not found: {path}"}]}
        if not path.suffix.lower() == ".pdf":
            return {"content": [{"type": "text", "text": f"Not a PDF file: {path}"}]}

        max_pages = arguments.get("max_pages", 10)
        max_pages = max(1, min(30, max_pages))  # Clamp to 1-30

        try:
            from pypdf import PdfReader
        except ImportError:
            return {"content": [{"type": "text", "text": "pypdf not installed. Run: uv pip install pypdf"}]}

        try:
            reader = PdfReader(str(path))
            total_pages = len(reader.pages)
            pages_to_read = min(max_pages, total_pages)
            text_parts = []
            char_count = 0
            CHAR_CAP = 8000

            for i in range(pages_to_read):
                page_text = reader.pages[i].extract_text() or ""
                page_text = page_text.strip()
                if not page_text:
                    continue

                separator = f"\n\n--- Page {i + 1} ---\n\n"
                if char_count + len(separator) + len(page_text) > CHAR_CAP:
                    # Truncate this page to fit within cap
                    remaining = CHAR_CAP - char_count - len(separator)
                    if remaining > 50:
                        text_parts.append(separator + page_text[:remaining] + "...")
                    break

                text_parts.append(separator + page_text)
                char_count += len(separator) + len(page_text)

            if not text_parts:
                return {"content": [{"type": "text", "text": "PDF contains no extractable text (may be scanned/image-based)."}]}

            result_text = "".join(text_parts)

            # Add truncation notice if partial
            if pages_to_read < total_pages or char_count >= CHAR_CAP:
                result_text += f"\n\n(showing first {pages_to_read} of {total_pages} pages, ~{char_count} characters)"

            return {"content": [{"type": "text", "text": result_text}]}

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Failed to read PDF: {e}"}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}


def main():
    """Run the MCP server over stdio — synchronous reads."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
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
