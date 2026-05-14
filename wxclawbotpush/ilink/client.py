import base64
import hashlib
import json
import logging
import os
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .models import IncomingMessage

logger = logging.getLogger("wxclawbotpush.ilink")


class ILinkClient:
    def __init__(
        self,
        base_url: str,
        bot_token: Optional[str] = None,
        account_id: Optional[str] = None,
        sync_buf: Optional[str] = None,
        timeout: int = 20,
        log_func: Optional[Callable[[str, str], None]] = None,
    ):
        self.base_url = (base_url or "https://ilinkai.weixin.qq.com").rstrip("/")
        self.bot_token = bot_token
        self.account_id = account_id
        self.sync_buf = sync_buf
        self.timeout = timeout
        self._log_func = log_func
        self.channel_version = "1.0.2"
        self.cdn_base_url = "https://novac2c.cdn.weixin.qq.com/c2c"
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    def _log(self, level: str, message: str):
        if self._log_func:
            try:
                self._log_func(level, f"[ILinkClient] {message}")
                return
            except Exception:
                pass
        lv = (level or "info").lower()
        if lv == "debug":
            logger.debug(f"[ILinkClient] {message}")
        elif lv == "warning":
            logger.warning(f"[ILinkClient] {message}")
        elif lv == "error":
            logger.error(f"[ILinkClient] {message}")
        else:
            logger.info(f"[ILinkClient] {message}")

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def _headers(self, auth_required: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "WxClawbotPush/1.0",
        }
        if auth_required and self.bot_token:
            headers["AuthorizationType"] = "ilink_bot_token"
            headers["Authorization"] = f"Bearer {self.bot_token}"
            headers["X-WECHAT-UIN"] = self._build_wechat_uin()
        return headers

    @staticmethod
    def _build_wechat_uin() -> str:
        random_u32 = random.getrandbits(32)
        return base64.b64encode(str(random_u32).encode("utf-8")).decode("ascii")

    def _with_base_info(self, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(body or {})
        base_info = payload.get("base_info")
        if not isinstance(base_info, dict):
            base_info = {}
        base_info.setdefault("channel_version", self.channel_version)
        payload["base_info"] = base_info
        return payload

    def _post(self, url: str, json_body: Dict[str, Any], auth: bool = True, **kwargs) -> httpx.Response:
        """发送 POST 请求。"""
        return self._client.post(url, json=json_body, headers=self._headers(auth), **kwargs)

    def _get(self, url: str, params: Dict[str, Any] = None, auth: bool = True, **kwargs) -> httpx.Response:
        """发送 GET 请求。"""
        return self._client.get(url, params=params, headers=self._headers(auth), **kwargs)

    @staticmethod
    def _json(resp) -> Dict[str, Any]:
        if not resp:
            return {}
        try:
            return resp.json() or {}
        except Exception:
            text = (getattr(resp, "text", "") or "").strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except Exception:
                return {}

    @staticmethod
    def _ok(payload: Dict[str, Any]) -> bool:
        if not payload:
            return False
        code = payload.get("errcode")
        if code is None:
            code = payload.get("code")
        if code is None:
            code = payload.get("ret")
        if code is None:
            err = payload.get("errmsg") or payload.get("error") or payload.get("error_msg")
            if err and str(err).strip().lower() not in {"ok", "success", "succeed"}:
                return False
            state = payload.get("status") or payload.get("state")
            if isinstance(state, str) and state.strip().lower() in {"error", "failed", "fail"}:
                return False
            return True
        try:
            return int(str(code)) == 0
        except Exception:
            return str(code).strip().lower() in {"0", "ok", "success", "succeed"}

    @staticmethod
    def _short_text(value: Any, max_len: int = 240) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        else:
            text = str(value)
        text = text.replace("\n", " ").replace("\r", " ").strip()
        if len(text) > max_len:
            return f"{text[:max_len]}..."
        return text

    # ── QR Code ──────────────────────────────────────────────────────

    def get_qrcode(self) -> Dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type=3"
        self._log("debug", f"请求二维码: {url}")
        resp = self._get(url, auth=False)
        payload = self._json(resp)
        if not payload:
            self._log("warning", "二维码接口返回空响应")
            return {"success": False, "message": "获取二维码失败"}

        data = payload.get("data") or payload.get("result") or payload
        qrcode = (
            data.get("qrcode") or data.get("qr_code")
            or data.get("qrcode_id") or data.get("ticket")
        )
        qrcode_url = (
            data.get("qrcode_url") or data.get("url")
            or data.get("qrcodeUrl") or data.get("qr_url")
            or data.get("qrcode_img_content") or data.get("qrcode_img_url")
            or data.get("qr_img")
        )

        if not qrcode_url and qrcode:
            qrcode_url = f"https://liteapp.weixin.qq.com/q/7GiQu1?qrcode={qrcode}&bot_type=3"

        result = {
            "success": self._ok(payload) and bool(qrcode or qrcode_url),
            "qrcode": qrcode,
            "qrcode_url": qrcode_url,
            "raw": payload,
            "message": payload.get("errmsg") or payload.get("message"),
        }
        self._log(
            "info" if result.get("success") else "warning",
            f"二维码解析: success={result.get('success')}, has_url={bool(result.get('qrcode_url'))}",
        )
        return result

    def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        url = f"{self.base_url}/ilink/bot/get_qrcode_status"
        self._log("debug", f"查询二维码状态: qrcode={qrcode}")
        resp = self._get(url, params={"qrcode": qrcode}, auth=False)
        payload = self._json(resp)
        if not payload:
            resp = self._get(f"{url}?qrcode={qrcode}", auth=False)
            payload = self._json(resp)
        if not payload:
            self._log("warning", "二维码状态接口返回空响应")
            return {
                "success": False, "status": "waiting",
                "token": None, "account_id": None,
                "raw": {}, "message": "二维码状态接口返回空响应",
            }

        data = payload.get("data") or payload.get("result") or payload
        token = (
            data.get("bot_token") or data.get("token") or data.get("access_token")
            or self._find_first_value(data, ["bot_token", "access_token", "token", "jwt", "auth_token"])
        )
        account_id = (
            data.get("account_id") or data.get("ilink_bot_id")
            or data.get("wxid") or data.get("uid") or data.get("user_id")
        )
        base_url = data.get("baseurl") or data.get("base_url")

        if token:
            self.bot_token = token
        if account_id:
            self.account_id = str(account_id)

        state = (
            data.get("status") or data.get("state")
            or self._find_first_value(data, ["status", "state", "scan_status"])
            or "waiting"
        )

        return {
            "success": self._ok(payload),
            "status": str(state).lower(),
            "token": token,
            "account_id": account_id,
            "base_url": base_url,
            "raw": payload,
            "message": payload.get("errmsg") or payload.get("message"),
        }

    # ── Send Messages ─────────────────────────────────────────────────

    @staticmethod
    def _build_user_candidates(to_user: str) -> List[str]:
        raw = str(to_user or "").strip()
        if not raw:
            return []
        candidates = [raw]
        if "@" in raw:
            candidates.append(raw.split("@", 1)[0])
        if raw.endswith("@im.wechat"):
            candidates.append(raw[:-len("@im.wechat")])
        else:
            candidates.append(f"{raw}@im.wechat")
        uniq = []
        for item in candidates:
            if item and item not in uniq:
                uniq.append(item)
        return uniq

    def _build_protocol_msg_payload(
        self, user_id: str, text: str, context_token: Optional[str]
    ) -> Dict[str, Any]:
        msg = {
            "from_user_id": str(self.account_id or ""),
            "to_user_id": user_id,
            "client_id": f"mp-{uuid.uuid4()}",
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        return {"msg": msg}

    def _is_send_success(self, payload: Dict[str, Any]) -> bool:
        if not payload:
            return False
        code = self._find_first_value(
            payload, ["errcode", "code", "ret", "result_code", "status_code"]
        )
        if code is not None:
            try:
                return int(str(code)) == 0
            except Exception:
                return str(code).strip().lower() in {"0", "ok", "success", "succeed"}
        success_flag = self._find_first_value(
            payload, ["success", "ok", "is_success", "sent"]
        )
        if isinstance(success_flag, bool):
            return success_flag
        if success_flag is not None:
            return str(success_flag).strip().lower() in {"1", "true", "ok", "success", "succeed", "sent"}
        return False

    def _is_send_explicit_failure(self, payload: Dict[str, Any]) -> bool:
        if not payload:
            return False
        code = self._find_first_value(
            payload, ["errcode", "code", "ret", "result_code", "status_code"]
        )
        if code is not None:
            try:
                return int(str(code)) != 0
            except Exception:
                return str(code).strip().lower() not in {"0", "ok", "success", "succeed"}
        success_flag = self._find_first_value(payload, ["success", "ok", "is_success", "sent"])
        if isinstance(success_flag, bool):
            return not success_flag
        return False

    def _is_send_http_success(self, resp, payload: Dict[str, Any]) -> bool:
        if resp is None:
            return False
        status_code = getattr(resp, "status_code", None)
        if status_code is None:
            return False
        try:
            status_ok = 200 <= int(status_code) < 300
        except Exception:
            status_ok = False
        if not status_ok:
            return False
        if not payload:
            return True
        return not self._is_send_explicit_failure(payload)

    def send_text(
        self, to_user: str, text: str, context_token: Optional[str] = None
    ) -> bool:
        if not self.bot_token:
            self._log("warning", "发送消息失败：bot token 未配置")
            return False
        if not to_user or not text:
            self._log("warning", "发送消息失败：to_user 或 text 为空")
            return False

        url = f"{self.base_url}/ilink/bot/sendmessage"
        user_candidates = self._build_user_candidates(to_user)
        last_error = ""

        for user_id in user_candidates:
            body = self._build_protocol_msg_payload(
                user_id=user_id, text=text, context_token=context_token
            )
            request_body = self._with_base_info(body)
            resp = self._post(url, json_body=request_body)
            payload = self._json(resp)
            if self._is_send_success(payload) or self._is_send_http_success(resp, payload):
                self._log("info", f"发送成功: to_user={user_id}")
                return True
            http_code = getattr(resp, "status_code", None)
            err_msg = (
                self._find_first_value(payload, ["errmsg", "message", "error", "detail"])
                if payload else None
            )
            last_error = f"http={http_code}, err={self._short_text(err_msg)}"

        self._log("warning", f"发送失败: to_user={to_user}, {last_error}")
        return False

    # ── Poll Updates ──────────────────────────────────────────────────

    def poll_updates(
        self, timeout_seconds: int = 25
    ) -> Tuple[List[IncomingMessage], Optional[str], Dict[str, Any]]:
        if not self.bot_token:
            return [], self.sync_buf, {"success": False, "message": "bot token 未配置"}

        url = f"{self.base_url}/ilink/bot/getupdates"
        body = {
            "sync_buf": self.sync_buf or "",
            "timeout": timeout_seconds,
        }
        request_body = self._with_base_info(body)
        resp = self._post(url, json_body=request_body, timeout=timeout_seconds + 10)
        payload = self._json(resp)

        items, sync_buf = self._extract_updates(payload)
        parsed = []
        for item in items:
            msg = self._parse_incoming(item)
            if msg:
                parsed.append(msg)

        if sync_buf is not None:
            self.sync_buf = str(sync_buf)

        result = {
            "success": self._ok(payload),
            "raw": payload,
            "item_count": len(items),
            "parsed_count": len(parsed),
        }
        if parsed:
            self._log("info", f"轮询收到消息: count={len(parsed)}")
        return parsed, self.sync_buf, result

    def _extract_updates(self, payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        data = payload.get("data") or payload.get("result") or payload
        sync_buf = (
            data.get("get_updates_buf") or payload.get("get_updates_buf")
            or data.get("sync_buf") or data.get("syncBuf")
            or payload.get("sync_buf") or payload.get("syncBuf")
        )
        for key in ["msgs", "updates", "messages", "items", "events", "msg_list", "msgList", "add_msgs", "addMsgs"]:
            val = data.get(key) or payload.get(key)
            if isinstance(val, list):
                return val, sync_buf
        nested = self._find_first_list(data, prefer_keys=["msgs", "updates", "messages", "items", "events"])
        if isinstance(nested, list):
            return nested, sync_buf
        return [], sync_buf

    def _parse_incoming(self, item: Dict[str, Any]) -> Optional[IncomingMessage]:
        if not isinstance(item, dict):
            return None
        message = item
        for key in ["message", "msg", "event", "payload", "data"]:
            child = item.get(key)
            if isinstance(child, dict):
                message = child
                break

        sender = (
            message.get("from") if isinstance(message.get("from"), dict)
            else message.get("sender") if isinstance(message.get("sender"), dict)
            else message.get("user") if isinstance(message.get("user"), dict)
            else message.get("from_user") if isinstance(message.get("from_user"), dict)
            else {}
        )

        user_id = (
            self._pick_value(sender, ["user_id", "id", "wxid", "uid"])
            or self._pick_value(message, [
                "from_user", "from_user_id", "user_id", "uid", "wxid",
                "from_uid", "fromUser", "fromUserId", "openid",
            ])
        )
        user_id = self._as_scalar(user_id)
        if not user_id:
            return None

        text = None
        item_list = message.get("item_list") if isinstance(message.get("item_list"), list) else []
        for one in item_list:
            if not isinstance(one, dict):
                continue
            if one.get("type") == 1 and isinstance(one.get("text_item"), dict):
                text = self._pick_value(one.get("text_item") or {}, ["text", "content"])
                if text:
                    break

        if isinstance(message.get("text"), dict):
            text = self._pick_value(message.get("text") or {}, ["content", "text", "value", "msg"])
        if not text:
            text = self._pick_value(message, ["content", "message", "msg", "text", "body"])
        if not text:
            text = self._find_first_value(message, ["content", "text", "message", "msg", "body"])
        if not text:
            return None
        if isinstance(text, dict):
            text = self._pick_value(text, ["content", "text", "value", "message"])
        if not isinstance(text, str):
            text = str(text)

        username = (
            self._pick_value(sender, ["name", "nickname", "username", "remark"])
            or self._pick_value(message, ["username", "nickname", "from_name"])
            or str(user_id)
        )
        return IncomingMessage(
            user_id=str(user_id),
            text=str(text),
            username=str(username) if username else None,
            raw=item,
        )

    # ── Connection Test ───────────────────────────────────────────────

    def test_connection(self) -> Tuple[bool, str]:
        if not self.bot_token:
            return False, "未登录，缺少 bot token"
        url = f"{self.base_url}/ilink/bot/getconfig"
        resp = self._post(url, json_body={})
        payload = self._json(resp)
        if self._ok(payload):
            return True, "连接正常"
        return False, payload.get("errmsg") or payload.get("message") or "连接失败"

    # ── Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def _pick_value(obj: Dict[str, Any], keys: List[str]) -> Optional[Any]:
        for key in keys:
            if key in obj and obj.get(key) not in (None, ""):
                return obj.get(key)
        return None

    @classmethod
    def _find_first_value(cls, data: Any, keys: List[str], max_depth: int = 5) -> Optional[Any]:
        if max_depth < 0 or data is None:
            return None
        if isinstance(data, dict):
            direct = cls._pick_value(data, keys)
            if direct not in (None, ""):
                return direct
            for value in data.values():
                found = cls._find_first_value(value, keys, max_depth - 1)
                if found not in (None, ""):
                    return found
        elif isinstance(data, list):
            for value in data:
                found = cls._find_first_value(value, keys, max_depth - 1)
                if found not in (None, ""):
                    return found
        return None

    @classmethod
    def _find_first_list(cls, data: Any, prefer_keys: List[str], max_depth: int = 5) -> Optional[List[Any]]:
        if max_depth < 0 or data is None:
            return None
        if isinstance(data, dict):
            for key in prefer_keys:
                value = data.get(key)
                if isinstance(value, list):
                    return value
            for value in data.values():
                found = cls._find_first_list(value, prefer_keys, max_depth - 1)
                if found is not None:
                    return found
        elif isinstance(data, list):
            if data and all(isinstance(it, dict) for it in data):
                return data
        return None

    @staticmethod
    def _as_scalar(value: Any) -> Optional[Any]:
        if value in (None, ""):
            return None
        if isinstance(value, (dict, list, tuple, set)):
            return None
        return value
