from __future__ import annotations

from typing import Any

from app.constants import OTHER_PRODUCT_GROUP, REDEMPTION_STATUSES
from app.database import db
from app.errors import bad_request
from app.utils import iso


def list_redemptions(
    page: int = 1, page_size: int = 20, product_id: str | None = None, status: str | None = None
) -> dict[str, Any]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise bad_request("分页参数无效")
    if status and status not in REDEMPTION_STATUSES:
        raise bad_request("核销状态无效")

    where: list[str] = []
    args: list[Any] = []
    if product_id == OTHER_PRODUCT_GROUP:
        where.append("l.product_id IS NULL")
    elif product_id:
        where.append("l.product_id=%s")
        args.append(product_id)

    redeemed = "COALESCE(l.redemption_status,'')='COMPLETED'"
    invalid = "COALESCE(l.redemption_status,'') IN ('FAILED','UNKNOWN')"
    if status == "REDEEMED":
        where.append(redeemed)
    elif status == "INVALID":
        where.append(invalid)
    elif status == "UNREDEEMED":
        where.append(f"NOT ({redeemed}) AND NOT ({invalid})")

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    total = db.fetchone(
        f"SELECT COUNT(*) AS n FROM access_links l {where_sql}",
        tuple(args),
    )["n"]
    total_pages = max(1, (total + page_size - 1) // page_size)
    resolved = min(page, total_pages)

    rows = db.fetchall(
        f"""SELECT l.*, p.name AS linked_product_name,
            c.id AS assigned_coupon_id, c.product_id AS coupon_product_id, c.product_name AS coupon_product_name,
            c.platform_id AS coupon_platform_id, c.account_id AS coupon_account_id,
            cp.name AS coupon_platform_name, ca.name AS coupon_account_name
            FROM access_links l
            LEFT JOIN products p ON p.id=l.product_id
            LEFT JOIN LATERAL (
                SELECT cal.coupon_id
                FROM coupon_assignment_logs cal
                WHERE cal.access_link_id=l.id
                ORDER BY cal.assigned_at DESC, cal.id DESC
                LIMIT 1
            ) assignment ON true
            LEFT JOIN coupons c ON c.id=assignment.coupon_id
            LEFT JOIN platforms cp ON cp.id=c.platform_id
            LEFT JOIN platform_accounts ca ON ca.id=c.account_id
            {where_sql}
            ORDER BY COALESCE(
                l.redemption_finished_at,
                l.redemption_started_at,
                l."updatedAt",
                l."createdAt"
            ) DESC, l.id DESC
            LIMIT %s OFFSET %s""",
        tuple(args + [page_size, (resolved - 1) * page_size]),
    )

    counts = db.fetchall("SELECT product_id, COUNT(*) AS n FROM access_links GROUP BY product_id")
    items: list[dict[str, Any]] = []
    for link in rows:
        redemption_state = str(link.get("redemption_status") or "")
        if redemption_state == "COMPLETED":
            redemption_status = "REDEEMED"
        elif redemption_state in {"FAILED", "UNKNOWN"}:
            redemption_status = "INVALID"
        else:
            redemption_status = "UNREDEEMED"

        payload = link.get("redemption_payload") if isinstance(link.get("redemption_payload"), dict) else {}
        snapshot = payload.get("store") if isinstance(payload.get("store"), dict) else {}

        def snap(*names: str) -> str:
            for name in names:
                if snapshot.get(name) not in (None, ""):
                    return str(snapshot[name])
            return ""

        product_value = link.get("product_id") or link.get("coupon_product_id")
        platform_value = link.get("coupon_platform_id") or link.get("platform_id")
        account_value = link.get("coupon_account_id") or link.get("account_id")
        items.append(
            {
                "id": str(link["id"]),
                "uuid": link["public_id"],
                "couponId": str(link["assigned_coupon_id"]) if link.get("assigned_coupon_id") else None,
                "couponCode": link.get("coupon_code") or "",
                "productId": str(product_value) if product_value else None,
                "productName": link.get("linked_product_name") or link.get("coupon_product_name") or "",
                "platformId": str(platform_value) if platform_value else None,
                "platformName": link.get("coupon_platform_name") or "",
                "accountId": str(account_value) if account_value else None,
                "accountName": link.get("coupon_account_name") or "",
                "status": redemption_status,
                "importedAt": iso(link.get("createdAt")),
                # 管理页展示的是本次下单时间；旧数据没有开始时间时回退到链接创建时间。
                "orderedAt": iso(link.get("redemption_started_at") or link.get("createdAt")),
                "redeemedAt": iso(link.get("coupon_applied_at") or link.get("redemption_finished_at"))
                if redemption_status == "REDEEMED"
                else None,
                "storeCode": snap("storeCode", "storecode", "code", "id") or str(payload.get("storeCode") or ""),
                "storeName": snap("storeName", "storename", "name"),
                "storeAddress": snap("storeAddress", "address"),
            }
        )

    product_counts = {
        str(row["product_id"]) if row["product_id"] else OTHER_PRODUCT_GROUP: row["n"] for row in counts
    }
    return {
        "items": items,
        "page": resolved,
        "pageSize": page_size,
        "total": total,
        "totalPages": total_pages,
        "allTotal": sum(row["n"] for row in counts),
        "productCounts": product_counts,
    }
