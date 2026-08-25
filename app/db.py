"""SQLite persistence. One shared connection behind a lock — plenty at fleet scale."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

JSON_FIELDS = ("vars", "config", "identity", "action_overrides", "last_result")
RULE_JSON_FIELDS = ("target_packages", "patterns", "key_sequence")
EVENTS_KEPT_PER_DEVICE = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS channel_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    target_packages TEXT NOT NULL DEFAULT '["com.google.android.youtube.tvunplugged"]',
    patterns TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL DEFAULT 'auto_skip',
    key_sequence TEXT NOT NULL DEFAULT '["KEYCODE_CHANNEL_UP"]',
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guard_settings (
    device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    default_action TEXT NOT NULL DEFAULT 'auto_skip',
    poll_interval_s REAL NOT NULL DEFAULT 1.0,
    cooldown_s REAL NOT NULL DEFAULT 15.0,
    snooze_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, id DESC);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns and tables introduced after initial release."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(devices)")}
        if "location" not in cols:
            self._conn.execute("ALTER TABLE devices ADD COLUMN location TEXT NOT NULL DEFAULT ''")

        # Seed default channel rules if empty
        rule_count = self._conn.execute("SELECT COUNT(*) FROM channel_rules").fetchone()[0]
        if rule_count == 0:
            now = utcnow()
            self._conn.execute(
                """
                INSERT INTO channel_rules (name, enabled, target_packages, patterns, action, key_sequence, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Block Fox News",
                    1,
                    json.dumps(["com.google.android.youtube.tvunplugged"]),
                    json.dumps([r"fox\s*news", r"\bFNC\b"]),
                    "auto_skip",
                    json.dumps(["KEYCODE_CHANNEL_UP"]),
                    "Automatically skips Fox News live stream on YouTube TV",
                    now,
                    now,
                ),
            )

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
            device_id = cur.lastrowid
            # Create default guard settings
            self._conn.execute(
                "INSERT OR IGNORE INTO guard_settings (device_id, enabled, default_action, poll_interval_s, cooldown_s, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, 1, "auto_skip", 1.0, 15.0, now, now),
            )
        return self.get_device(device_id)

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

    # -------------------------------------------------------- channel rules

    @staticmethod
    def _decode_rule(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        rule = dict(row)
        for field in RULE_JSON_FIELDS:
            if field in rule:
                rule[field] = json.loads(rule[field] or "[]")
        rule["enabled"] = bool(rule["enabled"])
        return rule

    @staticmethod
    def _encode_rule(fields: dict) -> dict:
        return {
            k: (json.dumps(v) if k in RULE_JSON_FIELDS else v)
            for k, v in fields.items()
        }

    def list_channel_rules(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM channel_rules ORDER BY id ASC").fetchall()
        return [self._decode_rule(r) for r in rows]

    def get_channel_rule(self, rule_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM channel_rules WHERE id = ?", (rule_id,)).fetchone()
        return self._decode_rule(row)

    def create_channel_rule(self, **fields) -> dict:
        now = utcnow()
        fields = self._encode_rule(fields)
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"INSERT INTO channel_rules ({cols}) VALUES ({marks})", tuple(fields.values())
            )
        return self.get_channel_rule(cur.lastrowid)

    def update_channel_rule(self, rule_id: int, **fields) -> dict | None:
        if fields:
            fields = self._encode_rule(fields)
            fields["updated_at"] = utcnow()
            assignments = ", ".join(f"{k} = ?" for k in fields)
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE channel_rules SET {assignments} WHERE id = ?",
                    (*fields.values(), rule_id),
                )
        return self.get_channel_rule(rule_id)

    def delete_channel_rule(self, rule_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM channel_rules WHERE id = ?", (rule_id,))

    # ------------------------------------------------------- guard settings

    def get_guard_settings(self, device_id: int) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM guard_settings WHERE device_id = ?", (device_id,)).fetchone()
            if row is None:
                now = utcnow()
                self._conn.execute(
                    "INSERT INTO guard_settings (device_id, enabled, default_action, poll_interval_s, cooldown_s, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device_id, 1, "auto_skip", 1.0, 15.0, now, now),
                )
                row = self._conn.execute("SELECT * FROM guard_settings WHERE device_id = ?", (device_id,)).fetchone()
        res = dict(row)
        res["enabled"] = bool(res["enabled"])
        return res

    def update_guard_settings(self, device_id: int, **fields) -> dict:
        if fields:
            fields["updated_at"] = utcnow()
            assignments = ", ".join(f"{k} = ?" for k in fields)
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE guard_settings SET {assignments} WHERE device_id = ?",
                    (*fields.values(), device_id),
                )
        return self.get_guard_settings(device_id)
