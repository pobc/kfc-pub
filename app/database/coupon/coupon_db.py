from __future__ import annotations

from typing import Any

from app.database import db
from app.database.platform.platform_db import ensure_kfc_source
from app.errors import conflict, not_found
from app.serializers import batch_view, coupon_view
from app.utils import now


def count_coupons() -> int:
    return db.fetchone("SELECT COUNT(*) AS n FROM coupons")["n"]


def list_coupons() -> list[dict[str, Any]]:
    rows = db.fetchall('SELECT * FROM coupons ORDER BY imported_at DESC, "createdAt" DESC')
    return [coupon_view(row) for row in rows]


def update_coupon(coupon_id: str, coupon_code: str) -> dict[str, Any]:
    code = coupon_code.strip()
    with db.transaction():
        coupon = db.fetchone("SELECT * FROM coupons WHERE id = %s FOR UPDATE", (coupon_id,))
        if not coupon:
            raise not_found("卡券不存在")
        if db.fetchone(
            "SELECT 1 FROM coupon_assignment_logs WHERE coupon_id = %s LIMIT 1",
            (coupon_id,),
        ):
            raise conflict("卡券已分配，不能修改")
        duplicate = db.fetchone(
            "SELECT id FROM coupons WHERE platform_id = %s AND coupon_code = %s",
            (coupon["platform_id"], code),
        )
        if duplicate and str(duplicate["id"]) != str(coupon_id):
            raise conflict("该平台已存在此卡券码")
        row = db.fetchone(
            'UPDATE coupons SET coupon_code = %s, "updatedAt" = now() WHERE id = %s RETURNING *',
            (code, coupon_id),
        )
    return coupon_view(row)


def reset_coupon(coupon_id: str) -> dict[str, Any]:
    row = db.fetchone(
        """UPDATE coupons
           SET local_status='wait', "updatedAt"=now()
           WHERE id=%s
           RETURNING *""",
        (coupon_id,),
    )
    if not row:
        raise not_found("卡券不存在")
    return coupon_view(row)


def delete_coupon(coupon_id: str) -> dict[str, bool]:
    with db.transaction():
        coupon = db.fetchone("SELECT * FROM coupons WHERE id = %s FOR UPDATE", (coupon_id,))
        if not coupon:
            raise not_found("卡券不存在")
        if db.fetchone(
            "SELECT 1 FROM coupon_assignment_logs WHERE coupon_id = %s LIMIT 1",
            (coupon_id,),
        ):
            raise conflict("卡券已分配，不能删除")
        db.execute("DELETE FROM coupons WHERE id = %s", (coupon_id,))
        db.execute(
            """UPDATE coupon_import_batches SET success_count = GREATEST(0, success_count - 1),
               total_rows = GREATEST(failed_count, total_rows - 1), "updatedAt" = now() WHERE id = %s""",
            (coupon["batch_id"],),
        )
    return {"deleted": True}


def import_manual_coupons(product_id: str, batch_name: str, codes: list[str]) -> dict[str, Any]:
    with db.transaction():
        product = db.fetchone("SELECT id FROM products WHERE id = %s", (product_id,))
        if not product:
            raise not_found("关联餐品不存在")
        platform_id, account_id = ensure_kfc_source()
        imported_at = now()
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches
               (id, name, platform_id, account_id, product_id, total_rows, success_count, failed_count, imported_at, "createdAt", "updatedAt")
               VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, 0, 0, %s, now(), now()) RETURNING *""",
            (batch_name.strip(), platform_id, account_id, product_id, len(codes), imported_at),
        )
        items: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw_code in enumerate(codes, start=1):
            code = raw_code.strip()
            if not code:
                failures.append({"row": index, "couponCode": code, "reason": "卡券码为空"})
                continue
            if code in seen:
                failures.append({"row": index, "couponCode": code, "reason": "导入文件内卡券码重复"})
                continue
            seen.add(code)
            row = db.fetchone(
                """INSERT INTO coupons
                   (coupon_code, local_status, product_name, kfc_status, platform_id, account_id, product_id,
                    batch_id, imported_at, last_seen_at, "createdAt", "updatedAt")
                   VALUES (%s, 'wait', '', 'AVAILABLE', %s, %s, %s, %s, %s, %s, now(), now())
                   ON CONFLICT (platform_id, coupon_code) DO NOTHING
                   RETURNING *""",
                (code, platform_id, account_id, product_id, batch["id"], imported_at, imported_at),
            )
            if not row:
                failures.append({"row": index, "couponCode": code, "reason": "该平台已导入此卡券码"})
                continue
            items.append(coupon_view(row))
        db.execute(
            'UPDATE coupon_import_batches SET success_count = %s, failed_count = %s, "updatedAt" = now() WHERE id = %s',
            (len(items), len(failures), batch["id"]),
        )
        batch = db.fetchone(
            "SELECT * FROM coupon_import_batches WHERE id = %s",
            (batch["id"],),
        )
    return {"batch": batch_view(batch), "items": items, "failures": failures}


def assigned_coupon_reference(redemption_uuid: str) -> dict[str, Any]:
    link = db.fetchone(
        "SELECT id, coupon_code, platform_id, account_id, product_id FROM access_links WHERE public_id = %s",
        (redemption_uuid.strip().lower(),),
    )
    if not link or not link.get("coupon_code"):
        raise not_found("当前链接尚未分配卡券")
    coupon = db.fetchone(
        """SELECT c.coupon_code, c."createdAt", c.product_name
           FROM coupon_assignment_logs l
           JOIN coupons c ON c.id=l.coupon_id
           WHERE l.access_link_id=%s
           ORDER BY l.assigned_at DESC,l.id DESC LIMIT 1""",
        (link["id"],),
    )
    if not coupon:
        query = """SELECT coupon_code, "createdAt", product_name FROM coupons
                   WHERE coupon_code=%s"""
        args: list[Any] = [link["coupon_code"]]
        if link.get("platform_id"):
            query += " AND platform_id=%s"
            args.append(link["platform_id"])
        if link.get("account_id"):
            query += " AND account_id=%s"
            args.append(link["account_id"])
        if link.get("product_id"):
            query += " AND product_id=%s"
            args.append(link["product_id"])
        rows = db.fetchall(query, tuple(args))
        if len(rows) > 1:
            raise conflict("当前链接关联的卡券记录不唯一")
        coupon = rows[0] if rows else None
    if not coupon:
        raise not_found("当前链接关联的卡券不存在")
    if coupon["coupon_code"] != link["coupon_code"]:
        raise conflict("卡券分配记录与访问链接不一致")
    return {
        "couponCode": coupon["coupon_code"],
        "placedAt": coupon["createdAt"],
        "productName": coupon.get("product_name") or "",
    }
