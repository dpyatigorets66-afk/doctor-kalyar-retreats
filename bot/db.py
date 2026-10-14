"""SQLite-хранилище. Все отметки времени — UTC в ISO-формате, day — дата по Бангкоку."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Подписчики бота: только те, кто сам нажал /start
CREATE TABLE IF NOT EXISTS subscribers (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    started_at      TEXT NOT NULL,
    last_start_at   TEXT NOT NULL,
    unsubscribed_at TEXT,
    blocked_at      TEXT
);

-- Стоп-лист: навсегда, первым не пишем
CREATE TABLE IF NOT EXISTS stop_list (
    recipient  TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    reason     TEXT,
    ts         TEXT NOT NULL,
    day        TEXT NOT NULL,
    apologized INTEGER NOT NULL DEFAULT 0
);

-- Всё, что реально отправлено (или сделано: вступление в группу)
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    day        TEXT NOT NULL,
    channel    TEXT NOT NULL,
    recipient  TEXT,
    group_id   TEXT,
    campaign   TEXT,
    text       TEXT,
    replied_at TEXT
);
CREATE INDEX IF NOT EXISTS outbox_channel_day ON outbox (channel, day);
CREATE INDEX IF NOT EXISTS outbox_recipient ON outbox (recipient, channel);

-- Каждое решение модуля safety
CREATE TABLE IF NOT EXISTS safety_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    day       TEXT NOT NULL,
    channel   TEXT NOT NULL,
    recipient TEXT,
    allowed   INTEGER NOT NULL,
    reason    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS safety_log_day ON safety_log (day);

-- Последнее входящее сообщение от человека
CREATE TABLE IF NOT EXISTS inbound (
    recipient TEXT PRIMARY KEY,
    ts        TEXT NOT NULL
);

-- События для сводки: reply, stop, unsubscribe, blocked, flood, spambot, group_penalty
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    day       TEXT NOT NULL,
    kind      TEXT NOT NULL,
    recipient TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS events_kind_day ON events (kind, day);

CREATE TABLE IF NOT EXISTS group_blacklist (
    group_id TEXT PRIMARY KEY,
    reason   TEXT,
    ts       TEXT NOT NULL
);

-- Уведомления админу; бот отправляет их фоновой задачей
CREATE TABLE IF NOT EXISTS admin_alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    text    TEXT NOT NULL,
    sent_at TEXT
);
"""


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else row[0]

    def get_state(self, key: str) -> str | None:
        return self.scalar("SELECT value FROM state WHERE key = ?", (key,))

    def set_state(self, key: str, value: str, now_iso: str) -> None:
        self.execute(
            "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now_iso),
        )

    def delete_state(self, key: str) -> None:
        self.execute("DELETE FROM state WHERE key = ?", (key,))
