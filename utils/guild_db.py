import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


class GuildDB:
    """Runtime state for moderation features (starboard posts, reports, misc meta).

    Static per-guild settings (feature toggles, welcome/goodbye, auto roles,
    starboard/report/logging config) live in data/config.yaml via
    utils.bot_config. This class only holds state the bot writes while running.
    """

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.db_path = os.path.join("data", "servers", str(guild_id), "server.db")
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.cursor = self.conn.cursor()
        self._setup_database()

    def _setup_database(self):
        """Create runtime-state tables and indexes if they don't exist"""

        # --- Starboard: original message -> starboard post mapping ---
        self.cursor.execute(
            """CREATE TABLE IF NOT EXISTS starboard_messages
               (original_message_id INTEGER PRIMARY KEY,
                original_channel_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                starboard_message_id INTEGER,
                star_count INTEGER DEFAULT 0,
                content_snippet TEXT)""")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_starboard_starboard_msg "
                            "ON starboard_messages(starboard_message_id)")

        # --- Reports ---
        self.cursor.execute(
            """CREATE TABLE IF NOT EXISTS reports
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                reported_message_id INTEGER NOT NULL,
                reported_channel_id INTEGER NOT NULL,
                reported_author_id INTEGER NOT NULL,
                reason TEXT,
                message_link TEXT,
                message_snippet TEXT,
                status TEXT DEFAULT 'pending',
                resolved_by INTEGER,
                resolved_at TEXT,
                timestamp TEXT NOT NULL)""")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_timestamp ON reports(timestamp)")

        # --- Meta: runtime key/value (e.g. starboard leaderboard message id) ---
        self.cursor.execute(
            """CREATE TABLE IF NOT EXISTS meta
               (key TEXT PRIMARY KEY,
                value TEXT)""")
        self.conn.commit()


    # -------------------- SERIALIZATION

    @staticmethod
    def _serialize(data: Any) -> Optional[str]:
        return None if data is None else json.dumps(data)

    @staticmethod
    def _deserialize(raw: Optional[str]) -> Any:
        return None if raw is None else json.loads(raw)

    # -------------------- QUERY HELPERS

    def _fetch_row(self) -> Optional[dict]:
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def _fetch_rows(self) -> list[dict]:
        return [dict(row) for row in self.cursor.fetchall()]


    # -------------------- META (runtime key/value)

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Fetch the value stored under key (JSON-decoded), or default if unset"""
        self.cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = self.cursor.fetchone()
        if not row:
            return default
        return self._deserialize(row["value"])

    def set_meta(self, key: str, value: Any):
        """Overwrite the value stored under key (JSON-encoded)"""
        self.cursor.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, self._serialize(value)))
        self.conn.commit()


    # -------------------- STARBOARD MESSAGES

    def get_starboard_message(self, original_message_id: int) -> Optional[dict]:
        self.cursor.execute("SELECT * FROM starboard_messages WHERE original_message_id = ?",
                            (original_message_id,))
        return self._fetch_row()

    def get_starboard_message_by_starboard_id(self, starboard_message_id: int) -> Optional[dict]:
        self.cursor.execute("SELECT * FROM starboard_messages WHERE starboard_message_id = ?",
                            (starboard_message_id,))
        return self._fetch_row()

    def add_starboard_message(self, original_message_id: int, original_channel_id: int,
                              author_id: int, starboard_message_id: Optional[int] = None,
                              star_count: int = 0, content_snippet: str = ""):
        self.cursor.execute(
            "INSERT INTO starboard_messages (original_message_id, original_channel_id, "
            "author_id, starboard_message_id, star_count, content_snippet) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (original_message_id, original_channel_id, author_id,
             starboard_message_id, star_count, content_snippet))
        self.conn.commit()

    def update_starboard_message(self, original_message_id: int, **kwargs: Any):
        if not kwargs:
            return
        columns = ", ".join(f"{k} = ?" for k in kwargs)
        self.cursor.execute(f"UPDATE starboard_messages SET {columns} WHERE original_message_id = ?",
                            (*kwargs.values(), original_message_id))
        self.conn.commit()

    def remove_starboard_message(self, original_message_id: int):
        self.cursor.execute("DELETE FROM starboard_messages WHERE original_message_id = ?",
                            (original_message_id,))
        self.conn.commit()

    def get_top_starred_messages(self, limit: int = 10) -> list[dict]:
        self.cursor.execute(
            "SELECT * FROM starboard_messages WHERE starboard_message_id IS NOT NULL "
            "ORDER BY star_count DESC, original_message_id ASC LIMIT ?",
            (limit,))
        return self._fetch_rows()


    # -------------------- REPORTS

    def add_report(self, reporter_id: int, reported_message_id: int, reported_channel_id: int,
                   reported_author_id: int, reason: str = "",
                   message_link: Optional[str] = None,
                   message_snippet: Optional[str] = None) -> Optional[int]:
        timestamp = datetime.now(timezone.utc).isoformat()
        self.cursor.execute(
            "INSERT INTO reports (reporter_id, reported_message_id, reported_channel_id, "
            "reported_author_id, reason, message_link, message_snippet, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (reporter_id, reported_message_id, reported_channel_id,
             reported_author_id, reason, message_link, message_snippet, timestamp))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_report(self, report_id: int) -> Optional[dict]:
        self.cursor.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        return self._fetch_row()

    def get_reports(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM reports"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        self.cursor.execute(query, params)
        return self._fetch_rows()

    def update_report_status(self, report_id: int, status: str,
                             resolved_by: Optional[int] = None):
        resolved_at = datetime.now(timezone.utc).isoformat() if resolved_by else None
        self.cursor.execute(
            "UPDATE reports SET status = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_by, resolved_at, report_id))
        self.conn.commit()


    # -------------------- LIFECYCLE

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()