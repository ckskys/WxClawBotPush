import secrets
import time
from typing import Dict, Optional

import bcrypt

from database import get_db

SESSION_TIMEOUT = 86400 * 7
_sessions: Dict[str, dict] = {}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_user(username: str, password: str, is_admin: bool = False) -> Optional[int]:
    db = get_db()
    pw_hash = hash_password(password)
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, pw_hash, int(is_admin)),
        )
        db.commit()
        user_id = cur.lastrowid
        _ensure_user_config(user_id)
        return user_id
    except Exception:
        return None


def create_admin(username: str, password: str) -> Optional[int]:
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
    if existing:
        return existing["id"]
    return create_user(username, password, is_admin=True)


def authenticate(username: str, password: str) -> Optional[int]:
    db = get_db()
    row = db.execute("SELECT id, password_hash, is_disabled FROM users WHERE username = ?", (username,)).fetchone()
    if not row or row["is_disabled"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row["id"]


def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {"user_id": user_id, "created_at": int(time.time())}
    return token


def get_session_user(token: str) -> Optional[int]:
    sess = _sessions.get(token)
    if not sess:
        return None
    if int(time.time()) - sess["created_at"] > SESSION_TIMEOUT:
        del _sessions[token]
        return None
    return sess["user_id"]


def delete_session(token: str):
    _sessions.pop(token, None)


def get_user(user_id: int) -> Optional[dict]:
    db = get_db()
    row = db.execute("SELECT id, username, is_admin, is_disabled, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def is_admin(user_id: int) -> bool:
    db = get_db()
    row = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["is_admin"])


def _ensure_user_config(user_id: int):
    db = get_db()
    token = secrets.token_hex(16)
    db.execute(
        "INSERT OR IGNORE INTO user_configs (user_id, webhook_token) VALUES (?, ?)",
        (user_id, token),
    )
    db.commit()
