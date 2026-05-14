"""数据库模块：SQLite 连接管理与 DDL 初始化。"""
import sqlite3
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "wxclawbotpush.db"

_connection: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    """获取全局 SQLite 连接（延迟创建），启用 WAL 模式和外键约束。"""
    global _connection
    if _connection is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


def init_db():
    """初始化数据库表结构（幂等：仅当表不存在时创建）。"""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT NOT NULL UNIQUE,
            password_hash   TEXT NOT NULL,
            is_admin        INTEGER DEFAULT 0,
            is_disabled     INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_configs (
            user_id           INTEGER PRIMARY KEY REFERENCES users(id),
            base_url          TEXT DEFAULT 'https://ilinkai.weixin.qq.com',
            bot_token         TEXT,
            account_id        TEXT,
            sync_buf          TEXT,
            known_users       TEXT DEFAULT '[]',
            context_tokens    TEXT DEFAULT '{}',
            webhook_token     TEXT UNIQUE,
            message_template  TEXT DEFAULT '{{ title }}\n{{ text }}\n{{ link }}',
            updated_at        TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS system_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS push_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER REFERENCES users(id),
            target_user TEXT,
            status      TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        INSERT OR IGNORE INTO system_config (key, value) VALUES ('registration_open', '1');
    """)
    db.commit()


def close_db():
    """关闭数据库连接。"""
    global _connection
    if _connection:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None
