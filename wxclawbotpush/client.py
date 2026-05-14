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
