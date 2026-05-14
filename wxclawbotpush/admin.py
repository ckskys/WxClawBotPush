import logging
import time
import threading
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from config import get_config, save_config
from client import get_client, close_client, _qr_code_data
from log_utils import log_buffer, log_buffer_lock
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
