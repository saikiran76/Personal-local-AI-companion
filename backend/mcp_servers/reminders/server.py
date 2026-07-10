"""
MCP Reminders Server — manages user reminders via MCP tools.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously to avoid Windows ProactorEventLoop bugs.

Reminders are stored as individual JSON files in ~/.desktop-companion/reminders/.
Reliable alerts are scheduled via Windows Task Scheduler (survives app close / sleep).
.ics files are generated as optional calendar exports (not auto-opened).
"""

import json
import logging
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("reminders")

REMINDERS_DIR = Path.home() / ".desktop-companion" / "reminders"
CALENDAR_DIR = Path.home() / ".desktop-companion" / "calendar"


def _safe_filename(title: str) -> str:
    """Convert a title to a safe filename fragment."""
    safe = re.sub(r'[^\w\s-]', '', title)[:40].strip()
    return re.sub(r'\s+', '_', safe) or "reminder"




def _schedule_task(title: str, due_date: str, due_time: str) -> str:
    """Create a Windows scheduled task that fires a toast notification.

    Uses BurntToast (installed) as primary, falls back to .NET MessageBox.
    The task runs at the exact due time, independent of whether Luna is running.
    Survives app close, laptop sleep, etc.

    All three failure points addressed by writing scripts to files
    (avoids PowerShell -Command string mangling):
    1. Locale-independent date parsing via ParseExact in task creation script
    2. Explicit Interactive principal so task runs in user's desktop session
    3. BurntToast as primary (registers working AUMID shim for unpackaged scripts)
    """
    import tempfile

    # Escape single quotes in title for PowerShell
    safe_title = title.replace("'", "''").replace('"', '`"')

    # Notification script: BurntToast primary, MessageBox fallback
    ps_script = f"""
try {{
    Import-Module BurntToast -ErrorAction Stop
    New-BurntToastNotification -Text 'Luna Reminder', '{safe_title}'
}} catch {{
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('{safe_title}', 'Luna Reminder', 'OK', 'Information')
}}
""".strip()

    script_dir = Path(tempfile.gettempdir()) / "luna_reminders"
    script_dir.mkdir(exist_ok=True)
    script_path = script_dir / f"reminder_{uuid.uuid4().hex[:8]}.ps1"
    script_path.write_text(ps_script, encoding="utf-8")

    task_name = f"LunaReminder_{uuid.uuid4().hex[:8]}"

    # Write task creation to a script file and call it with parameters
    # (avoids PowerShell -Command string mangling which silently breaks quoting)
    fire_at = f"{due_date} {due_time}"
    create_script = script_dir / "create_scheduled_task.ps1"
    create_script_content = (
    'param([string]$TaskName, [string]$ScriptPath, [string]$FireAt)\n'
    '$dt = [DateTime]::ParseExact($FireAt, "yyyy-MM-dd HH:mm", $null)\n'
    '$action = New-ScheduledTaskAction -Execute "powershell.exe" '
    '-Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""\n'
    '$trigger = New-ScheduledTaskTrigger -Once -At $dt\n'
    '$settings = New-ScheduledTaskSettingsSet '
    '-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable '
    '-ExecutionTimeLimit (New-TimeSpan -Minutes 2)\n'
    '$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited\n'
    'Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force\n'
    )
    create_script.write_text(create_script_content, encoding="utf-8")

    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(create_script),
                "-TaskName", task_name,
                "-ScriptPath", str(script_path),
                "-FireAt", fire_at,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("Scheduled task created: %s for %s %s", task_name, due_date, due_time)
            return task_name
        else:
            logger.warning("Register-ScheduledTask failed: %s", result.stderr.strip())
            return ""
    except Exception as e:
        logger.warning("schtasks error: %s", e)
        return ""
    except Exception as e:
        logger.warning("schtasks error: %s", e)
        return ""


def _delete_scheduled_task(task_name: str) -> bool:
    """Delete a scheduled task by name."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f'Unregister-ScheduledTask -TaskName "{task_name}" -Confirm:$false -ErrorAction SilentlyContinue'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _create_ics_file(title: str, due_date: str, due_time: str | None, reminder_minutes_before: int = 15) -> str:
    """Create an .ics calendar file as an optional export (not auto-opened).

    Returns the path to the created .ics file.
    """
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

    logger.info("Calendar file created (export only): %s", ics_path)
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
                "serverInfo": {"name": "reminders", "version": "0.3.0"},
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
                        "description": (
                            "Create a reminder that reliably fires at the scheduled time. "
                            "Uses Windows Task Scheduler — survives app close, laptop sleep, "
                            "and doesn't depend on any calendar app. Also generates an .ics "
                            "file as an optional calendar export."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "What to be reminded about"},
                                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                                "due_time": {"type": "string", "description": "Time in HH:MM format (24h). Required for reliable scheduling."},
                            },
                            "required": ["title", "due_date", "due_time"],
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
                        "description": "Delete a reminder and cancel its scheduled task",
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
            if not due_time:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": "Time is required for reliable scheduling (HH:MM)"}}

            try:
                datetime.strptime(due_date, "%Y-%m-%d")
            except ValueError:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Invalid date format: {due_date}. Use YYYY-MM-DD."}}

            try:
                datetime.strptime(due_time, "%H:%M")
            except ValueError:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Invalid time format: {due_time}. Use HH:MM (24h)."}}

            reminder_id = uuid.uuid4().hex[:12]

            # Schedule the reliable notification via Task Scheduler
            task_name = _schedule_task(title, due_date, due_time)

            # Generate .ics as optional calendar export
            ics_path = _create_ics_file(title, due_date, due_time)

            reminder = {
                "id": reminder_id,
                "title": title,
                "due_date": due_date,
                "due_time": due_time,
                "created_at": datetime.now().isoformat(),
                "completed": False,
                "task_name": task_name,  # for cleanup on delete
            }

            reminder_path = REMINDERS_DIR / f"{reminder_id}.json"
            reminder_path.write_text(json.dumps(reminder, indent=2), encoding="utf-8")

            time_str = f" at {due_time}"
            logger.info("Reminder created: %s (due %s%s, task=%s)", title, due_date, time_str, task_name or "none")

            result = {
                "id": reminder_id,
                "title": title,
                "due_date": due_date,
                "due_time": due_time,
                "created_at": datetime.now().isoformat(),
                "completed": False,
            }
            if task_name:
                result["scheduled"] = f"Notification will fire at {due_time} on {due_date} via Windows Task Scheduler"
            else:
                result["scheduled"] = "Task scheduling failed — reminder saved but may not fire reliably"
            if ics_path:
                result["ics_path"] = ics_path

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result)},
                    ],
                },
            }

        elif tool_name == "list_reminders":
            filter_date = arguments.get("date", "").strip()
            reminders = []

            for p in sorted(REMINDERS_DIR.glob("*.json")):
                try:
                    r = json.loads(p.read_text(encoding="utf-8"))
                    if r.get("completed"):
                        continue
                    if filter_date and r.get("due_date") != filter_date:
                        continue
                    reminders.append(r)
                except (json.JSONDecodeError, KeyError):
                    continue

            reminders.sort(key=lambda r: (r.get("due_date", ""), r.get("due_time") or "99:99"))

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(reminders)}],
                },
            }

        elif tool_name == "delete_reminder":
            reminder_id = arguments.get("reminder_id", "")
            reminder_path = REMINDERS_DIR / f"{reminder_id}.json"

            if reminder_path.exists():
                reminder = json.loads(reminder_path.read_text(encoding="utf-8"))
                # Cancel the scheduled task
                task_name = reminder.get("task_name", "")
                if task_name:
                    _delete_scheduled_task(task_name)
                    logger.info("Cancelled scheduled task: %s", task_name)
                reminder_path.unlink()
                logger.info("Reminder deleted: %s", reminder.get("title", reminder_id))
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Deleted reminder: {reminder.get('title', reminder_id)}"}],
                    },
                }

            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Reminder not found: {reminder_id}"}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


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
