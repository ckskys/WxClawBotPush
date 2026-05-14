# WxClawbotPush 项目重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `app/main.py` 按关注点拆分为独立模块，重命名包和符号，不改变功能行为。

**Architecture:** 使用 APIRouter 将路由分散到各模块，`client.py` 管理 ILinkClient 单例和共享全局状态，`polling.py` 管理所有后台线程。模块间通过绝对导入协作，无循环依赖。

**Tech Stack:** Python 3.11, FastAPI, httpx, pycryptodome

---

### File Structure Summary

```
wxclawbotpush/          ← 原 app/
├── app.py              ← FastAPI 实例 + startup
├── config.py           ← 配置读写
├── logging.py          ← 日志缓冲
├── client.py           ← ILinkClient 单例管理 + _qr_code_data
├── webhook.py          ← Webhook 路由
├── admin.py            ← 管理 API + 页面路由
├── polling.py          ← 消息轮询 + 扫码监听
├── ilink/
│   ├── __init__.py
│   ├── client.py       ← ILinkClient (重构内部命名)
│   └── models.py       ← IncomingMessage dataclass
└── templates/
    └── admin.html
```

---

### Task 1: Create logging module

**Files:**
- Create: `wxclawbotpush/logging.py`

- [ ] **Step 1: Extract logging code from main.py**

```python
import logging
import threading
from collections import deque
from datetime import datetime

log_buffer: deque = deque(maxlen=1000)
log_buffer_lock = threading.Lock()


class LogBufferHandler(logging.Handler):
    def emit(self, record):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        with log_buffer_lock:
            log_buffer.append(entry)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("wxclawbotpush")
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)

    buffer_handler = LogBufferHandler()
    buffer_handler.setLevel(logging.INFO)
    buffer_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(buffer_handler)

    return logger
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/logging.py
git commit -m "refactor: extract logging module from main.py"
```

---

### Task 2: Create config module

**Files:**
- Create: `wxclawbotpush/config.py`

- [ ] **Step 1: Extract config code from main.py**

```python
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_CONFIG = {
    "base_url": os.environ.get("ILINK_BASE_URL", "https://ilinkai.weixin.qq.com"),
    "bot_token": None,
    "account_id": None,
    "sync_buf": None,
    "known_users": [],
    "user_context_tokens": {},
}
_config: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    global _config
    cfg = {**DEFAULT_CONFIG}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            import logging
            logging.getLogger("wxclawbotpush").warning("读取配置文件失败，使用默认配置")
    _config = cfg
    return cfg


def save_config(updates: Dict[str, Any] = None) -> Dict[str, Any]:
    global _config
    cfg = load_config()
    if updates:
        cfg.update(updates)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _config = cfg
    return cfg


def get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        load_config()
    return _config
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/config.py
git commit -m "refactor: extract config module from main.py"
```

---

### Task 3: Create ilink models module

**Files:**
- Create: `wxclawbotpush/ilink/models.py`
- Modify: `wxclawbotpush/ilink/client.py` — remove `ILinkIncomingMessage` dataclass

- [ ] **Step 1: Write models.py with renamed dataclass**

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class IncomingMessage:
    user_id: str
    text: str
    username: Optional[str] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    context_token: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 2: Remove `ILinkIncomingMessage` from client.py and update import**

Remove lines 20-28 from `client.py` (the `ILinkIncomingMessage` dataclass definition).

Add import at top of `client.py`:
```python
from .models import IncomingMessage
```

Replace `ILinkIncomingMessage` with `IncomingMessage` in `poll_updates()` return type hint (line 373) and `_parse_incoming()` return type hint (line 422):

```python
# Line 373: change return type
def poll_updates(
    self, timeout_seconds: int = 25
) -> Tuple[List[IncomingMessage], Optional[str], Dict[str, Any]]:

# Line 422: change return type  
def _parse_incoming(self, item: Dict[str, Any]) -> Optional[IncomingMessage]:

# Line 479: change constructor call
return IncomingMessage(
    user_id=str(user_id),
    text=str(text),
    username=str(username) if username else None,
    raw=item,
)
```

- [ ] **Step 3: Update ilink __init__.py exports**

```python
from .client import ILinkClient
from .models import IncomingMessage

__all__ = ["ILinkClient", "IncomingMessage"]
```

- [ ] **Step 4: Commit**

```bash
git add wxclawbotpush/ilink/models.py wxclawbotpush/ilink/__init__.py wxclawbotpush/ilink/client.py
git commit -m "refactor: extract IncomingMessage model, rename from ILinkIncomingMessage"
```

---

### Task 4: Create client module (ILinkClient singleton)

**Files:**
- Create: `wxclawbotpush/client.py`

- [ ] **Step 1: Write client.py with singleton management**

```python
from typing import Any, Dict, Optional

from ilink.client import ILinkClient
from config import get_config

_client: Optional[ILinkClient] = None
_qr_code_data: Dict[str, Any] = {}


def get_client() -> ILinkClient:
    global _client
    cfg = get_config()
    if _client:
        return _client
    _client = ILinkClient(
        base_url=cfg.get("base_url", "https://ilinkai.weixin.qq.com"),
        bot_token=cfg.get("bot_token"),
        account_id=cfg.get("account_id"),
        sync_buf=cfg.get("sync_buf"),
        timeout=20,
    )
    return _client


def recreate_client(token=None, account_id=None, sync_buf=None):
    global _client
    if _client:
        try:
            _client.close()
        except Exception:
            pass
    _client = ILinkClient(
        base_url=get_config().get("base_url", "https://ilinkai.weixin.qq.com"),
        bot_token=token,
        account_id=account_id,
        sync_buf=sync_buf,
        timeout=20,
    )


def close_client():
    global _client
    if _client:
        try:
            _client.close()
        except Exception:
            pass
        _client = None
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/client.py
git commit -m "refactor: extract client singleton management from main.py"
```

---

### Task 5: Create polling module

**Files:**
- Create: `wxclawbotpush/polling.py`

- [ ] **Step 1: Write polling.py with renamed functions**

```python
import logging
import threading
import time
from typing import Optional

from config import get_config, save_config
from client import get_client, recreate_client, _qr_code_data
from ilink.client import ILinkClient

logger = logging.getLogger("wxclawbotpush")

_polling_active = False
_polling_thread: Optional[threading.Thread] = None


def start_polling():
    global _polling_active, _polling_thread
    if _polling_thread and _polling_thread.is_alive():
        return
    _polling_active = True
    _polling_thread = threading.Thread(target=_poll_incoming_messages, daemon=True)
    _polling_thread.start()
    logger.info("后台消息轮询已启动")


def stop_polling():
    global _polling_active
    _polling_active = False
    logger.info("后台消息轮询已停止")


def _poll_incoming_messages():
    global _polling_active

    while _polling_active:
        try:
            client = get_client()
            if not client.bot_token:
                time.sleep(5)
                continue

            messages, sync_buf, result = client.poll_updates(timeout_seconds=25)
            if sync_buf != get_config().get("sync_buf"):
                save_config({"sync_buf": sync_buf})

            for msg in messages:
                user_id = msg.user_id
                cfg = get_config()
                known_users = list(cfg.get("known_users") or [])
                need_save = False

                if user_id not in known_users:
                    known_users.append(user_id)
                    logger.info(f"发现新用户: {user_id} (username={msg.username})")
                    need_save = True

                ctx_tokens = dict(cfg.get("user_context_tokens") or {})
                if msg.context_token:
                    ctx_tokens[user_id] = msg.context_token
                    need_save = True

                if need_save:
                    save_config({
                        "known_users": known_users,
                        "user_context_tokens": ctx_tokens,
                    })

        except Exception as e:
            logger.warning(f"消息轮询异常: {e}")
            time.sleep(5)


def _poll_qr_code_status(qrcode_id: str):
    cfg = get_config()
    base_url = cfg.get("base_url", "https://ilinkai.weixin.qq.com")
    client = ILinkClient(base_url=base_url)
    max_wait = 240
    interval = 3

    global _qr_code_data
    for _ in range(max_wait // interval):
        time.sleep(interval)
        try:
            result = client.get_qrcode_status(str(qrcode_id))
            status = result.get("status", "").lower()
            _qr_code_data["status"] = status

            if result.get("token"):
                token = result["token"]
                account_id = result.get("account_id")
                resolved_url = result.get("base_url") or base_url
                save_config({
                    "bot_token": token,
                    "account_id": account_id,
                    "base_url": resolved_url,
                    "sync_buf": None,
                })
                recreate_client(token=token, account_id=account_id)
                _qr_code_data["status"] = "connected"
                logger.info(f"扫码登录成功: account_id={account_id}")
                client.close()
                start_polling()
                return

            if status in ("expired", "timeout", "canceled", "cancelled"):
                logger.warning(f"扫码状态: {status}")
                client.close()
                return
        except Exception as e:
            logger.warning(f"检测扫码状态异常: {e}")

    _qr_code_data["status"] = "timeout"
    client.close()
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/polling.py
git commit -m "refactor: extract polling and QR code login watcher from main.py"
```

---

### Task 6: Create webhook module

**Files:**
- Create: `wxclawbotpush/webhook.py`

- [ ] **Step 1: Write webhook.py with APIRouter and renamed functions**

```python
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from config import get_config
from client import get_client

logger = logging.getLogger("wxclawbotpush")
router = APIRouter()


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


def _broadcast_to_users(cfg: dict, message_text: str) -> dict:
    known_users = list(cfg.get("known_users") or [])
    if not known_users:
        raise HTTPException(status_code=503, detail="没有已知微信用户，请先向机器人发送一条消息")

    client = get_client()
    results = {}
    for user_id in known_users:
        ctx_token = (cfg.get("user_context_tokens") or {}).get(user_id)
        ok = client.send_text(user_id, message_text, context_token=ctx_token)
        results[user_id] = "ok" if ok else "fail"
        logger.info(f"Webhook 转发: user={user_id}, status={'ok' if ok else 'fail'}")

    all_ok = all(v == "ok" for v in results.values())
    return {
        "success": all_ok,
        "results": results,
        "message_count": len(known_users),
    }


@router.get("/webhook")
async def webhook_get_handler(request: Request):
    cfg = get_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=503, detail="微信未登录，请先扫码登录")

    query_params = dict(request.query_params)
    message_text = query_params.pop("msg", "")
    if query_params:
        message_text += "\n\n" + json.dumps(query_params, ensure_ascii=False, indent=2)
    if not message_text:
        raise HTTPException(status_code=400, detail="缺少 msg 参数")

    return _broadcast_to_users(cfg, message_text)


@router.post("/webhook")
async def webhook_post_handler(request: Request):
    cfg = get_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=503, detail="微信未登录，请先扫码登录")

    message_text = ""

    query_params = dict(request.query_params)
    if "msg" in query_params:
        message_text = query_params["msg"]
        extra = {k: v for k, v in query_params.items() if k != "msg"}
        if extra:
            message_text += "\n\n" + json.dumps(extra, ensure_ascii=False, indent=2)

    try:
        body = await request.json()
    except Exception:
        body = None

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
                message_text += ("\n---\n" if message_text else "") + msg
        elif isinstance(body, str):
            if body:
                message_text += ("\n" if message_text else "") + body

    if not message_text:
        raw = await request.body()
        if raw:
            try:
                message_text = raw.decode("utf-8")
            except Exception:
                message_text = raw.decode("latin-1")

    if not message_text:
        raise HTTPException(status_code=400, detail="消息内容为空")

    return _broadcast_to_users(cfg, message_text)
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/webhook.py
git commit -m "refactor: extract webhook module from main.py"
```

---

### Task 7: Create admin module

**Files:**
- Create: `wxclawbotpush/admin.py`

- [ ] **Step 1: Write admin.py with APIRouter and renamed functions**

```python
import logging
import time
import threading
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from config import get_config, save_config
from client import get_client, close_client, _qr_code_data
from logging import log_buffer, log_buffer_lock
from ilink.client import ILinkClient
from polling import stop_polling, _poll_qr_code_status

logger = logging.getLogger("wxclawbotpush")
router = APIRouter()


@router.get("/api/status")
def get_status():
    cfg = get_config()
    qr = _qr_code_data
    return {
        "connected": bool(cfg.get("bot_token")),
        "account_id": cfg.get("account_id"),
        "known_users": len(cfg.get("known_users") or []),
        "qrcode": qr.get("qrcode"),
        "qrcode_status": qr.get("status", "none"),
        "qrcode_url": qr.get("qrcode_url"),
    }


@router.get("/api/qrcode")
def get_qr_code():
    global _qr_code_data
    client = ILinkClient(base_url=get_config().get("base_url", "https://ilinkai.weixin.qq.com"))
    result = client.get_qrcode()
    if result.get("success"):
        _qr_code_data = {
            "qrcode": result.get("qrcode"),
            "qrcode_url": result.get("qrcode_url"),
            "status": "waiting",
            "updated_at": int(time.time()),
        }
        threading.Thread(
            target=_poll_qr_code_status,
            args=(result.get("qrcode"),),
            daemon=True,
        ).start()
    client.close()
    return result


@router.get("/api/qrcode/image")
def get_qr_code_image():
    qr = _qr_code_data
    qr_url = qr.get("qrcode_url") if qr else None
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


@router.post("/api/logout")
def logout():
    global _qr_code_data
    stop_polling()
    close_client()
    _qr_code_data = {}
    save_config({"bot_token": None, "account_id": None, "sync_buf": None})
    logger.info("已退出登录")
    return {"success": True}


@router.get("/api/logs")
def get_logs(limit: int = 200):
    limit = max(1, min(limit, 1000))
    with log_buffer_lock:
        logs = list(log_buffer)[-limit:]
    return {"count": len(logs), "logs": logs}


@router.post("/api/logs/clear")
def clear_logs():
    with log_buffer_lock:
        log_buffer.clear()
    return {"success": True}


@router.get("/", response_class=HTMLResponse)
@router.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    html_path = Path(__file__).parent / "templates" / "admin.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/admin.py
git commit -m "refactor: extract admin module from main.py"
```

---

### Task 8: Create app module (FastAPI entry point)

**Files:**
- Create: `wxclawbotpush/app.py`

- [ ] **Step 1: Write app.py**

```python
from fastapi import FastAPI

from logging import setup_logging
from config import load_config, get_config
from polling import start_polling
from webhook import router as webhook_router
from admin import router as admin_router

logger = setup_logging()

app = FastAPI(title="WxClawbotPush", description="Webhook to WeChat ClawBot", version="1.0.0")
app.include_router(webhook_router)
app.include_router(admin_router)


@app.on_event("startup")
def startup():
    load_config()
    if get_config().get("bot_token"):
        start_polling()
    logger.info("WxClawbotPush 启动完成")
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/app.py
git commit -m "refactor: create app module as FastAPI entry point"
```

---

### Task 9: Move admin.html to templates directory

**Files:**
- Move: `wxclawbotpush/admin.html` → `wxclawbotpush/templates/admin.html`

- [ ] **Step 1: Create templates directory and move file**

```bash
mkdir -p wxclawbotpush/templates
mv wxclawbotpush/admin.html wxclawbotpush/templates/admin.html
```

- [ ] **Step 2: Commit**

```bash
git add wxclawbotpush/templates/admin.html
git rm wxclawbotpush/admin.html
git commit -m "refactor: move admin.html to templates directory"
```

---

### Task 10: Update Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Update COPY path and uvicorn command**

```dockerfile
FROM python:3.11-slim

ENV TZ=Asia/Shanghai
ENV WEBHOOK_PORT=8099
ENV DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wxclawbotpush/ /app/

RUN mkdir -p /data

EXPOSE 8099

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${WEBHOOK_PORT}"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile
git commit -m "refactor: update Dockerfile for new package structure"
```

---

### Task 11: Create pyproject.toml and .gitignore

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Delete: `DOCKER_HUB.md`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "wxclawbotpush"
version = "1.0.0"
description = "Webhook to WeChat ClawBot message push service"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0",
    "httpx>=0.24.0",
    "pycryptodome>=3.19.0",
]
```

- [ ] **Step 2: Create .gitignore**

```
data/
__pycache__/
*.pyc
.env
```

- [ ] **Step 3: Delete old files**

```bash
rm wxclawbotpush/main.py   # if it still exists after all extractions
rm DOCKER_HUB.md
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore
git rm DOCKER_HUB.md
git commit -m "chore: add pyproject.toml, .gitignore, remove stale files"
```

---

### Task 12: Remove old app/ directory and rename wxclawbotpush/

**Files:**
- Delete: `app/` directory (if exists alongside `wxclawbotpush/`)
- Verify: All code now lives in `wxclawbotpush/`

- [ ] **Step 1: Clean up old app directory**

```bash
# Confirm wxclawbotpush/ has all files
ls -la wxclawbotpush/

# Remove old app/ if it still exists
rm -rf app/

# Verify git status
git status
```

- [ ] **Step 2: Docker compose local test**

```bash
# Build and test locally
docker compose up -d --build
sleep 3
curl http://localhost:8099/admin
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: remove old app directory, finalize restructure"
```

---

### Task 13: Final verification

- [ ] **Step 1: Verify all routes work**

```bash
# Test admin page
curl -s http://localhost:8099/admin | head -1
# Expected: <!DOCTYPE html>

# Test API status
curl -s http://localhost:8099/api/status
# Expected: {"connected":false,...}

# Test webhook GET
curl -s "http://localhost:8099/webhook?msg=test"
# Expected: {"detail":"微信未登录..."} (not 405 Method Not Allowed)
```

- [ ] **Step 2: Check no old names remain**

```bash
# Should find no results
grep -r "ILinkIncomingMessage" wxclawbotpush/ --include="*.py"
grep -r "extract_message_fields" wxclawbotpush/ --include="*.py"
grep -r "BufferHandler" wxclawbotpush/ --include="*.py"
grep -r "import main" wxclawbotpush/ --include="*.py"
```

- [ ] **Step 3: Tear down test and commit**

```bash
docker compose down
git commit --allow-empty -m "refactor: final verification, all routes and naming pass"
```
