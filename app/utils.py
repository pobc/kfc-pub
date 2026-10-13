"""不依赖数据库、HTTP 或外部服务的通用纯函数。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def clean(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")[:500]


def mask(value: str) -> str:
    text = clean(value).strip()
    if not text:
        return "EMPTY"
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"
