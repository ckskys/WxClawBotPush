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
