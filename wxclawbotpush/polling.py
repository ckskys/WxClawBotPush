"""后台轮询模块：微信消息同步和二维码扫码状态检测。"""
import logging
import threading
import time
from typing import Dict

from config import get_user_config, save_user_config
from client import get_client, recreate_client, get_qr_code_data
from ilink.client import ILinkClient

logger = logging.getLogger("wxclawbotpush")

_polling_threads: Dict[int, threading.Thread] = {}  # 用户 ID → 轮询线程
_polling_flags: Dict[int, bool] = {}  # 用户 ID → 是否继续轮询


def start_polling(user_id: int):
    """启动用户的消息轮询守护线程。"""
    if user_id in _polling_threads and _polling_threads[user_id].is_alive():
        return
    _polling_flags[user_id] = True
    t = threading.Thread(target=_poll_incoming_messages, args=(user_id,), daemon=True)
    _polling_threads[user_id] = t
    t.start()
    logger.info(f"后台消息轮询已启动: user_id={user_id}")


def stop_polling(user_id: int):
    """停止用户的消息轮询（通过标志位通知线程退出）。"""
    _polling_flags[user_id] = False
    logger.info(f"后台消息轮询已停止: user_id={user_id}")


def stop_all_polling():
    """停止所有用户的消息轮询。"""
    for uid in list(_polling_flags.keys()):
        _polling_flags[uid] = False
    logger.info("所有轮询已停止")


def is_polling(user_id: int) -> bool:
    """检查用户的消息轮询是否正在运行。"""
    return _polling_flags.get(user_id, False)


def _poll_incoming_messages(user_id: int):
    """消息轮询循环：长轮询 /ilink/bot/getupdates，发现新联系人和上下文 token 后自动保存。"""
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
    """轮询二维码扫码状态（最长 240 秒）；扫码成功后自动保存 token 并启动消息轮询。"""
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
