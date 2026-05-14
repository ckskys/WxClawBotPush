from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class IncomingMessage:
    user_id: str
    text: str
    username: Optional[str] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    context_token: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
