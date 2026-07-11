"""
MCP Reminders Server — manages user reminders as an MCP tool server.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously to avoid Windows ProactorEventLoop bugs.
"""

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("reminders")

REMINDERS_DIR = Path.home() / ".desktop-companion" / "reminders"
CALENDAR_DIR = Path.home() / ".desktop-companion" / "calendar"

def _load_reminder(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def _safe_filename(title: str) -> str:
    safe = re.sub(r'[^\w\s-]', '', title)[:40].strip()
    return re.sub(r'\s+', '_', safe) or "reminder"

def _create_ics_file(title: str, due_date: str, due_time: str | None, reminder_minutes_before: int = 15) -> str:
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)

    if due_time:
        dtstart = f"{due_date}T{due_time}:00"
        h, m = map(int, due_time.split(":"))
        end_m = m + 60
        end_h = h + end_m // 60
        end_m = end_m % 60
        dtend = f"{due_date}T{end_h:02d}:{end_m:02d}:00"
    else:
        dtstart = due_date
        dtend = ""

    escaped_title = title.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")

    uid = f"{uuid.uuid4()}@desktop-companion"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    ics_content = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Luna Desktop Companion//reminders\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        + (f"DTEND:{dtend}\r\n" if dtend else "")
        + f"SUMMARY:{escaped_title}\r\n"
        "DESCRIPTION:Luna reminder\r\n"
        "BEGIN:VALARM\r\n"
        f"TRIGGER:-PT{reminder_minutes_before}M\r\n"
        "ACTION:DISPLAY\r\n"
        "DESCRIPTION:Reminder\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = _safe_filename(title)
    ics_path = CALENDAR_DIR / f"{timestamp}_{safe_title}.ics"
    ics_path.write_text(ics_content, encoding="utf-8")

    logger.info("Calendar event created: %s", ics_path)
    return str(ics_path)


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
                "serverInfo": {"name": "reminders", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "create_reminder",
                        "description": "Create a new reminder with a title and due date. This will open the user's OS calendar app.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "What to be reminded about"},
                                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                                "due_time": {"type": "string", "description": "Optional time in HH:MM format (24h)"},
                            },
                            "required": ["title", "due_date"],
                        },
                    },
                    {
                        "name": "list_reminders",
                        "description": "List all reminders, optionally filtered by date",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string", "description": "Filter by date (YYYY-MM-DD). Omit for all."},
                            },
                        },
                    },
                    {
                        "name": "delete_reminder",
                        "description": "Delete a reminder by its ID",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "reminder_id": {"type": "string", "description": "The reminder ID to delete"},
                            },
                            "required": ["reminder_id"],
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        REMINDERS_DIR.mkdir(parents=True, exist_ok=True)

        if tool_name == "create_reminder":
            title = arguments.get("title", "").strip()
            due_date = arguments.get("due_date", "").strip()
            due_time = arguments.get("due_time", "").strip()

            if not title:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": "Title is required"}}
            if not due_date:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": "Due date is required (YYYY-MM-DD)"}}

            try:
                datetime.strptime(due_date, "%Y-%m-%d")
            except ValueError:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Invalid date format: {due_date}. Use YYYY-MM-DD."}}

            if due_time:
                try:
                    datetime.strptime(due_time, "%H:%M")
                except ValueError:
                    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Invalid time format: {due_time}. Use HH:MM (24h)."}}

            reminder_id = uuid.uuid4().hex[:12]
            reminder = {
                "id": reminder_id,
                "title": title,
                "due_date": due_date,
                "due_time": due_time or None,
                "created_at": datetime.now().isoformat(),
                "completed": False,
            }

            reminder_path = REMINDERS_DIR / f"{reminder_id}.json"
            reminder_path.write_text(json.dumps(reminder, indent=2), encoding="utf-8")
            ics_path = _create_ics_file(title, due_date, due_time)

            logger.info("Reminder created: %s", title)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(reminder)},
                        {"type": "text", "text": json.dumps({"ics_path": ics_path})},
                    ],
                },
            }

        elif tool_name == "list_reminders":
            filter_date = arguments.get("date", "").strip()
            reminders = []
            for p in sorted(REMINDERS_DIR.glob("*.json")):
                try:
                    r = _load_reminder(p)
                    if r.get("completed"): continue
                    if filter_date and r.get("due_date") != filter_date: continue
                    reminders.append(r)
                except:
                    continue
            reminders.sort(key=lambda r: (r.get("due_date", ""), r.get("due_time") or "99:99"))
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(reminders)}]}}

        elif tool_name == "delete_reminder":
            reminder_id = arguments.get("reminder_id", "")
            reminder_path = REMINDERS_DIR / f"{reminder_id}.json"
            if reminder_path.exists():
                reminder = _load_reminder(reminder_path)
                reminder_path.unlink()
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": f"Deleted reminder: {reminder.get('title', reminder_id)}"}]}}
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Reminder not found: {reminder_id}"}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line: break
            line = line.strip()
            if not line: continue
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except:
            continue

if __name__ == "__main__":
    main()