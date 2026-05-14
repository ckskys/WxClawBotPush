"""Webhook 路由模块：接收外部推送请求并转发到微信联系人。"""
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
    """根据 webhook_token 查找对应的用户 ID。"""
    db = get_db()
    row = db.execute("SELECT user_id FROM user_configs WHERE webhook_token = ?", (token,)).fetchone()
    return row["user_id"] if row else None


def _get_token_from_request(request: Request) -> Optional[str]:
    """从请求的 query string 或 X-Token 头部提取 token。"""
    token = request.query_params.get("token")
    if token:
        return token
    return request.headers.get("X-Token") or request.headers.get("x-token")


def parse_webhook_payload(data: Dict[str, Any]) -> str:
    """从 webhook 负载中提取标题、正文和链接，拼成消息文本。"""
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
    """向用户的所有已知联系人广播消息（自动去重）。"""
    logger.info(f"[BROADCAST-IN] user_id={user_id} text_len={len(message_text)} text_preview={message_text[:80]}")
    cfg = get_user_config(user_id)
    known_users_raw = list(cfg.get("known_users") or [])
    # 去重，保持顺序
    seen = set()
    known_users = []
    for u in known_users_raw:
        if u not in seen:
            seen.add(u)
            known_users.append(u)
    if len(known_users_raw) != len(known_users):
        # 修复持久化中的重复数据
        from config import save_user_config
        save_user_config(user_id, {"known_users": known_users})

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
    """记录推送日志到 push_logs 表。"""
    db = get_db()
    db.execute(
        "INSERT INTO push_logs (user_id, target_user, status) VALUES (?, ?, ?)",
        (user_id, target_user, status),
    )
    db.commit()


def _build_message_text(request: Request, body: Any) -> str:
    """从 POST 请求体和 query 参数构建消息文本（兼容群晖 text 参数）。"""
    message_text = ""
    query_params = dict(request.query_params)

    # 优先 msg，其次 text（群晖 GET 格式也适用 POST）
    if "msg" in query_params or "text" in query_params:
        message_text = query_params.get("msg", "") or query_params.get("text", "")
        extra = {k: v for k, v in query_params.items() if k not in ("msg", "text", "token")}
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
    """GET /webhook — 通过 query 参数 msg= 或 text= 推送消息（兼容群晖）。"""
    logger.info(f"[WEBHOOK-IN] method={request.method} url={str(request.url)} client={request.client.host if request.client else '?'}")
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="缺少 token 鉴权参数")
    user_id = _find_user_by_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的 token")

    query_params = dict(request.query_params)
    # 优先 msg，其次 text（群晖默认格式）
    message_text = query_params.pop("msg", "") or query_params.pop("text", "")
    extra = {k: v for k, v in query_params.items() if k not in ("token", "text")}
    if extra:
        message_text += "\n\n" + json.dumps(extra, ensure_ascii=False, indent=2)
    if not message_text.strip():
        raise HTTPException(status_code=400, detail="缺少 msg 或 text 参数")

    return _broadcast_to_users(user_id, message_text)


@router.post("/webhook")
async def webhook_post_handler(request: Request):
    """POST /webhook — 通过请求体 JSON 推送消息，支持模板渲染。"""
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
