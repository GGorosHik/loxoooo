"""SQLite: токены клиентов, состояние подключений, настройки бота."""
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_conn: Optional[sqlite3.Connection] = None
_lock = threading.RLock()

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS clients (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash      TEXT    NOT NULL UNIQUE,
        label           TEXT    NOT NULL DEFAULT '',
        installation_id TEXT,
        nickname        TEXT    NOT NULL DEFAULT '',
        server          TEXT    NOT NULL DEFAULT '',
        connected       INTEGER NOT NULL DEFAULT 0,
        connected_at    REAL,
        last_seen       REAL,
        created_at      REAL    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commands_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          REAL    NOT NULL,
        nickname    TEXT    NOT NULL DEFAULT '',
        server_name TEXT    NOT NULL DEFAULT '',
        server_ip   TEXT    NOT NULL DEFAULT '',
        command     TEXT    NOT NULL DEFAULT ''
    )
    """,
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Мягкая миграция: server_ip на старых базах добавляем справа."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(clients)")}
    if "server_ip" not in cols:
        conn.execute("ALTER TABLE clients ADD COLUMN server_ip TEXT NOT NULL DEFAULT ''")


def init(db_path: str) -> None:
    global _conn
    path = Path(db_path)
    if str(path.parent) not in ("", "."):
        os.makedirs(path.parent, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        for stmt in _SCHEMA:
            _conn.execute(stmt)
        _ensure_columns(_conn)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.commit()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def add_token(label: str) -> tuple[str, int]:
    """Создаёт токен, возвращает (токен_открытым_текстом, id_записи). В базе — только sha256."""
    token = "mc_" + secrets.token_urlsafe(24)
    thash = hash_token(token)
    now = time.time()
    with _lock:
        cur = _conn.execute(
            "INSERT INTO clients (token_hash, label, created_at) VALUES (?, ?, ?)",
            (thash, label, now),
        )
        _conn.commit()
        row_id = int(cur.lastrowid)  # type: ignore[arg-type]
    return token, row_id


def get_by_token_hash(thash: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM clients WHERE token_hash = ?", (thash,)).fetchone()
    return dict(row) if row else None


def get_by_id(client_id: int) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def get_by_nickname(nickname: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM clients WHERE LOWER(nickname) = LOWER(?) ORDER BY last_seen DESC LIMIT 1",
            (nickname,),
        ).fetchone()
    return dict(row) if row else None


def get_by_installation_id(iid: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _conn.execute("SELECT * FROM clients WHERE installation_id = ?", (iid,)).fetchone()
    return dict(row) if row else None


def list_clients() -> list[dict[str, Any]]:
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM clients ORDER BY connected DESC, last_seen DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_connected(token_hash: str, installation_id: str, nickname: str) -> None:
    now = time.time()
    with _lock:
        _conn.execute(
            """
            UPDATE clients
            SET installation_id = COALESCE(NULLIF(?, ''), installation_id),
                nickname        = CASE WHEN ? != '' THEN ? ELSE nickname END,
                connected       = 1,
                connected_at    = ?,
                last_seen       = ?
            WHERE token_hash = ?
            """,
            (installation_id, nickname, nickname, now, now, token_hash),
        )
        _conn.commit()


def mark_status(token_hash: str, nickname: str, server: str, server_ip: str = "") -> None:
    now = time.time()
    with _lock:
        _conn.execute(
            """
            UPDATE clients
            SET nickname  = CASE WHEN ? != '' THEN ? ELSE nickname END,
                server    = ?,
                server_ip = ?,
                last_seen = ?
            WHERE token_hash = ?
            """,
            (nickname, nickname, server, server_ip, now, token_hash),
        )
        _conn.commit()


def touch(token_hash: str) -> None:
    with _lock:
        _conn.execute("UPDATE clients SET last_seen = ? WHERE token_hash = ?", (time.time(), token_hash))
        _conn.commit()


def mark_disconnected(token_hash: str) -> None:
    now = time.time()
    with _lock:
        _conn.execute(
            "UPDATE clients SET connected = 0, server = '', last_seen = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        _conn.commit()


def delete_client(token_hash: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM clients WHERE token_hash = ?", (token_hash,))
        _conn.commit()
    return cur.rowcount > 0


def add_recorded_command(nickname: str, server_name: str, server_ip: str, command: str) -> None:
    """Каждая перехваченная /login //l //register //reg — в журнал с привязкой к серверу."""
    with _lock:
        _conn.execute(
            """
            INSERT INTO commands_log (ts, nickname, server_name, server_ip, command)
            VALUES (?, ?, ?, ?, ?)
            """,
            (time.time(), nickname, server_name, server_ip, command),
        )
        _conn.commit()


def list_recorded_commands(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        rows = _conn.execute(
            "SELECT ts, nickname, server_name, server_ip, command "
            "FROM commands_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_setting(key: str, value: str) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        _conn.commit()


def get_setting(key: str, default: str) -> str:
    with _lock:
        row = _conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
