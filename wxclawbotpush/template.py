import re
from typing import Any, Dict

_TOKEN_RE = re.compile(r"\{\{\s*(\w+(?:\.\w+)*)\s*\}\}")


def render_template(template_str: str, data: Dict[str, Any]) -> str:
    """将 {{ field.sub }} 占位符替换为 data 中对应的值。缺失字段替换为空字符串。"""
    if not template_str:
        return ""

    def replace_match(match: re.Match) -> str:
        path = match.group(1).strip()
        value = _resolve_path(data, path.split("."))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            import json
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _TOKEN_RE.sub(replace_match, template_str)


def _resolve_path(obj: Any, keys: list) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            try:
                idx = int(key)
                obj = obj[idx] if 0 <= idx < len(obj) else None
            except ValueError:
                return None
        else:
            return None
        if obj is None:
            return None
    return obj
