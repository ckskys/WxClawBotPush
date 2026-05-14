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
