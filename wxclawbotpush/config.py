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


def save_config(updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
