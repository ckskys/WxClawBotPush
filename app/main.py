import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from ilink import ILinkClient

# ── Logging ───────────────────────────────────────────────────────────

log_buffer: deque = deque(maxlen=1000)
log_buffer_lock = threading.Lock()


class BufferHandler(logging.Handler):
    def emit(self, record):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        with log_buffer_lock:
            log_buffer.append(entry)


logger = logging.getLogger("wxclawbotpush")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

buffer_handler = BufferHandler()
buffer_handler.setLevel(logging.INFO)
buffer_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(buffer_handler)

# ── Config ────────────────────────────────────────────────────────────

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
_config: Dict[str, Any] = None


def load_config() -> Dict[str, Any]:
    global _config
    cfg = {**DEFAULT_CONFIG}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            logger.warning("读取配置文件失败，使用默认配置")
    _config = cfg
    return cfg


def save_config(updates: Dict[str, Any] = None):
    global _config
    cfg = load_config()
    if updates:
        cfg.update(updates)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _config = cfg
    return cfg


# ── Client Management ─────────────────────────────────────────────────

_client: Optional[ILinkClient] = None
_qrcode_data: Dict[str, Any] = {}
_polling_active = False
_polling_thread: Optional[threading.Thread] = None


def get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        load_config()
    return _config


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


def reset_client(token=None, account_id=None, sync_buf=None):
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


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(title="WxClawbotPush", description="Webhook to WeChat ClawBot", version="1.0.0")


@app.on_event("startup")
def startup():
    load_config()
    if get_config().get("bot_token"):
        start_polling()
    logger.info("WxClawbotPush 启动完成")


# ── Webhook Endpoint ──────────────────────────────────────────────────

def extract_message_fields(data: Dict[str, Any]) -> str:
    """从任意 JSON 中提取消息文本。"""
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


def _do_send(cfg: dict, message_text: str) -> dict:
    """共享的消息发送逻辑。"""
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


@app.get("/webhook")
async def webhook_get(request: Request):
    """GET 方式发送纯文本消息。"""
    cfg = get_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=503, detail="微信未登录，请先扫码登录")

    query_params = dict(request.query_params)
    message_text = query_params.pop("msg", "")
    if query_params:
        message_text += "\n\n" + json.dumps(query_params, ensure_ascii=False, indent=2)
    if not message_text:
        raise HTTPException(status_code=400, detail="缺少 msg 参数")

    return _do_send(cfg, message_text)


@app.post("/webhook")
async def webhook_post(request: Request):
    """
    接收 Webhook 消息，转发到微信 ClawBot。

    支持任意 JSON 格式，自动提取 title/text/content/link 等字段。
    也支持 URL 参数 ?msg=xxx 作为纯文本消息发送。
    """
    cfg = get_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=503, detail="微信未登录，请先扫码登录")

    # 解析消息内容
    message_text = ""

    # 支持 URL 参数 ?msg=xxx (纯文本消息)
    query_params = dict(request.query_params)
    if "msg" in query_params:
        message_text = query_params["msg"]
        # 合并其他参数作为额外信息
        extra = {k: v for k, v in query_params.items() if k != "msg"}
        if extra:
            message_text += "\n\n" + json.dumps(extra, ensure_ascii=False, indent=2)

    # 支持 JSON body
    try:
        body = await request.json()
    except Exception:
        body = None

    if body:
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    msg = extract_message_fields(item)
                    if msg:
                        message_text += ("\n---\n" if message_text else "") + msg
        elif isinstance(body, dict):
            msg = extract_message_fields(body)
            if msg:
                message_text += ("\n---\n" if message_text else "") + msg
        elif isinstance(body, str):
            if body:
                message_text += ("\n" if message_text else "") + body

    if not message_text:
        # 如果有 raw body
        raw = await request.body()
        if raw:
            try:
                message_text = raw.decode("utf-8")
            except Exception:
                message_text = raw.decode("latin-1")

    if not message_text:
        raise HTTPException(status_code=400, detail="消息内容为空")

    return _do_send(cfg, message_text)


# ── Admin API ─────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    """获取登录状态。"""
    cfg = get_config()
    qr = _qrcode_data
    return {
        "connected": bool(cfg.get("bot_token")),
        "account_id": cfg.get("account_id"),
        "known_users": len(cfg.get("known_users") or []),
        "qrcode": qr.get("qrcode"),
        "qrcode_status": qr.get("status", "none"),
        "qrcode_url": qr.get("qrcode_url"),
    }


@app.get("/api/qrcode")
def api_get_qrcode():
    """获取登录二维码。"""
    global _qrcode_data
    client = ILinkClient(base_url=get_config().get("base_url", "https://ilinkai.weixin.qq.com"))
    result = client.get_qrcode()
    if result.get("success"):
        _qrcode_data = {
            "qrcode": result.get("qrcode"),
            "qrcode_url": result.get("qrcode_url"),
            "status": "waiting",
            "updated_at": int(time.time()),
        }
        # 后台检测扫码
        threading.Thread(target=_watch_login, args=(result.get("qrcode"),), daemon=True).start()
    client.close()
    return result


@app.get("/api/qrcode/image")
def api_qrcode_image():
    """返回二维码图片 (PNG)。"""
    qr = _qrcode_data
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


@app.post("/api/logout")
def api_logout():
    """退出登录。"""
    global _client, _qrcode_data
    stop_polling()
    if _client:
        _client.close()
        _client = None
    _qrcode_data = {}
    save_config({"bot_token": None, "account_id": None, "sync_buf": None})
    logger.info("已退出登录")
    return {"success": True}


@app.get("/api/logs")
def api_logs(limit: int = 200):
    """获取日志。"""
    limit = max(1, min(limit, 1000))
    with log_buffer_lock:
        logs = list(log_buffer)[-limit:]
    return {"count": len(logs), "logs": logs}


@app.post("/api/logs/clear")
def api_clear_logs():
    """清空日志。"""
    with log_buffer_lock:
        log_buffer.clear()
    return {"success": True}


# ── Message Polling ────────────────────────────────────────────────────


def start_polling():
    """启动后台消息轮询线程。"""
    global _polling_active, _polling_thread
    if _polling_thread and _polling_thread.is_alive():
        return
    _polling_active = True
    _polling_thread = threading.Thread(target=_poll_loop, daemon=True)
    _polling_thread.start()
    logger.info("后台消息轮询已启动")


def stop_polling():
    """停止后台消息轮询线程。"""
    global _polling_active
    _polling_active = False
    logger.info("后台消息轮询已停止")


def _poll_loop():
    """持续轮询微信用户发来的消息，自动记录用户 ID。"""
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


# ── QR Code Login Watcher ─────────────────────────────────────────────

def _watch_login(qrcode_id: str):
    """后台检测扫码状态。"""
    cfg = get_config()
    base_url = cfg.get("base_url", "https://ilinkai.weixin.qq.com")
    client = ILinkClient(base_url=base_url)
    max_wait = 240
    interval = 3

    global _qrcode_data
    for _ in range(max_wait // interval):
        time.sleep(interval)
        try:
            result = client.get_qrcode_status(str(qrcode_id))
            status = result.get("status", "").lower()
            _qrcode_data["status"] = status

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
                reset_client(token=token, account_id=account_id)
                _qrcode_data["status"] = "connected"
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

    _qrcode_data["status"] = "timeout"
    client.close()


# ── Admin Page ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    """管理页面 HTML。"""
    return ADMIN_HTML


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WxClawbotPush - 微信通知转发</title>
<style>
  :root { --bg: #f5f5f5; --card: #fff; --text: #333; --muted: #888; --primary: #07c160; --danger: #e74c3c; --radius: 8px; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 800px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 22px; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
  .logo { width: 32px; height: 32px; background: var(--primary); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; font-size: 18px; }
  .card { background: var(--card); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card-title { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .status-dot.on { background: var(--primary); }
  .status-dot.off { background: var(--muted); }
  .btn { padding: 8px 20px; border: none; border-radius: var(--radius); cursor: pointer; font-size: 14px; }
  .btn-primary { background: var(--primary); color: #fff; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-outline { background: transparent; border: 1px solid #ddd; }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  input, textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: var(--radius); font-size: 14px; }
  textarea { resize: vertical; min-height: 80px; }
  .form-group { margin-bottom: 12px; }
  .form-group label { display: block; margin-bottom: 4px; font-weight: 500; font-size: 14px; }
  .qrcode-container { text-align: center; padding: 16px; }
  .qrcode-container img { max-width: 280px; border-radius: var(--radius); }
  .log-entry { font-family: monospace; font-size: 12px; padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
  .log-time { color: var(--muted); margin-right: 8px; }
  .log-INFO { color: #333; }
  .log-WARNING { color: #e67e22; }
  .log-ERROR { color: var(--danger); }
  .toast { position: fixed; top: 16px; right: 16px; background: #333; color: #fff; padding: 10px 20px; border-radius: var(--radius); z-index: 999; display: none; }
  .hint { color: var(--muted); font-size: 13px; margin-top: 4px; }
</style>
</head>
<body>
<div class="toast" id="toast"></div>
<div class="container">
  <h1><span class="logo">W</span> WxClawbotPush</h1>

  <!-- 状态卡片 -->
  <div class="card">
    <div class="card-title">系统状态</div>
    <div id="status-area">加载中...</div>
  </div>

  <!-- 二维码卡片 -->
  <div class="card" id="qrcode-card" style="display:none;">
    <div class="card-title">扫码登录</div>
    <div class="qrcode-container">
      <img id="qrcode-img" src="" alt="微信扫码登录">
    </div>
    <p class="hint" style="text-align:center;margin-top:8px;">请使用微信扫一扫完成登录</p>
    <p class="hint" style="text-align:center;">二维码有效期约 4 分钟</p>
  </div>

  <!-- 操作按钮 -->
  <div class="card">
    <div class="card-title">操作</div>
    <button class="btn btn-primary" id="btn-qrcode">获取登录二维码</button>
    <button class="btn btn-danger" id="btn-logout" style="margin-left:8px;">退出登录</button>
    <button class="btn btn-outline" id="btn-refresh-status" style="margin-left:8px;">刷新状态</button>
  </div>

  <!-- 日志卡片 -->
  <div class="card">
    <div class="card-title">
      运行日志
      <button class="btn btn-outline" id="btn-clear-logs" style="float:right;font-size:12px;padding:4px 10px;">清空</button>
    </div>
    <div id="log-area" style="max-height:400px;overflow-y:auto;">加载中...</div>
  </div>
</div>

<script>
function toast(msg) { var t=document.getElementById('toast'); t.textContent=msg; t.style.display='block'; setTimeout(function(){t.style.display='none';},2000); }
function api(path, opts) { return fetch(path, opts).then(function(r){ return r.json(); }); }

function refreshStatus() {
  api('/api/status').then(function(s){
    var area = document.getElementById('status-area');
    var conn = s.connected;
    area.innerHTML =
      '<p>连接状态：<span class="status-dot '+(conn?'on':'off')+'"></span>'+(conn?'已连接':'未连接')+'</p>'+
      '<p>账号ID：'+(s.account_id||'-')+'</p>'+
      '<p>已连接用户数：'+(s.known_users||0)+'</p>'+
      '<p>二维码状态：'+(s.qrcode_status||'none')+'</p>'+
      '<p style="margin-top:8px;">Webhook 地址：<code>'+window.location.protocol+'//'+window.location.host+'/webhook</code></p>';
    var qrCard = document.getElementById('qrcode-card');
    if (!conn && s.qrcode) {
      qrCard.style.display = '';
      var img = document.getElementById('qrcode-img');
      img.src = '/api/qrcode/image?t='+Date.now();
    } else if (conn) {
      qrCard.style.display = 'none';
    }
  });
}

function refreshLogs() {
  api('/api/logs').then(function(d){
    var area = document.getElementById('log-area');
    area.innerHTML = d.logs.map(function(l){
      return '<div class="log-entry"><span class="log-time">'+l.time+'</span><span class="log-'+l.level+'">['+l.level+'] '+l.message+'</span></div>';
    }).reverse().join('');
  });
}


document.getElementById('btn-qrcode').addEventListener('click', function(){
  var btn = this; btn.disabled = true; btn.textContent = '获取中...';
  api('/api/qrcode').then(function(r){
    btn.disabled = false; btn.textContent = '获取登录二维码';
    if (r.success) {
      toast('二维码已生成，请扫码');
      refreshStatus();
    } else {
      toast('获取失败: '+(r.message||'未知错误'));
    }
  });
});

document.getElementById('btn-logout').addEventListener('click', function(){
  if (!confirm('确定要退出登录吗？退出后 Webhook 将无法转发消息。')) return;
  api('/api/logout', { method:'POST' }).then(function(r){
    if (r.success) { toast('已退出登录'); refreshStatus(); } else { toast('退出失败'); }
  });
});

document.getElementById('btn-refresh-status').addEventListener('click', function(){ refreshStatus(); refreshLogs(); });
document.getElementById('btn-clear-logs').addEventListener('click', function(){
  api('/api/logs/clear', { method:'POST' }).then(function(r){ if (r.success) { toast('日志已清空'); refreshLogs(); } });
});

refreshStatus(); refreshLogs();
setInterval(function(){ refreshStatus(); refreshLogs(); }, 15000);
</script>
</body>
</html>"""
