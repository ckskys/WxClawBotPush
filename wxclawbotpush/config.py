"""配置管理模块：系统配置和用户配置的持久化 CRUD。"""
import json
import os
from typing import Any, Dict, Optional

from database import get_db

DEFAULT_BASE_URL = os.environ.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")


def get_system_config(key: str, default: str = "") -> str:
    """读取系统配置项的值。"""
    db = get_db()
    row = db.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_system_config(key: str, value: str):
    """写入（插入或更新）系统配置项。"""
    db = get_db()
    db.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def get_user_config(user_id: int) -> Dict[str, Any]:
    """读取用户配置，JSON 字段自动反序列化。"""
    db = get_db()
    row = db.execute("SELECT * FROM user_configs WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return {}
    cfg = dict(row)
    for field in ("known_users", "context_tokens"):
        try:
            cfg[field] = json.loads(cfg.get(field) or "[]")
        except Exception:
            cfg[field] = [] if field == "known_users" else {}
    return cfg


def save_user_config(user_id: int, updates: Dict[str, Any]):
    """增量更新用户配置，JSON 字段自动序列化。"""
    db = get_db()
    row = db.execute("SELECT user_id FROM user_configs WHERE user_id = ?", (user_id,)).fetchone()
    json_fields = {"known_users", "context_tokens"}
    set_parts = []
    values = []
    for k, v in updates.items():
        if k in json_fields:
            v = json.dumps(v, ensure_ascii=False) if v is not None else ("[]" if k == "known_users" else "{}")
        set_parts.append(f"{k} = ?")
        values.append(v)
    values.append(user_id)
    if not set_parts:
        return
    db.execute(f"UPDATE user_configs SET {', '.join(set_parts)} WHERE user_id = ?", values)
    db.commit()


def init_user_config(user_id: int):
    """为新用户创建配置记录并生成 webhook_token。"""
    import secrets
    token = secrets.token_hex(16)
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO user_configs (user_id, webhook_token) VALUES (?, ?)",
        (user_id, token),
    )
    db.commit()
