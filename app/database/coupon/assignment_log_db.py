from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.database import db
from app.errors import bad_request, conflict, not_found
from app.utils import iso


def _product_ids_for_link(link: dict[str, Any]) -> list[Any]:
    if link.get("product_id"):
        return [link["product_id"]]
    if not link.get("product_group_id"):
        raise conflict("当前链接未绑定可分配卡券的餐品")
    rows = db.fetchall(
        "SELECT product_id FROM product_group_items WHERE product_group_id=%s",
        (link["product_group_id"],),
    )
    ids = [row["product_id"] for row in rows]
    if not ids:
        raise conflict("当前链接未绑定可分配卡券的餐品")
    return ids


def _legacy_coupon_for_link(link: dict[str, Any]) -> dict[str, Any] | None:
    product_ids = _product_ids_for_link(link)
    query = "SELECT * FROM coupons WHERE coupon_code=%s AND product_id = ANY(%s)"
    args: list[Any] = [link["coupon_code"], product_ids]
    if link.get("platform_id"):
        query += " AND platform_id=%s"
        args.append(link["platform_id"])
    if link.get("account_id"):
        query += " AND account_id=%s"
        args.append(link["account_id"])
    query += " FOR UPDATE"
    rows = db.fetchall(query, tuple(args))
    if len(rows) > 1:
        raise conflict("访问链接关联的卡券记录不唯一")
    return rows[0] if rows else None


def assign_coupon(redemption_uuid: str, visitor_ip: str) -> dict[str, Any]:
    normalized = redemption_uuid.strip().lower()
    ip = visitor_ip.strip()
    if not re.fullmatch(r"[0-9a-f]{16}", normalized):
        raise bad_request("核销 UUID 必须是 16 位十六进制字符串")
    if not ip or len(ip) > 64:
        raise bad_request("访问者 IP 不能为空且不能超过 64 个字符")

    timeout_seconds = settings().coupon_lock_timeout_seconds
    with db.transaction():
        link = db.fetchone(
            """SELECT *,
                      (internal_locked_at IS NOT NULL
                       AND internal_locked_at >= now() - (%s * interval '1 second')) AS lock_valid
               FROM access_links WHERE public_id=%s FOR UPDATE""",
            (timeout_seconds, normalized),
        )
        if not link:
            raise not_found("核销 UUID 不存在")
        if link["status"] != "UNUSED":
            raise conflict("当前链接状态不允许分配卡券")
        if not link.get("lock_valid"):
            raise conflict("用券操作锁无效或已超时，请重新发起")

        if link.get("coupon_code"):
            log = db.fetchone(
                "SELECT * FROM coupon_assignment_logs WHERE access_link_id=%s ORDER BY assigned_at DESC,id DESC LIMIT 1",
                (link["id"],),
            )
            if log:
                coupon = db.fetchone(
                    "SELECT * FROM coupons WHERE id=%s FOR UPDATE",
                    (log["coupon_id"],),
                )
                if coupon and coupon["coupon_code"] != link["coupon_code"]:
                    raise conflict("访问链接固定卡券与分配记录不一致")
            else:
                coupon = _legacy_coupon_for_link(link)
            if not coupon:
                raise not_found("访问链接关联的卡券不存在")
            if coupon["local_status"] == "wait":
                db.execute(
                    "UPDATE coupons SET local_status='assigned', \"updatedAt\"=now() WHERE id=%s",
                    (coupon["id"],),
                )
            if not log:
                log = db.fetchone(
                    "INSERT INTO coupon_assignment_logs (access_link_id,coupon_id,visitor_ip,assigned_at) VALUES (%s,%s,%s,now()) RETURNING *",
                    (link["id"], coupon["id"], ip),
                )
        else:
            product_ids = _product_ids_for_link(link)
            query = (
                "SELECT * FROM coupons WHERE kfc_status='AVAILABLE' AND local_status='wait' AND product_id = ANY(%s)"
            )
            args: list[Any] = [product_ids]
            if link.get("platform_id"):
                query += " AND platform_id=%s"
                args.append(link["platform_id"])
            if link.get("account_id"):
                query += " AND account_id=%s"
                args.append(link["account_id"])
            query += ' ORDER BY "createdAt" ASC,id ASC FOR UPDATE SKIP LOCKED LIMIT 1'
            coupon = db.fetchone(query, tuple(args))
            if not coupon:
                raise conflict("暂无可分配的有效卡券")
            db.execute(
                "UPDATE coupons SET local_status='assigned', \"updatedAt\"=now() WHERE id=%s",
                (coupon["id"],),
            )
            db.execute(
                'UPDATE access_links SET coupon_code=%s, "updatedAt"=now() WHERE id=%s',
                (coupon["coupon_code"], link["id"]),
            )
            log = db.fetchone(
                "INSERT INTO coupon_assignment_logs (access_link_id,coupon_id,visitor_ip,assigned_at) VALUES (%s,%s,%s,now()) RETURNING *",
                (link["id"], coupon["id"], ip),
            )
    return {
        "id": str(log["id"]),
        "redemptionUuid": normalized,
        "accessLinkId": str(log["access_link_id"]),
        "couponId": str(log["coupon_id"]),
        "visitorIp": log["visitor_ip"],
        "assignedAt": iso(log["assigned_at"]),
    }
