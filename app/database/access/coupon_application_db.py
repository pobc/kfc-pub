from __future__ import annotations

from typing import Any

from app.config import settings
from app.database import db
from app.errors import conflict, not_found
from app.utils import iso


def reserve_coupon_application(
    access_id: str,
    store: dict[str, Any],
    menu_products: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if store.get("status") != "OPEN":
        raise conflict("所选餐厅当前不可使用卡券")
    timeout_seconds = settings().coupon_lock_timeout_seconds
    with db.transaction():
        link = db.fetchone(
            """SELECT *,
                      (internal_locked_at IS NOT NULL
                       AND internal_locked_at >= now() - (%s * interval '1 second')) AS lock_valid
               FROM access_links WHERE public_id=%s FOR UPDATE""",
            (timeout_seconds, access_id),
        )
        if not link:
            raise not_found("链接不存在或已失效")
        if link.get("coupon_applied_at") and link.get("coupon_cart_id"):
            return {"cartId": link["coupon_cart_id"], "appliedAt": iso(link["coupon_applied_at"])}
        if link["status"] != "UNUSED":
            raise conflict("该链接当前不可使用卡券")
        if link.get("lock_valid"):
            raise conflict("卡券正在加入购物车，请勿重复操作")

        menu_by_id = {str(product["id"]): product for product in menu_products}
        if not items:
            raise conflict("至少选择一件餐品")
        for item in items:
            product = menu_by_id.get(str(item["productId"]))
            quantity = item["quantity"]
            if not product:
                raise conflict("餐品不属于当前链接")
            minimum = product.get("minQty", 1)
            maximum = product.get("maxQty", 10)
            if not isinstance(quantity, int) or quantity < minimum or quantity > maximum:
                raise conflict(f"餐品数量应为 {minimum}–{maximum}")

        db.execute(
            'UPDATE access_links SET internal_locked_at=now(), "updatedAt"=now() WHERE id=%s',
            (link["id"],),
        )
    return None


def complete_coupon_application(access_id: str, cart_id: str) -> dict[str, Any]:
    cart_id = cart_id.strip()
    if not cart_id:
        raise conflict("肯德基购物车未返回 cartId")
    timeout_seconds = settings().coupon_lock_timeout_seconds
    with db.transaction():
        link = db.fetchone(
            """SELECT *,
                      (internal_locked_at IS NOT NULL
                       AND internal_locked_at >= now() - (%s * interval '1 second')) AS lock_valid
               FROM access_links WHERE public_id=%s FOR UPDATE""",
            (timeout_seconds, access_id),
        )
        if not link:
            raise not_found("链接不存在或已失效")
        if link.get("coupon_applied_at") and link.get("coupon_cart_id"):
            return {"cartId": link["coupon_cart_id"], "appliedAt": iso(link["coupon_applied_at"])}
        if link["status"] != "UNUSED" or not link.get("lock_valid"):
            raise conflict("卡券使用锁已失效")
        row = db.fetchone(
            """UPDATE access_links
               SET coupon_cart_id=%s,coupon_applied_at=now(),internal_locked_at=NULL,"updatedAt"=now()
               WHERE id=%s RETURNING coupon_cart_id,coupon_applied_at""",
            (cart_id, link["id"]),
        )
    return {"cartId": row["coupon_cart_id"], "appliedAt": iso(row["coupon_applied_at"])}


def fail_coupon_application(access_id: str) -> None:
    """Release only the temporary execution lock; coupon ownership is immutable."""
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE public_id=%s FOR UPDATE", (access_id,))
        if (
            not link
            or link["status"] != "UNUSED"
            or link.get("coupon_applied_at")
            or not link.get("internal_locked_at")
        ):
            return
        db.execute(
            'UPDATE access_links SET internal_locked_at=NULL,"updatedAt"=now() WHERE id=%s',
            (link["id"],),
        )
