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
