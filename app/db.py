"""SQLite persistence. One shared connection behind a lock — plenty at fleet scale."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

JSON_FIELDS = ("vars", "config", "identity", "action_overrides", "last_result")
EVENTS_KEPT_PER_DEVICE = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    connector TEXT NOT NULL DEFAULT 'adb',
    profile_id TEXT,
    mode TEXT NOT NULL DEFAULT 'monitor',
    enabled INTEGER NOT NULL DEFAULT 1,
    vars TEXT NOT NULL DEFAULT '{}',
    config TEXT NOT NULL DEFAULT '{}',
    action_overrides TEXT NOT NULL DEFAULT '{}',
    identity TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'unknown',
    status_detail TEXT NOT NULL DEFAULT '',
    last_result TEXT NOT NULL DEFAULT '[]',
    last_seen TEXT,
    last_audit TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(host, port)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, id DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- devices

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        device = dict(row)
        for field in JSON_FIELDS:
            if field in device:
                device[field] = json.loads(device[field] or "null") or ([] if field == "last_result" else {})
        device["enabled"] = bool(device["enabled"])
        return device

    @staticmethod
    def _encode(fields: dict) -> dict:
        return {
            k: (json.dumps(v) if k in JSON_FIELDS else v)
            for k, v in fields.items()
        }

    def list_devices(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
        return [self._decode(r) for r in rows]

    def get_device(self, device_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return self._decode(row)

    def create_device(self, **fields) -> dict:
        now = utcnow()
        fields = self._encode(fields)
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"INSERT INTO devices ({cols}) VALUES ({marks})", tuple(fields.values())
            )
        return self.get_device(cur.lastrowid)

    def update_device(self, device_id: int, **fields) -> dict | None:
        if fields:
            fields = self._encode(fields)
            fields["updated_at"] = utcnow()
            assignments = ", ".join(f"{k} = ?" for k in fields)
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE devices SET {assignments} WHERE id = ?",
                    (*fields.values(), device_id),
                )
        return self.get_device(device_id)

    def delete_device(self, device_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

    # -------------------------------------------------------------- events

    def add_event(self, device_id: int, kind: str, message: str,
                  level: str = "info", detail: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (device_id, ts, kind, level, message, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, utcnow(), kind, level, message, detail),
            )
            self._conn.execute(
                "DELETE FROM events WHERE device_id = ? AND id NOT IN "
                "(SELECT id FROM events WHERE device_id = ? ORDER BY id DESC LIMIT ?)",
                (device_id, device_id, EVENTS_KEPT_PER_DEVICE),
            )

    def list_events(self, device_id: int, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
