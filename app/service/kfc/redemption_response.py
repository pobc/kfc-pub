"""Validation for responses observed from the fixed browser checkout flow."""

from __future__ import annotations

import hashlib
import math
from typing import Any
from urllib.parse import urlsplit


class CouponUnavailableError(RuntimeError):
    def __init__(self, reasons: list[Any]) -> None:
        super().__init__("目标卡券库存不足，无法下单")
        self.reasons = reasons


def matches_endpoint(url: str, names: set[str]) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == "order.kfc.com.cn" and parsed.path.rstrip("/").rsplit("/", 1)[-1] in names


def response_succeeded(payload: dict[str, Any]) -> bool:
    return type(payload.get("code")) in (str, int) and str(payload["code"]) == "0"


def require_available_coupon(payload: dict[str, Any], coupon_code: str) -> dict[str, Any]:
    if not response_succeeded(payload):
        raise RuntimeError("KFC getCoupon 返回失败")
    # The endpoint groups usable/unusable cards in separate lists. Match the
    # assigned code in either list; never use a different card's inventory.
    pending = [payload.get("data")]
    digest = hashlib.md5(coupon_code.encode()).hexdigest()
    matches = []
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            code = str(node.get("couponCode") or "")
            if code and (code == coupon_code or code.lower() == digest):
                matches.append(node)
            else:
                pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    if not matches:
        raise RuntimeError("KFC getCoupon 中未找到已分配卡券")
    for coupon in matches:
        value = coupon.get("availableCount")
        try:
            count = float(value) if not isinstance(value, bool) else math.nan
        except (TypeError, ValueError):
            count = math.nan
        if not math.isfinite(count):
            raise RuntimeError("KFC 卡券库存字段无效")
        if count < 1:
            reasons = coupon.get("unUseReason")
            raise CouponUnavailableError(reasons if isinstance(reasons, list) else [])
    return matches[0]


def coupon_cart_id(payload: dict[str, Any]) -> str:
    if not response_succeeded(payload):
        raise RuntimeError("KFC useCoupon 返回失败")
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    cart = data.get("cart")
    cart = cart if isinstance(cart, dict) else {}
    value = cart.get("id") or data.get("cartId") or data.get("cart_id") or data.get("orderId")
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise RuntimeError("KFC useCoupon 未返回购物车订单号")
    return str(value).strip()
