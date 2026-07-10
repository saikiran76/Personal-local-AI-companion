"""SQLite persistence layer — single file at ~/.desktop-companion/luna.db.

Four tables:
  permissions   — scope-based access control (email, calendar, files, etc.)
  activity_log  — one-liner summaries of tool calls (NOT full content)
  conversations — session containers
  messages      — individual turns within a conversation
  memories      — user preferences, facts, writing style
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_DIR = Path.home() / ".desktop-companion"
_DB_PATH = _DB_DIR / "luna.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS permissions (
  scope TEXT PRIMARY KEY,
  granted INTEGER NOT NULL DEFAULT 0,
  granted_at TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  scope TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  title TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  tool_name TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_memories_cat ON memories(category);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper. Call init() once at startup."""

    def __init__(self, path: Path | str = _DB_PATH):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def init(self):
        """Open connection and create tables if needed."""
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        # Migration: add tool_name column to messages if missing (existing DBs)
        try:
            self._conn.execute("SELECT tool_name FROM messages LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE messages ADD COLUMN tool_name TEXT")
        self._conn.commit()
        logger.info("Database initialized: %s", self._path)

    @contextmanager
    def _tx(self):
        """Yield a cursor inside a transaction. Auto-commits or rolls back."""
        assert self._conn, "Database not initialized"
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def get_permission(self, scope: str) -> bool:
        with self._tx() as cur:
            cur.execute("SELECT granted FROM permissions WHERE scope = ?", (scope,))
            row = cur.fetchone()
            return bool(row["granted"]) if row else False

    def set_permission(self, scope: str, granted: bool):
        with self._tx() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO permissions (scope, granted, granted_at) VALUES (?, ?, ?)",
                (scope, int(granted), _now()),
            )

    def list_permissions(self) -> list[dict]:
        with self._tx() as cur:
            cur.execute("SELECT scope, granted, granted_at FROM permissions ORDER BY scope")
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def log_activity(self, scope: str, tool_name: str, summary: str):
        """Append a one-liner to the activity log. summary must be short."""
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO activity_log (timestamp, scope, tool_name, summary) VALUES (?, ?, ?, ?)",
                (_now(), scope, tool_name, summary[:200]),
            )

    def get_activity(self, limit: int = 50) -> list[dict]:
        with self._tx() as cur:
            cur.execute(
                "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, title: str | None = None) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO conversations (started_at, title) VALUES (?, ?)",
                (_now(), title),
            )
            return cur.lastrowid

    def update_conversation_title(self, conv_id: int, title: str):
        with self._tx() as cur:
            cur.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))

    def list_conversations(self, limit: int = 20) -> list[dict]:
        with self._tx() as cur:
            cur.execute(
                "SELECT id, started_at, title FROM conversations ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def delete_conversation(self, conv_id: int):
        with self._tx() as cur:
            cur.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            cur.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, conv_id: int, role: str, content: str, tool_name: str | None = None) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_name, created_at) VALUES (?, ?, ?, ?, ?)",
                (conv_id, role, content[:2000], tool_name, _now()),
            )
            return cur.lastrowid

    def get_messages(self, conv_id: int, limit: int = 50) -> list[dict]:
        with self._tx() as cur:
            cur.execute(
                "SELECT id, role, content, tool_name, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                (conv_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_recent_messages(self, conv_id: int, limit: int = 10) -> list[dict]:
        """Get the last N messages in a conversation (for context window)."""
        with self._tx() as cur:
            cur.execute(
                "SELECT role, content FROM ("
                "  SELECT id, role, content FROM messages "
                "  WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?"
                ") sub ORDER BY id ASC",
                (conv_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Memories
    # ------------------------------------------------------------------

    def add_memory(self, category: str, content: str) -> int:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO memories (category, content, created_at) VALUES (?, ?, ?)",
                (category, content[:2000], _now()),
            )
            return cur.lastrowid

    def get_memories(self, category: str | None = None, limit: int = 50) -> list[dict]:
        with self._tx() as cur:
            if category:
                cur.execute(
                    "SELECT * FROM memories WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                    (category, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def search_memories(self, query: str, limit: int = 10) -> list[dict]:
        with self._tx() as cur:
            cur.execute(
                "SELECT * FROM memories WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def delete_memory(self, mem_id: int):
        with self._tx() as cur:
            cur.execute("DELETE FROM memories WHERE id = ?", (mem_id,))

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# Singleton — import and call db.init() at startup
db = Database()
