from __future__ import annotations

import secrets
from typing import Any

from app.constants import ACCESS_STATUSES
from app.database import db
from app.errors import bad_request, conflict, not_found
from app.utils import iso


def _positive_int(raw: str | None, fallback: int, label: str, maximum: int | None = None) -> int:
    if raw in (None, ""):
        return fallback
    try:
        value = int(raw)
    except ValueError:
        raise bad_request(f"{label}必须是整数")
    if value < 1 or (maximum is not None and value > maximum):
        range_text = f"1 到 {maximum}" if maximum else "大于 0"
        raise bad_request(f"{label}必须是{range_text}之间的整数")
    return value


def access_link_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "accessId": row["public_id"],
        "status": row["status"],
        "couponCode": row.get("coupon_code"),
        "storeCode": row.get("store_code"),
        "productId": str(row["product_id"]) if row.get("product_id") else None,
        "productName": row.get("product_name") or "未关联餐品",
        "internalLockedAt": iso(row.get("internal_locked_at")),
        "orderedAt": iso(row.get("ordered_at")),
        "completedAt": iso(row.get("completed_at")),
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def count_access_links() -> int:
    return db.fetchone("SELECT COUNT(*) AS n FROM access_links")["n"]


def list_access_links(page: str | None, page_size: str | None, query: str | None, status: str | None) -> dict[str, Any]:
    page_num = _positive_int(page, 1, "页码")
    size = _positive_int(page_size, 20, "每页数量", 100)
    keyword = (query or "").strip().lower()
    status_value = (status or "").strip()
    if status_value and status_value not in ACCESS_STATUSES:
        raise bad_request("访问链接状态无效")
    where: list[str] = []
    args: list[Any] = []
    if keyword:
        where.append("(LOWER(l.public_id) LIKE %s OR LOWER(COALESCE(p.name,'')) LIKE %s)")
        args.extend([f"%{keyword}%", f"%{keyword}%"])
    if status_value:
        where.append("l.status = %s")
        args.append(status_value)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    all_total = db.fetchone("SELECT COUNT(*) AS n FROM access_links")["n"]
    total = db.fetchone(
        f"SELECT COUNT(*) AS n FROM access_links l LEFT JOIN products p ON p.id=l.product_id {where_sql}",
        tuple(args),
    )["n"]
    total_pages = max(1, (total + size - 1) // size)
    resolved = min(page_num, total_pages)
    rows = db.fetchall(
        f"""SELECT l.*, p.name AS product_name FROM access_links l LEFT JOIN products p ON p.id=l.product_id
            {where_sql} ORDER BY l."createdAt" DESC, l.id DESC LIMIT %s OFFSET %s""",
        tuple(args + [size, (resolved - 1) * size]),
    )
    return {
        "items": [access_link_view(row) for row in rows],
        "page": resolved,
        "pageSize": size,
        "total": total,
        "totalPages": total_pages,
        "allTotal": all_total,
    }


def create_access_links(product_id: str, count: int = 10) -> list[dict[str, Any]]:
    with db.transaction():
        product = db.fetchone("SELECT id, name FROM products WHERE id=%s", (product_id,))
        if not product:
            raise not_found("关联餐品不存在")
        items: list[dict[str, Any]] = []
        for _ in range(count):
            saved = None
            for _attempt in range(100):
                public_id = secrets.token_hex(8)
                saved = db.fetchone(
                    """INSERT INTO access_links
                       (public_id,status,coupon_code,product_id,platform_id,account_id,imported_at,internal_locked_at,coupon_cart_id,coupon_applied_at,ordered_at,completed_at,"createdAt","updatedAt")
                       VALUES (%s,'UNUSED',NULL,%s,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,now(),now())
                       ON CONFLICT DO NOTHING RETURNING *""",
                    (public_id, product_id),
                )
                if saved:
                    break
            if not saved:
                raise conflict("访问 ID 生成失败，请重试")
            saved["product_name"] = product["name"]
            items.append(access_link_view(saved))
    return items


def update_access_link(link_id: str, product_id: str) -> dict[str, Any]:
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (link_id,))
        if not link:
            raise not_found("访问链接不存在")
        product = db.fetchone("SELECT id,name FROM products WHERE id=%s", (product_id,))
        if not product:
            raise not_found("关联餐品不存在")
        if str(link.get("product_id") or "") == str(product_id):
            link["product_name"] = product["name"]
            return access_link_view(link)
        has_order = db.fetchone(
            "SELECT 1 FROM orders WHERE access_link_public_id=%s LIMIT 1",
            (link["public_id"],),
        )
        if link["status"] != "UNUSED" or link.get("internal_locked_at") or has_order or link.get("coupon_code"):
            raise conflict("链接已进入下单流程，不能修改关联餐品")
        row = db.fetchone(
            'UPDATE access_links SET product_id=%s, "updatedAt"=now() WHERE id=%s RETURNING *',
            (product_id, link_id),
        )
        row["product_name"] = product["name"]
    return access_link_view(row)


def delete_access_link(link_id: str) -> dict[str, bool]:
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (link_id,))
        if not link:
            raise not_found("访问链接不存在")
        has_order = db.fetchone(
            "SELECT 1 FROM orders WHERE access_link_public_id=%s LIMIT 1",
            (link["public_id"],),
        )
        if link.get("internal_locked_at") or has_order or link.get("coupon_code"):
            raise conflict("链接已进入下单流程，不能删除")
        db.execute("DELETE FROM access_link_status_history WHERE access_link_id=%s", (link_id,))
        db.execute("DELETE FROM access_links WHERE id=%s", (link_id,))
    return {"deleted": True}


def get_link(access_id: str) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM access_links WHERE public_id=%s", (access_id,))
    if not row:
        raise not_found("链接不存在或已失效")
    if not row.get("product_id"):
        raise not_found("链接尚未绑定餐品")
    return row


def linked_products(access_id: str) -> list[dict[str, Any]]:
    link = get_link(access_id)
    rows = db.fetchall("SELECT * FROM products WHERE id=%s", (link["product_id"],))
    if not rows:
        raise not_found("链接绑定的餐品不存在")
    return [
        {
            "id": str(row["id"]),
            "description": row.get("description") or "",
            "imageUrl": row.get("image_url") or "",
            "parentLinkId": row.get("parent_link_id") or "",
            "specLinkIds": row.get("spec_link_ids") or [],
            "condimentLinkIds": row.get("condiment_link_ids") or [],
            "minQty": 1,
            "maxQty": 10,
        }
        for row in rows
    ]


def public_link_view(access_id: str) -> dict[str, Any]:
    link = get_link(access_id)
    route = {
        "UNUSED": "SELECT_STORE",
        "PICKING_UP": "PICKUP_STATUS",
        "FAILED": "ORDER_FAILED",
        "COMPLETED": "ORDER_COMPLETED",
    }
    data = {
        "accessId": access_id,
        "status": link["status"],
        "allowedPage": "COUPON_APPLIED" if link.get("coupon_applied_at") else route[link["status"]],
    }
    if link["status"] == "UNUSED":
        data.update(
            {
                "couponApplied": bool(link.get("coupon_applied_at") and link.get("coupon_cart_id")),
                "couponAppliedAt": iso(link.get("coupon_applied_at")) or "",
            }
        )
    return data
