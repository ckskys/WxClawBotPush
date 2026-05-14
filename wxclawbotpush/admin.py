"""管理面板路由：认证、用户管理、系统配置、个人设置、日志查看。"""
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
from auth import create_user, authenticate, create_session, get_session_user, delete_session, get_user, is_admin, verify_password, hash_password, SESSION_TIMEOUT
from client import get_client, close_client, get_qr_code_data, clear_qr_code_data
from log_utils import log_buffer, log_buffer_lock
from ilink.client import ILinkClient
from polling import start_polling, stop_polling, _poll_qr_code_status, is_polling

logger = logging.getLogger("wxclawbotpush")
router = APIRouter()


def _require_session(request: Request) -> int:
    """从 Authorization 头或 Cookie 中提取 session token 并验证，返回 user_id。"""
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
    """先验证登录，再验证管理员权限，返回 user_id。"""
    user_id = _require_session(request)
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user_id


# ── 认证路由 ───────────────────────────────────────────────

@router.post("/api/login")
def api_login(body: dict, response: Response):
    """用户登录：验证用户名密码，返回 session token 并设置 Cookie。"""
    username = (body or {}).get("username", "")
    password = (body or {}).get("password", "")
    user_id = authenticate(username, password)
    if not user_id:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user = get_user(user_id)
    if user and user.get("is_disabled"):
        raise HTTPException(status_code=403, detail="账号已禁用")

    token = create_session(user_id)
    response.set_cookie(
        key="session", value=token,
        max_age=SESSION_TIMEOUT, httponly=True,
        samesite="strict", secure=False,
    )
    return {"token": token, "user_id": user_id, "is_admin": user["is_admin"] if user else False}


@router.post("/api/register")
def api_register(body: dict, response: Response):
    """用户注册：需系统开放注册，用户名 ≥3 位，密码 ≥6 位。"""
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
    response.set_cookie(
        key="session", value=token,
        max_age=SESSION_TIMEOUT, httponly=True,
        samesite="strict", secure=False,
    )
    return {"token": token, "user_id": user_id, "is_admin": False}


@router.post("/api/logout")
def api_logout(request: Request, response: Response):
    """退出登录：删除 session token 并清除 Cookie。"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        token = request.cookies.get("session")
    if token:
        delete_session(token)
    response.delete_cookie(key="session")
    return {"success": True}


# ── 管理员路由 ──────────────────────────────────────────────

@router.get("/api/admin/users")
def admin_list_users(request: Request):
    """管理员：获取所有用户列表（含连接状态）。"""
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
    """管理员：创建新用户。"""
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
    """管理员：更新用户（禁用/启用、角色变更、重置密码）。"""
    _require_admin(request)
    db = get_db()
    if "is_disabled" in body:
        db.execute("UPDATE users SET is_disabled = ? WHERE id = ?", (int(body["is_disabled"]), user_id))
    if "is_admin" in body:
        db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(body["is_admin"]), user_id))
    if "password" in body:
        pw_hash = hash_password(body["password"])
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    db.commit()
    return {"success": True}


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(request: Request, user_id: int):
    """管理员：删除用户及其所有关联数据。"""
    _require_admin(request)
    from client import close_client as _close_client
    from polling import stop_polling as _stop_polling
    _stop_polling(user_id)
    _close_client(user_id)
    db = get_db()
    db.execute("DELETE FROM user_configs WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return {"success": True}


@router.get("/api/admin/config")
def admin_get_system_config(request: Request):
    """管理员：获取系统配置。"""
    _require_admin(request)
    return {
        "registration_open": get_system_config("registration_open", "1"),
    }


@router.put("/api/admin/config")
def admin_set_system_config(request: Request, body: dict):
    """管理员：更新系统配置。"""
    _require_admin(request)
    for key, value in body.items():
        set_system_config(key, str(value))
    return {"success": True}


@router.get("/api/admin/stats")
def admin_get_stats(request: Request):
    """管理员：获取系统统计（用户数、消息数、在线数）。"""
    _require_admin(request)
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    online_users = 0
    try:
        from polling import _polling_flags
        online_users = sum(1 for v in _polling_flags.values() if v)
    except Exception:
        pass
    return {
        "total_users": total_users,
        "online_users": online_users,
    }


# ── 用户设置路由 ────────────────────────────────────────────

@router.get("/api/user/config")
def user_get_config(request: Request):
    """获取当前用户的配置信息。"""
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
    """更新当前用户的消息模板和 base_url。"""
    user_id = _require_session(request)
    body = await request.json()
    allowed = {"message_template", "base_url"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if updates:
        save_user_config(user_id, updates)
    return {"success": True}


@router.get("/api/user/qrcode")
def user_get_qrcode(request: Request):
    """请求新的微信扫码登录二维码。"""
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
    """生成并返回二维码 PNG 图片。"""
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
    """获取当前用户的微信连接状态。"""
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
    """断开微信连接（不注销账号）。"""
    user_id = _require_session(request)
    stop_polling(user_id)
    close_client(user_id)
    clear_qr_code_data(user_id)
    save_user_config(user_id, {"bot_token": None, "account_id": None, "sync_buf": None})
    logger.info(f"已退出登录: user_id={user_id}")
    return {"success": True}


@router.post("/api/user/token")
def user_reset_token(request: Request):
    """重置当前用户的 webhook_token。"""
    import secrets
    user_id = _require_session(request)
    new_token = secrets.token_hex(16)
    save_user_config(user_id, {"webhook_token": new_token})
    return {"webhook_token": new_token}


@router.post("/api/user/change-password")
def user_change_password(request: Request, body: dict):
    """修改当前用户密码：需验证当前密码，新密码至少 6 位。"""
    user_id = _require_session(request)
    current_password = (body or {}).get("current_password", "")
    new_password = (body or {}).get("new_password", "")
    if not current_password or not new_password:
        raise HTTPException(status_code=400, detail="请输入当前密码和新密码")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6位")
    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码错误")
    pw_hash = hash_password(new_password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    db.commit()
    return {"success": True}


@router.post("/api/user/deactivate")
def user_deactivate(request: Request, response: Response):
    """注销当前账号：删除所有关联数据并清除会话。"""
    user_id = _require_session(request)
    stop_polling(user_id)
    close_client(user_id)
    clear_qr_code_data(user_id)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        token = request.cookies.get("session")
    if token:
        delete_session(token)
    response.delete_cookie(key="session")
    db = get_db()
    db.execute("DELETE FROM user_configs WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return {"success": True}


# ── 日志路由 ────────────────────────────────────────────────

@router.get("/api/logs")
def get_logs(request: Request, limit: int = 200):
    """管理员：查看系统日志（从内存缓冲区）。"""
    _require_admin(request)
    limit = max(1, min(limit, 1000))
    with log_buffer_lock:
        logs = list(log_buffer)[-limit:]
    return {"count": len(logs), "logs": logs}


@router.post("/api/logs/clear")
def clear_logs(request: Request):
    """管理员：清空日志缓冲区。"""
    _require_admin(request)
    with log_buffer_lock:
        log_buffer.clear()
    return {"success": True}


# ── 页面路由 ────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    """返回管理面板 HTML 页面。"""
    html_path = Path(__file__).parent / "templates" / "admin.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@router.get("/login.html", response_class=HTMLResponse)
def serve_login_page():
    """返回登录/注册 HTML 页面。"""
    html_path = Path(__file__).parent / "templates" / "login.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
