"""iLink 协议数据模型。"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class IncomingMessage:
    """接收到的微信消息。"""
    user_id: str  # 发送者 ID
    text: str  # 消息文本
    username: Optional[str] = None  # 发送者昵称
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    context_token: Optional[str] = None  # 用于回复的上下文 token
    raw: Dict[str, Any] = field(default_factory=dict)  # 原始数据
