# 多用户平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 WxClawBotPush 从单用户单 bot 升级为多用户多微信机器人平台，含 Webhook 鉴权、消息模板、管理后台。

**Architecture:** SQLite 持久化替代 JSON 文件，每用户独立 ILinkClient 实例池 + 独立轮询线程。FastAPI 路由增加 token/session 双层鉴权。模板引擎递归解析 `{{ field }}` 占位符。

**Tech Stack:** Python 3.10+, FastAPI, SQLite3 (stdlib), bcrypt, httpx, pycryptodome

---

### Task 1: 数据库层 — database.py

**Files:**
- Create: `wxclawbotpush/database.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 添加 bcrypt 依赖**

```bash
echo "bcrypt>=4.0.0" >> requirements.txt
```

- [ ] **Step 2: 编写 database.py**

```python
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
    global _connection
    if _connection is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


def init_db():
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
    global _connection
    if _connection:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None
```

- [ ] **Step 3: 验证 database.py 能导入且 init_db 不报错**

```bash
cd d:/WxClawBotPush && python -c "from wxclawbotpush.database import init_db, get_db; init_db(); print('OK'); print(get_db().execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall())"
```

Expected: OK + 列出 4 张表

- [ ] **Step 4: Commit**

```bash
git add wxclawbotpush/database.py requirements.txt
git commit -m "feat: add SQLite database layer with schema"
```

---

### Task 2: 消息模板引擎 — template.py

**Files:**
- Create: `wxclawbotpush/template.py`

- [ ] **Step 1: 编写 template.py**

```python
import re
from typing import Any, Dict

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\s*\}\}")


def render_template(template_str: str, data: Dict[str, Any]) -> str:
    """将 {{ field.sub }} 占位符替换为 data 中对应的值。缺失字段替换为空字符串。"""
    if not template_str:
        return ""

    def replace_match(match: re.Match) -> str:
        path = match.group(1).strip()
        value = _resolve_path(data, path.split("."))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            import json
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _TOKEN_RE.sub(replace_match, template_str)


def _resolve_path(obj: Any, keys: list) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            try:
                idx = int(key)
                obj = obj[idx] if 0 <= idx < len(obj) else None
            except ValueError:
                return None
        else:
            return None
        if obj is None:
            return None
    return obj
```

- [ ] **Step 2: 验证模板引擎**

```bash
cd d:/WxClawBotPush && python -c "
from wxclawbotpush.template import render_template

# 基本替换
assert render_template('{{ title }}\n{{ text }}', {'title': 'hello', 'text': 'world'}) == 'hello\nworld'

# 嵌套字段
assert render_template('{{ alert.name }}: {{ alert.severity }}', {'alert': {'name': 'CPU', 'severity': 'high'}}) == 'CPU: high'

# 缺失字段
assert render_template('{{ xx }}{{ yy }}', {}) == ''

# 嵌套列表
assert render_template('{{ items.0.name }}', {'items': [{'name': 'a'}]}) == 'a'

# 空模板
assert render_template('', {}) == ''

print('All tests passed')
"
```

Expected: `All tests passed`

- [ ] **Step 3: Commit**

```bash
git add wxclawbotpush/template.py
git commit -m "feat: add message template engine"
```

---

### Task 3: 认证模块 — auth.py

**Files:**
- Create: `wxclawbotpush/auth.py`
- Modify: `wxclawbotpush/database.py`

- [ ] **Step 1: 编写 auth.py**

```python
import hashlib
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
    import secrets as _secrets
    db = get_db()
    token = _secrets.token_hex(16)
    db.execute(
        "INSERT OR IGNORE INTO user_configs (user_id, webhook_token) VALUES (?, ?)",
        (user_id, token),
    )
    db.commit()
```

- [ ] **Step 2: 验证 auth 模块**

```bash
cd d:/WxClawBotPush && python -c "
from wxclawbotpush.database import init_db
init_db()
from wxclawbotpush.auth import create_user, authenticate, create_admin, hash_password, verify_password

# 测试密码哈希
h = hash_password('test123')
assert verify_password('test123', h)
assert not verify_password('wrong', h)

# 测试创建用户
uid = create_user('testuser', 'test123')
assert uid is not None

# 测试登录
assert authenticate('testuser', 'test123') == uid
assert authenticate('testuser', 'wrong') is None

# 测试管理员创建
aid = create_admin('admin', 'admin123')
assert aid is not None

# 重复创建管理员不重复
aid2 = create_admin('admin2', 'admin456')
assert aid2 == aid

print('All tests passed')
"
```

Expected: `All tests passed`

- [ ] **Step 3: Commit**

```bash
git add wxclawbotpush/auth.py wxclawbotpush/database.py
git commit -m "feat: add auth module with bcrypt password hashing and session management"
```

---

### Task 4: 配置模块重写 — config.py

**Files:**
- Rewrite: `wxclawbotpush/config.py`

- [ ] **Step 1: 重写 config.py 为数据库驱动**

```python
import json
import os
from typing import Any, Dict, Optional

from database import get_db

DEFAULT_BASE_URL = os.environ.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com")


def get_system_config(key: str, default: str = "") -> str:
    db = get_db()
    row = db.execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_system_config(key: str, value: str):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def get_user_config(user_id: int) -> Dict[str, Any]:
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
    import secrets
    token = secrets.token_hex(16)
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO user_configs (user_id, webhook_token) VALUES (?, ?)",
        (user_id, token),
    )
    db.commit()
```

- [ ] **Step 2: 验证 config 模块**

```bash
cd d:/WxClawBotPush && python -c "
from wxclawbotpush.database import init_db
init_db()
from wxclawbotpush.config import get_system_config, set_system_config, get_user_config, save_user_config, init_user_config

# 系统配置
set_system_config('test_key', 'test_val')
assert get_system_config('test_key') == 'test_val'
assert get_system_config('nonexistent', 'default') == 'default'

# 用户配置（先创建一个用户）
from wxclawbotpush.database import get_db
db = get_db()
db.execute(\"INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'cfgtest', 'hash')\")
db.commit()
init_user_config(1)

cfg = get_user_config(1)
assert 'webhook_token' in cfg
assert isinstance(cfg.get('known_users'), list)

save_user_config(1, {'known_users': ['user_a', 'user_b'], 'message_template': '{{ msg }}'})
cfg2 = get_user_config(1)
assert cfg2['known_users'] == ['user_a', 'user_b']
assert cfg2['message_template'] == '{{ msg }}'

print('All tests passed')
"
```

Expected: `All tests passed`

- [ ] **Step 3: Commit**

```bash
git add wxclawbotpush/config.py
git commit -m "refactor: rewrite config module to use SQLite storage"
```

---

### Task 5: Client 多实例池 — client.py

**Files:**
- Rewrite: `wxclawbotpush/client.py`

- [ ] **Step 1: 重写 client.py 为多实例池**

```python
from typing import Any, Dict, Optional
import threading

from ilink.client import ILinkClient
from config import get_user_config

_clients: Dict[int, ILinkClient] = {}
_qr_code_data: Dict[int, Dict[str, Any]] = {}
_lock = threading.Lock()


def get_client(user_id: int) -> ILinkClient:
    with _lock:
        if user_id in _clients:
            return _clients[user_id]
        cfg = get_user_config(user_id)
        client = ILinkClient(
            base_url=cfg.get("base_url", "https://ilinkai.weixin.qq.com"),
            bot_token=cfg.get("bot_token"),
            account_id=cfg.get("account_id"),
            sync_buf=cfg.get("sync_buf"),
            timeout=20,
        )
        _clients[user_id] = client
        return client


def recreate_client(user_id: int, token=None, account_id=None, sync_buf=None):
    with _lock:
        existing = _clients.get(user_id)
        if existing:
            try:
                existing.close()
            except Exception:
                pass
        cfg = get_user_config(user_id)
        client = ILinkClient(
            base_url=cfg.get("base_url", "https://ilinkai.weixin.qq.com"),
            bot_token=token,
            account_id=account_id,
            sync_buf=sync_buf,
            timeout=20,
        )
        _clients[user_id] = client


def close_client(user_id: int):
    with _lock:
        client = _clients.pop(user_id, None)
        if client:
            try:
                client.close()
            except Exception:
                pass


def close_all():
    with _lock:
        for client in _clients.values():
            try:
                client.close()
            except Exception:
                pass
        _clients.clear()


def get_qr_code_data(user_id: int) -> Dict[str, Any]:
    if user_id not in _qr_code_data:
        _qr_code_data[user_id] = {}
    return _qr_code_data[user_id]


def clear_qr_code_data(user_id: int):
    d = _qr_code_data.get(user_id)
    if d is not None:
        d.clear()
```

- [ ] **Step 2: 验证 client 多实例隔离**

```bash
cd d:/WxClawBotPush && python -c "
from wxclawbotpush.database import init_db, get_db
init_db()
db = get_db()
db.execute(\"INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'h')\")
db.execute(\"INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (2, 'u2', 'h')\")
db.commit()

from wxclawbotpush.config import init_user_config, get_user_config, save_user_config
init_user_config(1)
init_user_config(2)
save_user_config(1, {'base_url': 'https://a.example.com'})
save_user_config(2, {'base_url': 'https://b.example.com'})

from wxclawbotpush.client import get_client, close_all
c1 = get_client(1)
c2 = get_client(2)
assert c1 is not c2
assert c1.base_url == 'https://a.example.com'
assert c2.base_url == 'https://b.example.com'

# 同一用户返回同一实例
c1b = get_client(1)
assert c1 is c1b

close_all()
print('All tests passed')
"
```

Expected: `All tests passed`

- [ ] **Step 3: Commit**

```bash
git add wxclawbotpush/client.py
git commit -m "refactor: rewrite client module as multi-instance pool per user"
```

---

### Task 6: 轮询模块改造 — polling.py

**Files:**
- Rewrite: `wxclawbotpush/polling.py`

- [ ] **Step 1: 重写 polling.py 为多用户架构**

```python
import logging
import threading
import time
from typing import Dict

from config import get_user_config, save_user_config
from client import get_client, recreate_client, get_qr_code_data
from ilink.client import ILinkClient

logger = logging.getLogger("wxclawbotpush")

_polling_threads: Dict[int, threading.Thread] = {}
_polling_flags: Dict[int, bool] = {}


def start_polling(user_id: int):
    if user_id in _polling_threads and _polling_threads[user_id].is_alive():
        return
    _polling_flags[user_id] = True
    t = threading.Thread(target=_poll_incoming_messages, args=(user_id,), daemon=True)
    _polling_threads[user_id] = t
    t.start()
    logger.info(f"后台消息轮询已启动: user_id={user_id}")


def stop_polling(user_id: int):
    _polling_flags[user_id] = False
    logger.info(f"后台消息轮询已停止: user_id={user_id}")


def stop_all_polling():
    for uid in list(_polling_flags.keys()):
        _polling_flags[uid] = False
    logger.info("所有轮询已停止")


def is_polling(user_id: int) -> bool:
    return _polling_flags.get(user_id, False)


def _poll_incoming_messages(user_id: int):
    while _polling_flags.get(user_id, False):
        try:
            client = get_client(user_id)
            cfg = get_user_config(user_id)
            if not cfg.get("bot_token"):
                time.sleep(5)
                continue

            messages, sync_buf, result = client.poll_updates(timeout_seconds=25)
            if sync_buf != cfg.get("sync_buf"):
                save_user_config(user_id, {"sync_buf": sync_buf})

            for msg in messages:
                user_cfg = get_user_config(user_id)
                known_users = list(user_cfg.get("known_users") or [])
                need_save = False

                if msg.user_id not in known_users:
                    known_users.append(msg.user_id)
                    logger.info(f"发现新用户: {msg.user_id} (user_id={user_id})")
                    need_save = True

                ctx_tokens = dict(user_cfg.get("context_tokens") or {})
                if msg.context_token:
                    ctx_tokens[msg.user_id] = msg.context_token
                    need_save = True

                if need_save:
                    save_user_config(user_id, {
                        "known_users": known_users,
                        "context_tokens": ctx_tokens,
                    })

        except Exception as e:
            logger.warning(f"消息轮询异常: user_id={user_id}, {e}")
            time.sleep(5)


def _poll_qr_code_status(user_id: int, qrcode_id: str):
    cfg = get_user_config(user_id)
    base_url = cfg.get("base_url", "https://ilinkai.weixin.qq.com")
    client = ILinkClient(base_url=base_url)
    max_wait = 240
    interval = 3

    qr_data = get_qr_code_data(user_id)
    for _ in range(max_wait // interval):
        time.sleep(interval)
        try:
            result = client.get_qrcode_status(str(qrcode_id))
            status = result.get("status", "").lower()
            qr_data["status"] = status

            if result.get("token"):
                token = result["token"]
                account_id = result.get("account_id")
                resolved_url = result.get("base_url") or base_url
                save_user_config(user_id, {
                    "bot_token": token,
                    "account_id": account_id,
                    "base_url": resolved_url,
                    "sync_buf": None,
                })
                recreate_client(user_id, token=token, account_id=account_id)
                qr_data["status"] = "connected"
                logger.info(f"扫码登录成功: user_id={user_id}, account_id={account_id}")
                client.close()
                start_polling(user_id)
                return

            if status in ("expired", "timeout", "canceled", "cancelled"):
                logger.warning(f"扫码状态: {status}, user_id={user_id}")
                client.close()
                return
        except Exception as e:
            logger.warning(f"检测扫码状态异常: {e}")

    qr_data["status"] = "timeout"
    client.close()
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/polling.py
git commit -m "refactor: rewrite polling module for per-user thread management"
```

---

### Task 7: Webhook 鉴权改造 — webhook.py

**Files:**
- Rewrite: `wxclawbotpush/webhook.py`

- [ ] **Step 1: 重写 webhook.py 加入 token 鉴权和模板渲染**

```python
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from config import get_user_config
from database import get_db
from template import render_template
from client import get_client

logger = logging.getLogger("wxclawbotpush")
router = APIRouter()


def _find_user_by_token(token: str) -> Optional[int]:
    db = get_db()
    row = db.execute("SELECT user_id FROM user_configs WHERE webhook_token = ?", (token,)).fetchone()
    return row["user_id"] if row else None


def _get_token_from_request(request: Request) -> Optional[str]:
    token = request.query_params.get("token")
    if token:
        return token
    return request.headers.get("X-Token") or request.headers.get("x-token")


def parse_webhook_payload(data: Dict[str, Any]) -> str:
    parts = []
    title = data.get("title") or data.get("subject") or data.get("summary")
    if title:
        parts.append(str(title))
    text = (
        data.get("text")
        or data.get("content")
        or data.get("message")
        or data.get("body")
        or data.get("description")
        or data.get("msg")
        or data.get("detail")
    )
    if text:
        if isinstance(text, (dict, list)):
            text = json.dumps(text, ensure_ascii=False, indent=2)
        parts.append(str(text))
    link = data.get("link") or data.get("url") or data.get("href")
    if link:
        parts.append(str(link))
    if not parts:
        parts.append(json.dumps(data, ensure_ascii=False, indent=2))
    return "\n".join(parts)


def _broadcast_to_users(user_id: int, message_text: str) -> dict:
    cfg = get_user_config(user_id)
    known_users = list(cfg.get("known_users") or [])
    if not known_users:
        raise HTTPException(status_code=503, detail="没有已知微信用户，请先向机器人发送一条消息")

    if not cfg.get("bot_token"):
        raise HTTPException(status_code=503, detail="微信未登录，请先扫码登录")

    client = get_client(user_id)
    results = {}
    for target_user in known_users:
        ctx_token = (cfg.get("context_tokens") or {}).get(target_user)
        ok = client.send_text(target_user, message_text, context_token=ctx_token)
        results[target_user] = "ok" if ok else "fail"
        logger.info(f"Webhook 转发: user_id={user_id}, target={target_user}, status={'ok' if ok else 'fail'}")
        _log_push(user_id, target_user, "ok" if ok else "fail")

    all_ok = all(v == "ok" for v in results.values())
    return {
        "success": all_ok,
        "results": results,
        "message_count": len(known_users),
    }


def _log_push(user_id: int, target_user: str, status: str):
    db = get_db()
    db.execute(
        "INSERT INTO push_logs (user_id, target_user, status) VALUES (?, ?, ?)",
        (user_id, target_user, status),
    )
    db.commit()


def _build_message_text(request: Request, body: Any) -> str:
    message_text = ""
    query_params = dict(request.query_params)

    if "msg" in query_params:
        message_text = query_params["msg"]
        extra = {k: v for k, v in query_params.items() if k != "msg" and k != "token"}
        if extra:
            message_text += "\n\n" + json.dumps(extra, ensure_ascii=False, indent=2)
        return message_text

    if body:
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    msg = parse_webhook_payload(item)
                    if msg:
                        message_text += ("\n---\n" if message_text else "") + msg
        elif isinstance(body, dict):
            msg = parse_webhook_payload(body)
            if msg:
                message_text = msg
        elif isinstance(body, str):
            message_text = body
    return message_text


@router.get("/webhook")
async def webhook_get_handler(request: Request):
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="缺少 token 鉴权参数")
    user_id = _find_user_by_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的 token")

    query_params = dict(request.query_params)
    message_text = query_params.pop("msg", "")
    extra = {k: v for k, v in query_params.items() if k != "token"}
    if extra:
        message_text += "\n\n" + json.dumps(extra, ensure_ascii=False, indent=2)
    if not message_text.strip():
        raise HTTPException(status_code=400, detail="缺少 msg 参数")

    cfg = get_user_config(user_id)
    template = cfg.get("message_template")
    if template:
        try:
            data = {"msg": message_text, "title": message_text, "text": message_text}
            message_text = render_template(template, data)
        except Exception:
            pass

    return _broadcast_to_users(user_id, message_text)


@router.post("/webhook")
async def webhook_post_handler(request: Request):
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="缺少 token 鉴权参数")
    user_id = _find_user_by_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的 token")

    try:
        body = await request.json()
    except Exception:
        body = None

    message_text = _build_message_text(request, body)

    if not message_text:
        raw = await request.body()
        if raw:
            try:
                message_text = raw.decode("utf-8")
            except Exception:
                message_text = raw.decode("latin-1")

    if not message_text:
        raise HTTPException(status_code=400, detail="消息内容为空")

    cfg = get_user_config(user_id)
    template = cfg.get("message_template")
    if template and isinstance(body, dict):
        try:
            message_text = render_template(template, body)
        except Exception:
            pass

    return _broadcast_to_users(user_id, message_text)
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/webhook.py
git commit -m "feat: add webhook token auth and template rendering"
```

---

### Task 8: Admin 路由扩展 — admin.py

**Files:**
- Rewrite: `wxclawbotpush/admin.py`

- [ ] **Step 1: 重写 admin.py 包含管理员和用户路由**

```python
import logging
import time
import threading
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from config import get_system_config, set_system_config, get_user_config, save_user_config, init_user_config
from database import get_db
from auth import create_user, authenticate, create_session, get_session_user, delete_session, get_user, is_admin
from client import get_client, close_client, get_qr_code_data, clear_qr_code_data
from log_utils import log_buffer, log_buffer_lock
from ilink.client import ILinkClient
from polling import start_polling, stop_polling, _poll_qr_code_status, is_polling

logger = logging.getLogger("wxclawbotpush")
router = APIRouter()


# ── 辅助函数 ──────────────────────────────────────────────

def _require_session(request: Request) -> int:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    user_id = get_session_user(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="登录已过期")
    return user_id


def _require_admin(request: Request) -> int:
    user_id = _require_session(request)
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user_id


# ── 认证路由 ───────────────────────────────────────────────

@router.post("/api/login")
def api_login(body: dict):
    username = (body or {}).get("username", "")
    password = (body or {}).get("password", "")
    user_id = authenticate(username, password)
    if not user_id:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user = get_user(user_id)
    if user and user.get("is_disabled"):
        raise HTTPException(status_code=403, detail="账号已禁用")

    token = create_session(user_id)
    return {"token": token, "user_id": user_id, "is_admin": user["is_admin"] if user else False}


@router.post("/api/register")
def api_register(body: dict):
    if get_system_config("registration_open", "1") != "1":
        raise HTTPException(status_code=403, detail="注册功能暂未开放")
    username = (body or {}).get("username", "").strip()
    password = (body or {}).get("password", "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if len(username) < 3 or len(password) < 6:
        raise HTTPException(status_code=400, detail="用户名至少3位，密码至少6位")

    user_id = create_user(username, password)
    if not user_id:
        raise HTTPException(status_code=409, detail="用户名已存在")
    token = create_session(user_id)
    return {"token": token, "user_id": user_id, "is_admin": False}


@router.post("/api/logout")
def api_logout(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        token = request.cookies.get("session")
    if token:
        delete_session(token)
    return {"success": True}


# ── 管理员路由 ──────────────────────────────────────────────

@router.get("/api/admin/users")
def admin_list_users(request: Request):
    _require_admin(request)
    db = get_db()
    rows = db.execute(
        "SELECT u.id, u.username, u.is_admin, u.is_disabled, u.created_at, "
        "uc.bot_token IS NOT NULL AS is_connected "
        "FROM users u LEFT JOIN user_configs uc ON u.id = uc.user_id ORDER BY u.id"
    ).fetchall()
    return {"users": [dict(r) for r in rows]}


@router.post("/api/admin/users")
def admin_create_user(request: Request, body: dict):
    _require_admin(request)
    username = (body or {}).get("username", "").strip()
    password = (body or {}).get("password", "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    user_id = create_user(username, password, is_admin=body.get("is_admin", False))
    if not user_id:
        raise HTTPException(status_code=409, detail="用户名已存在")
    return {"user_id": user_id}


@router.put("/api/admin/users/{user_id}")
def admin_update_user(request: Request, user_id: int, body: dict):
    _require_admin(request)
    db = get_db()
    if "is_disabled" in body:
        db.execute("UPDATE users SET is_disabled = ? WHERE id = ?", (int(body["is_disabled"]), user_id))
    if "is_admin" in body:
        db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(body["is_admin"]), user_id))
    if "password" in body:
        from auth import hash_password
        pw_hash = hash_password(body["password"])
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    db.commit()
    return {"success": True}


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(request: Request, user_id: int):
    _require_admin(request)
    from client import close_client
    from polling import stop_polling
    stop_polling(user_id)
    close_client(user_id)
    db = get_db()
    db.execute("DELETE FROM push_logs WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM user_configs WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return {"success": True}


@router.get("/api/admin/config")
def admin_get_system_config(request: Request):
    _require_admin(request)
    return {
        "registration_open": get_system_config("registration_open", "1"),
    }


@router.put("/api/admin/config")
def admin_set_system_config(request: Request, body: dict):
    _require_admin(request)
    for key, value in body.items():
        set_system_config(key, str(value))
    return {"success": True}


@router.get("/api/admin/stats")
def admin_get_stats(request: Request):
    _require_admin(request)
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    online_users = sum(1 for uid in range(1, total_users + 100) if is_polling(uid))
    total_messages = db.execute("SELECT COUNT(*) AS c FROM push_logs").fetchone()["c"]
    today_messages = db.execute(
        "SELECT COUNT(*) AS c FROM push_logs WHERE date(created_at) = date('now')"
    ).fetchone()["c"]
    return {
        "total_users": total_users,
        "online_users": online_users,
        "total_messages": total_messages,
        "today_messages": today_messages,
    }


# ── 用户设置路由 ────────────────────────────────────────────

@router.get("/api/user/config")
def user_get_config(request: Request):
    user_id = _require_session(request)
    cfg = get_user_config(user_id)
    return {
        "base_url": cfg.get("base_url", "https://ilinkai.weixin.qq.com"),
        "bot_token": bool(cfg.get("bot_token")),
        "account_id": cfg.get("account_id"),
        "webhook_token": cfg.get("webhook_token"),
        "message_template": cfg.get("message_template", ""),
        "known_users_count": len(cfg.get("known_users") or []),
        "is_connected": bool(cfg.get("bot_token") and is_polling(user_id)),
    }


@router.put("/api/user/config")
async def user_update_config(request: Request):
    user_id = _require_session(request)
    body = await request.json()
    allowed = {"message_template", "base_url"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if updates:
        save_user_config(user_id, updates)
    return {"success": True}


@router.get("/api/user/qrcode")
def user_get_qrcode(request: Request):
    user_id = _require_session(request)
    cfg = get_user_config(user_id)
    base_url = cfg.get("base_url", "https://ilinkai.weixin.qq.com")
    client = ILinkClient(base_url=base_url)
    result = client.get_qrcode()
    if result.get("success"):
        qr_data = get_qr_code_data(user_id)
        qr_data.clear()
        qr_data.update({
            "qrcode": result.get("qrcode"),
            "qrcode_url": result.get("qrcode_url"),
            "status": "waiting",
            "updated_at": int(time.time()),
        })
        threading.Thread(
            target=_poll_qr_code_status,
            args=(user_id, result.get("qrcode")),
            daemon=True,
        ).start()
    client.close()
    return result


@router.get("/api/user/qrcode/image")
def user_get_qrcode_image(request: Request):
    user_id = _require_session(request)
    qr_data = get_qr_code_data(user_id)
    qr_url = qr_data.get("qrcode_url") if qr_data else None
    if not qr_url:
        return PlainTextResponse("二维码未就绪", status_code=404)

    try:
        sources = [
            f"https://api.qrserver.com/v1/create-qr-code/?size=320x320&format=png&data={quote_plus(qr_url)}",
            f"https://quickchart.io/qr?size=320&margin=1&text={quote_plus(qr_url)}",
        ]
        for src in sources:
            with httpx.Client(timeout=httpx.Timeout(15)) as hc:
                resp = hc.get(src)
                if resp.status_code == 200 and resp.content:
                    return Response(
                        content=resp.content,
                        media_type="image/png",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
                    )
    except Exception as e:
        logger.warning(f"生成二维码图片失败: {e}")

    return PlainTextResponse("二维码图片生成失败", status_code=502)


@router.get("/api/user/status")
def user_get_status(request: Request):
    user_id = _require_session(request)
    cfg = get_user_config(user_id)
    qr_data = get_qr_code_data(user_id)
    return {
        "connected": bool(cfg.get("bot_token") and is_polling(user_id)),
        "account_id": cfg.get("account_id"),
        "known_users": len(cfg.get("known_users") or []),
        "qrcode": qr_data.get("qrcode"),
        "qrcode_status": qr_data.get("status", "none"),
        "qrcode_url": qr_data.get("qrcode_url"),
        "webhook_token": cfg.get("webhook_token"),
    }


@router.post("/api/user/logout")
def user_logout(request: Request):
    user_id = _require_session(request)
    stop_polling(user_id)
    close_client(user_id)
    clear_qr_code_data(user_id)
    save_user_config(user_id, {"bot_token": None, "account_id": None, "sync_buf": None})
    logger.info(f"已退出登录: user_id={user_id}")
    return {"success": True}


@router.get("/api/user/logs")
def user_get_logs(request: Request, limit: int = 200):
    user_id = _require_session(request)
    limit = max(1, min(limit, 1000))
    db = get_db()
    rows = db.execute(
        "SELECT target_user, status, created_at FROM push_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return {"count": len(rows), "logs": [dict(r) for r in rows]}


@router.post("/api/user/token")
def user_reset_token(request: Request):
    import secrets
    user_id = _require_session(request)
    new_token = secrets.token_hex(16)
    save_user_config(user_id, {"webhook_token": new_token})
    return {"webhook_token": new_token}


# ── 日志路由 ────────────────────────────────────────────────

@router.get("/api/logs")
def get_logs(request: Request, limit: int = 200):
    _require_admin(request)
    limit = max(1, min(limit, 1000))
    with log_buffer_lock:
        logs = list(log_buffer)[-limit:]
    return {"count": len(logs), "logs": logs}


@router.post("/api/logs/clear")
def clear_logs(request: Request):
    _require_admin(request)
    with log_buffer_lock:
        log_buffer.clear()
    return {"success": True}


# ── 页面路由 ────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    html_path = Path(__file__).parent / "templates" / "admin.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/admin.py
git commit -m "feat: rewrite admin routes with multi-user support"
```

---

### Task 9: app.py 改造 + 命令行管理工具

**Files:**
- Modify: `wxclawbotpush/app.py`
- Create: `wxclawbotpush/create_admin.py`
- Create: `wxclawbotpush/__main__.py`

- [ ] **Step 1: 重写 app.py**

```python
import os
from fastapi import FastAPI

from database import init_db, close_db
from auth import create_admin
from log_utils import setup_logging
from webhook import router as webhook_router
from admin import router as admin_router

logger = setup_logging()

app = FastAPI(title="WxClawbotPush", description="Multi-user Webhook to WeChat ClawBot", version="2.0.0")
app.include_router(webhook_router)
app.include_router(admin_router)


@app.on_event("startup")
def startup():
    init_db()

    admin_user = os.environ.get("ADMIN_USERNAME")
    admin_pass = os.environ.get("ADMIN_PASSWORD")
    if admin_user and admin_pass:
        uid = create_admin(admin_user, admin_pass)
        if uid:
            logger.info(f"管理员已就绪: user_id={uid}")

    logger.info("WxClawbotPush 启动完成")


@app.on_event("shutdown")
def shutdown():
    from polling import stop_all_polling
    from client import close_all
    stop_all_polling()
    close_all()
    close_db()
    logger.info("WxClawbotPush 已关闭")
```

- [ ] **Step 2: 编写 create_admin.py CLI**

```python
import sys
from database import init_db
from auth import create_admin


def main():
    init_db()
    if len(sys.argv) != 3:
        print("用法: python -m wxclawbotpush.create_admin <用户名> <密码>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    user_id = create_admin(username, password)
    if user_id:
        print(f"管理员已创建: user_id={user_id}")
    else:
        print("管理员创建失败（可能已存在）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 编写 __main__.py 用于 uvicorn 启动**

```python
import uvicorn


def main():
    uvicorn.run("app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 验证所有模块能导入且路由注册正常**

```bash
cd d:/WxClawBotPush && python -c "
import os
os.environ['DATA_DIR'] = 'd:/WxClawBotPush/data'
from wxclawbotpush.database import init_db
init_db()
from wxclawbotpush.auth import create_admin
create_admin('admin', 'admin123')
from wxclawbotpush.app import app
routes = [r.path for r in app.routes]
for path in ['/webhook', '/api/login', '/api/register', '/api/admin/users', '/api/user/config', '/api/user/qrcode', '/admin']:
    assert path in routes, f'{path} not found'
print('All routes registered OK')
"
```

Expected: `All routes registered OK`

- [ ] **Step 5: Commit**

```bash
git add wxclawbotpush/app.py wxclawbotpush/create_admin.py wxclawbotpush/__main__.py
git commit -m "feat: add multi-user app entrypoint and admin CLI"
```

---

### Task 10: HTML 模板 — login.html + admin.html 重写

**Files:**
- Create: `wxclawbotpush/templates/login.html`
- Rewrite: `wxclawbotpush/templates/admin.html`

- [ ] **Step 1: 编写 login.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WxClawBotPush - 登录</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.card { background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 32px; width: 360px; }
h1 { text-align: center; margin-bottom: 24px; font-size: 20px; color: #1a1a1a; }
label { display: block; margin-bottom: 4px; font-size: 14px; color: #555; }
input { width: 100%; padding: 8px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; margin-bottom: 16px; }
input:focus { outline: none; border-color: #07c160; box-shadow: 0 0 0 2px rgba(7,193,96,0.2); }
.btn { width: 100%; padding: 10px; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; }
.btn-primary { background: #07c160; color: #fff; }
.btn-primary:hover { background: #06ad56; }
.link { text-align: center; margin-top: 12px; font-size: 13px; color: #666; }
.link a { color: #07c160; text-decoration: none; }
.error { color: #e74c3c; font-size: 13px; margin-bottom: 12px; text-align: center; }
.tabs { display: flex; margin-bottom: 20px; border-bottom: 2px solid #eee; }
.tab { flex: 1; text-align: center; padding: 8px; cursor: pointer; font-size: 14px; color: #999; border-bottom: 2px solid transparent; margin-bottom: -2px; }
.tab.active { color: #07c160; border-bottom-color: #07c160; }
.hidden { display: none; }
</style>
</head>
<body>
<div class="card">
  <h1>WxClawBotPush</h1>
  <div class="tabs">
    <div class="tab active" data-tab="login">登录</div>
    <div class="tab" data-tab="register">注册</div>
  </div>
  <div id="error" class="error hidden"></div>
  <form id="loginForm">
    <label>用户名</label>
    <input type="text" id="username" autocomplete="username" required minlength="3">
    <label>密码</label>
    <input type="password" id="password" autocomplete="current-password" required minlength="6">
    <button type="submit" class="btn btn-primary">登录</button>
  </form>
  <form id="registerForm" class="hidden">
    <label>用户名</label>
    <input type="text" id="regUsername" required minlength="3">
    <label>密码</label>
    <input type="password" id="regPassword" required minlength="6">
    <button type="submit" class="btn btn-primary">注册</button>
  </form>
</div>
<script>
const tabs = document.querySelectorAll('.tab');
tabs.forEach(t => t.addEventListener('click', () => {
  tabs.forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  const isLogin = t.dataset.tab === 'login';
  document.getElementById('loginForm').classList.toggle('hidden', !isLogin);
  document.getElementById('registerForm').classList.toggle('hidden', isLogin);
  document.getElementById('error').classList.add('hidden');
}));

document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('username').value;
  const password = document.getElementById('password').value;
  const resp = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}) });
  const data = await resp.json();
  if (resp.ok) {
    localStorage.setItem('token', data.token);
    if (data.is_admin) window.location.href = '/admin';
    else window.location.href = '/admin';
  } else {
    const err = document.getElementById('error');
    err.textContent = data.detail || '登录失败';
    err.classList.remove('hidden');
  }
});

document.getElementById('registerForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('regUsername').value;
  const password = document.getElementById('regPassword').value;
  const resp = await fetch('/api/register', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}) });
  const data = await resp.json();
  if (resp.ok) {
    localStorage.setItem('token', data.token);
    window.location.href = '/admin';
  } else {
    const err = document.getElementById('error');
    err.textContent = data.detail || '注册失败';
    err.classList.remove('hidden');
  }
});
</script>
</body>
</html>
```

- [ ] **Step 2: 编写 admin.html（简化初始版本，后续迭代扩展）**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WxClawBotPush - 管理面板</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; }
.header { background: #fff; border-bottom: 1px solid #e8e8e8; padding: 0 24px; height: 56px; display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 18px; color: #1a1a1a; }
.header .right { display: flex; align-items: center; gap: 12px; }
.header .right span { font-size: 14px; color: #666; }
.header button { background: none; border: 1px solid #d9d9d9; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; }
.container { max-width: 900px; margin: 24px auto; padding: 0 16px; }
.card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 24px; margin-bottom: 16px; }
.card h2 { font-size: 16px; margin-bottom: 16px; color: #333; }
.form-row { display: flex; gap: 12px; margin-bottom: 12px; }
.form-row input { flex: 1; padding: 8px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; }
.form-row input:focus { outline: none; border-color: #07c160; }
.btn { padding: 8px 20px; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; }
.btn-green { background: #07c160; color: #fff; }
.btn-green:hover { background: #06ad56; }
.btn-red { background: #e74c3c; color: #fff; }
.btn-red:hover { background: #c0392b; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
th { color: #999; font-weight: 500; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge-on { background: #e6f9f0; color: #07c160; }
.badge-off { background: #f5f5f5; color: #999; }
.hidden { display: none; }
textarea { width: 100%; padding: 8px 12px; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 14px; font-family: monospace; resize: vertical; min-height: 80px; }
textarea:focus { outline: none; border-color: #07c160; }
.tokencopy { font-family: monospace; font-size: 13px; word-break: break-all; background: #f5f5f5; padding: 8px 12px; border-radius: 4px; margin: 8px 0; }
.code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 13px; }
</style>
</head>
<body>
<div class="header">
  <h1>WxClawBotPush</h1>
  <div class="right">
    <span id="headerUser"></span>
    <button onclick="logout()">退出</button>
  </div>
</div>
<div class="container" id="app"></div>
<script>
const token = localStorage.getItem('token');
if (!token) window.location.href = '/login.html';
const api = (url, opts = {}) => fetch(url, { ...opts, headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, ...(opts.headers || {}) } }).then(r => r.json());

async function load() {
  const resp = await api('/api/user/config');
  if (resp.detail && resp.detail.includes('登录')) { window.location.href = '/login.html'; return; }

  const statusResp = await api('/api/user/status');
  const statsResp = await api('/api/admin/stats');
  const isAdmin = statsResp.total_users !== undefined;

  document.getElementById('headerUser').textContent = '已连接用户';

  let html = '';

  if (isAdmin) {
    html += renderAdminPanel(resp, statusResp, statsResp);
  }

  html += renderUserPanel(resp, statusResp);

  document.getElementById('app').innerHTML = html;
  if (isAdmin) loadUsers();
}

function renderAdminPanel(cfg, status, stats) {
  return `<div class="card">
    <h2>系统看板</h2>
    <table><tr><td>总用户</td><td>${stats.total_users}</td><td>今日消息</td><td>${stats.today_messages}</td><td>总消息</td><td>${stats.total_messages}</td></tr></table>
    <div style="margin-top:16px">
      <input type="checkbox" id="regToggle" onchange="toggleReg(this.checked)">
      <label for="regToggle" style="font-size:14px">开放注册</label>
    </div>
    <h2 style="margin-top:20px">用户管理</h2>
    <div id="userList"></div>
    <div class="form-row" style="margin-top:12px">
      <input type="text" id="newUsername" placeholder="用户名">
      <input type="password" id="newPassword" placeholder="密码">
      <button class="btn btn-green" onclick="createUser()">创建用户</button>
    </div>
  </div>`;
}

function renderUserPanel(cfg, status) {
  return `<div class="card">
    <h2>个人设置</h2>
    ${status.connected
      ? `<p style="color:#07c160;font-size:14px">微信已连接 (${status.account_id || '未知账号'})</p>
         <p style="font-size:13px;color:#666;margin-top:4px">已知联系人: ${status.known_users} 个</p>`
      : `<p style="color:#999;font-size:14px">微信未连接</p>
         <button class="btn btn-green" onclick="getQrCode()">扫码登录</button>`
    }
    ${status.connected
      ? `<button class="btn btn-red" style="margin-left:8px" onclick="logoutBot()">断开微信</button>`
      : ''}
    <div id="qrcodeArea" style="margin-top:12px"></div>

    <h2 style="margin-top:24px">Webhook Token</h2>
    <div class="tokencopy">${cfg.webhook_token}</div>
    <button class="btn btn-green" onclick="resetToken()">重置 Token</button>
    <p style="font-size:12px;color:#999;margin-top:4px">推送地址: <span class="code">GET/POST /webhook?token=${cfg.webhook_token}</span></p>

    <h2 style="margin-top:24px">消息模板</h2>
    <textarea id="templateInput">${cfg.message_template || ''}</textarea>
    <button class="btn btn-green" style="margin-top:8px" onclick="saveTemplate()">保存模板</button>
    <p style="font-size:12px;color:#999;margin-top:4px">占位符: <span class="code">{{ field }}</span> 支持嵌套如 <span class="code">{{ alert.name }}</span></p>

    <h2 style="margin-top:24px">推送日志</h2>
    <div id="logArea"></div>
  </div>`;
}

async function loadUsers() {
  const resp = await api('/api/admin/users');
  const users = resp.users || [];
  document.getElementById('userList').innerHTML = `<table>
    <tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>微信</th><th>创建时间</th><th>操作</th></tr>
    ${users.map(u => `<tr>
      <td>${u.id}</td><td>${u.username}</td>
      <td>${u.is_admin ? '管理员' : '用户'}</td>
      <td><span class="badge ${u.is_disabled ? 'badge-off' : 'badge-on'}">${u.is_disabled ? '已禁用' : '正常'}</span></td>
      <td><span class="badge ${u.is_connected ? 'badge-on' : 'badge-off'}">${u.is_connected ? '已连接' : '未连接'}</span></td>
      <td>${u.created_at}</td>
      <td>
        <button onclick="toggleUser(${u.id}, ${!u.is_disabled})">${u.is_disabled ? '启用' : '禁用'}</button>
        <button onclick="deleteUser(${u.id})" style="color:#e74c3c;margin-left:4px">删除</button>
      </td>
    </tr>`).join('')}
  </table>`;
}

async function getQrCode() {
  try {
    const resp = await api('/api/user/qrcode');
    if (resp.qrcode_url) {
      document.getElementById('qrcodeArea').innerHTML = `<p style="font-size:13px;color:#666;margin-bottom:8px">请用微信扫描二维码</p><img src="/api/user/qrcode/image?t=${Date.now()}" style="width:280px;height:280px" onerror="setTimeout(getQrCode, 3000)">`;
    }
  } catch(e) {}
}

async function logoutBot() {
  await api('/api/user/logout', { method: 'POST' });
  location.reload();
}

async function resetToken() {
  const resp = await api('/api/user/token', { method: 'POST' });
  location.reload();
}

async function saveTemplate() {
  const tpl = document.getElementById('templateInput').value;
  await api('/api/user/config', { method: 'PUT', body: JSON.stringify({ message_template: tpl }) });
  alert('模板已保存');
}

async function toggleReg(v) {
  await api('/api/admin/config', { method: 'PUT', body: JSON.stringify({ registration_open: v ? '1' : '0' }) });
}

async function createUser() {
  const username = document.getElementById('newUsername').value;
  const password = document.getElementById('newPassword').value;
  if (!username || !password) { alert('请填写用户名和密码'); return; }
  await api('/api/admin/users', { method: 'POST', body: JSON.stringify({ username, password }) });
  loadUsers();
}

async function toggleUser(id, disabled) {
  await api(`/api/admin/users/${id}`, { method: 'PUT', body: JSON.stringify({ is_disabled: disabled ? 1 : 0 }) });
  loadUsers();
}

async function deleteUser(id) {
  if (!confirm('确定删除该用户？')) return;
  await api(`/api/admin/users/${id}`, { method: 'DELETE' });
  loadUsers();
}

async function loadLogs() {
  const resp = await api('/api/user/logs');
  const logs = resp.logs || [];
  document.getElementById('logArea').innerHTML = logs.slice(0, 20).map(l =>
    `<div style="font-size:12px;color:#666;padding:4px 0;border-bottom:1px solid #f5f5f5"><span class="badge ${l.status === 'ok' ? 'badge-on' : 'badge-off'}">${l.status}</span> ${l.target_user} <span style="float:right">${l.created_at}</span></div>`
  ).join('') || '<p style="font-size:13px;color:#999">暂无推送记录</p>';
}

function logout() {
  api('/api/logout', { method: 'POST' });
  localStorage.removeItem('token');
  window.location.href = '/login.html';
}

load();
setTimeout(() => { if (document.getElementById('logArea')) loadLogs(); }, 500);
</script>
</body>
</html>
```

- [ ] **Step 3: 更新 admin.py 中的登录页面路由，先重定向到 login.html**

在 `admin.py` 的页面路由中添加 login.html 路由：

```python
@router.get("/login.html", response_class=HTMLResponse)
def serve_login_page():
    html_path = Path(__file__).parent / "templates" / "login.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Commit**

```bash
git add wxclawbotpush/templates/login.html wxclawbotpush/templates/admin.html wxclawbotpush/admin.py
git commit -m "feat: add multi-user admin panel and login page"
```

---

### Task 11: 最终集成验证

- [ ] **Step 1: 启动服务验证**

```bash
cd d:/WxClawBotPush && DATA_DIR=d:/WxClawBotPush/data ADMIN_USERNAME=admin ADMIN_PASSWORD=admin123 python -m uvicorn wxclawbotpush.app:app --host 0.0.0.0 --port 8000 &
sleep 3
curl -s http://localhost:8000/api/login -X POST -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python -m json.tool
```

Expected: 返回 `token`, `user_id`, `is_admin: true`

- [ ] **Step 2: 验证注册接口**

```bash
curl -s http://localhost:8000/api/register -X POST -H "Content-Type: application/json" -d '{"username":"testuser","password":"test123456"}' | python -m json.tool
```

Expected: 返回 `token`, `user_id`, `is_admin: false`

- [ ] **Step 3: 验证 Webhook 鉴权**

```bash
# 无 token → 401
curl -s http://localhost:8000/webhook?msg=hello
# 无效 token → 401
curl -s http://localhost:8000/webhook?msg=hello\&token=badtoken
```

Expected: 两次都返回 401

- [ ] **Step 4: 验证管理员接口**

```bash
TOKEN=$(curl -s http://localhost:8000/api/login -X POST -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
# 用户列表
curl -s http://localhost:8000/api/admin/users -H "Authorization: Bearer $TOKEN" | python -m json.tool
# 统计
curl -s http://localhost:8000/api/admin/stats -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Expected: 用户列表显示 admin 和 testuser，统计显示 2 个用户

- [ ] **Step 5: 关闭服务并 Commit**

```bash
kill %1 2>/dev/null
git add -A
git commit -m "chore: final integration verification"
```

---

## 任务执行顺序

```
Task 1 (database.py) ──┬──→ Task 3 (auth.py) ──→ Task 4 (config.py) ──→ Task 5 (client.py)
                       │                                                         │
Task 2 (template.py) ──┘                                                         │
                                                                                 ↓
                                          Task 6 (polling.py) ──→ Task 7 (webhook.py) ──→ Task 8 (admin.py)
                                                                                                 │
                                                                                                 ↓
                                                                           Task 9 (app.py + CLI)
                                                                                 │
                                                                                 ↓
                                                                        Task 10 (HTML templates)
                                                                                 │
                                                                                 ↓
                                                                        Task 11 (验证)
```

- Task 1 和 Task 2 可并行
- Task 3→4→5 串行依赖
- Task 6 和 Task 7 可并行（都依赖 Task 5）
- Task 8 依赖 Task 6 和 Task 7
- Task 9 依赖 Task 8
- Task 10 依赖 Task 8
- Task 11 最后
