from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.errors import bad_request, conflict, not_found
from app.utils import iso

ACTIVE_STATUSES = {"WAITING", "PROCESSING", "CART_CREATED", "COUPON_APPLIED"}
STALE_EXECUTION_STATUSES = {"PROCESSING", "CART_CREATED", "COUPON_APPLIED"}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("redemption_payload")
    return payload if isinstance(payload, dict) else {}


def _view(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row)
    selection = payload.get("selectedStore") or {}
    store = payload.get("store") or {}
    return {
        "id": str(row["id"]),
        "accessLinkId": str(row["id"]),
        "publicId": row.get("public_id"),
        "couponId": str(row.get("coupon_id") or ""),
        "productId": str(row.get("coupon_product_id") or row.get("product_id") or ""),
        "couponCode": row.get("coupon_code") or "",
        "accountId": str(row["account_id"]) if row.get("account_id") else "",
        "status": "PAUSED"
        if row.get("redemption_status") == "PROCESSING" and payload.get("browserCheckpoint")
        else row.get("redemption_status") or "",
        "storeCode": str(row.get("store_code") or payload.get("storeCode") or ""),
        "storeName": str(selection.get("name") or store.get("storename") or store.get("storeName") or ""),
        "cityName": str(selection.get("cityName") or store.get("cityName") or store.get("cityname") or ""),
        "storeImageUrl": str(row.get("store_image_url") or ""),
        "storeSnapshot": payload.get("store") if isinstance(payload.get("store"), dict) else {},
        "packType": row.get("pickup_type") or payload.get("packType") or 1,
        "itemsSnapshot": payload.get("items") if isinstance(payload.get("items"), list) else [],
        "cartId": row.get("cart_id") or row.get("coupon_cart_id") or "",
        "upstreamCode": "",
        "errorMessage": row.get("redemption_error") or "",
        "workerStartedAt": iso(row.get("redemption_started_at")),
        "finishedAt": iso(row.get("redemption_finished_at")),
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def _latest_coupon_assignment(access_link_id: Any) -> dict[str, Any] | None:
    return db.fetchone(
        """SELECT c.id AS coupon_id, c.product_id AS coupon_product_id
           FROM coupon_assignment_logs l
           JOIN coupons c ON c.id = l.coupon_id
           WHERE l.access_link_id=%s
           ORDER BY l.assigned_at DESC,l.id DESC LIMIT 1""",
        (access_link_id,),
    )


def _view_with_coupon(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("coupon_id") or not row.get("coupon_product_id"):
        assignment = _latest_coupon_assignment(row["id"])
        if not assignment:
            return _view(row)
        row = dict(row)
        row.update(assignment)
    return _view(row)


def _product_ids_for_link(link: dict[str, Any]) -> list[Any]:
    if link.get("product_id"):
        row = db.fetchone("SELECT id FROM products WHERE id=%s", (link["product_id"],))
        if not row:
            raise conflict("当前链接绑定的餐品不存在")
        return [row["id"]]
    if not link.get("product_group_id"):
        raise conflict("当前链接未绑定可分配卡券的餐品")
    rows = db.fetchall(
        """SELECT p.id
           FROM product_group_items gi
           JOIN products p ON p.id=gi.product_id
           WHERE gi.product_group_id=%s""",
        (link["product_group_id"],),
    )
    ids = [row["id"] for row in rows]
    if not ids:
        raise conflict("当前链接未绑定可分配卡券的餐品")
    return ids


def _existing_coupon(link: dict[str, Any], product_ids: list[Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    log = db.fetchone(
        """SELECT * FROM coupon_assignment_logs
           WHERE access_link_id=%s ORDER BY assigned_at DESC,id DESC LIMIT 1""",
        (link["id"],),
    )
    if log:
        coupon = db.fetchone("SELECT * FROM coupons WHERE id=%s FOR UPDATE", (log["coupon_id"],))
        if not coupon or coupon["coupon_code"] != link["coupon_code"]:
            raise conflict("访问链接固定卡券与分配记录不一致")
        return coupon, log

    query = "SELECT * FROM coupons WHERE coupon_code=%s AND product_id = ANY(%s)"
    args: list[Any] = [link["coupon_code"], product_ids]
    if link.get("platform_id"):
        query += " AND platform_id=%s"
        args.append(link["platform_id"])
    if link.get("account_id"):
        query += " AND account_id=%s"
        args.append(link["account_id"])
    rows = db.fetchall(query + " FOR UPDATE", tuple(args))
    if len(rows) > 1:
        raise conflict("访问链接关联的卡券记录不唯一")
    if not rows:
        raise not_found("访问链接关联的卡券不存在")
    return rows[0], None


def _allocate_coupon(
    link: dict[str, Any],
    product_ids: list[Any],
    visitor_ip: str,
) -> dict[str, Any]:
    if link.get("coupon_code"):
        coupon, log = _existing_coupon(link, product_ids)
        if coupon["local_status"] not in {"wait", "assigned"}:
            raise conflict("访问链接关联的卡券当前不可核销")
        if coupon["local_status"] == "wait":
            db.execute(
                "UPDATE coupons SET local_status='assigned',\"updatedAt\"=now() WHERE id=%s",
                (coupon["id"],),
            )
        if not log:
            db.fetchone(
                """INSERT INTO coupon_assignment_logs (access_link_id,coupon_id,visitor_ip,assigned_at)
                   VALUES (%s,%s,%s,now()) RETURNING *""",
                (link["id"], coupon["id"], visitor_ip),
            )
        return coupon

    query = "SELECT * FROM coupons WHERE kfc_status='AVAILABLE' AND local_status='wait' AND product_id = ANY(%s)"
    args: list[Any] = [product_ids]
    if link.get("platform_id"):
        query += " AND platform_id=%s"
        args.append(link["platform_id"])
    if link.get("account_id"):
        query += " AND account_id=%s"
        args.append(link["account_id"])
    coupon = db.fetchone(
        query + ' ORDER BY "createdAt" ASC,id ASC FOR UPDATE SKIP LOCKED LIMIT 1',
        tuple(args),
    )
    if not coupon:
        raise conflict("暂无可分配的有效卡券")

    db.execute(
        "UPDATE coupons SET local_status='assigned',\"updatedAt\"=now() WHERE id=%s",
        (coupon["id"],),
    )
    db.execute(
        """UPDATE access_links
           SET coupon_code=%s,platform_id=%s,account_id=%s,"updatedAt"=now()
           WHERE id=%s""",
        (coupon["coupon_code"], coupon["platform_id"], coupon["account_id"], link["id"]),
    )
    db.fetchone(
        """INSERT INTO coupon_assignment_logs (access_link_id,coupon_id,visitor_ip,assigned_at)
           VALUES (%s,%s,%s,now()) RETURNING *""",
        (link["id"], coupon["id"], visitor_ip),
    )
    return coupon


def enqueue(
    access_id: str,
    *,
    store_code: str,
    store_snapshot: dict[str, Any],
    pack_type: int,
    items: list[dict[str, Any]],
    visitor_ip: str,
) -> dict[str, Any]:
    normalized = access_id.strip().lower()
    ip = visitor_ip.strip()
    if not re.fullmatch(r"[0-9a-f]{16}", normalized):
        raise bad_request("访问 ID 必须是 16 位十六进制字符串")
    if not ip or len(ip) > 64:
        raise bad_request("访问者 IP 不能为空且不能超过 64 个字符")
    if not store_code.strip() or not store_snapshot:
        raise conflict("餐厅信息已失效，请重新选择餐厅")
    if pack_type not in (1, 2):
        raise conflict("取餐方式无效")

    with db.transaction():
        link = db.fetchone(
            "SELECT * FROM access_links WHERE public_id=%s FOR UPDATE",
            (normalized,),
        )
        if not link:
            raise not_found("链接不存在或已失效")

        if link["status"] == "PROCESSING":
            if link.get("redemption_status") in ACTIVE_STATUSES:
                return _view_with_coupon(link)
            raise conflict("链接核销状态异常，请联系管理员")
        if link.get("coupon_applied_at") and link.get("coupon_cart_id"):
            return {
                "id": str(link["id"]),
                "accessLinkId": str(link["id"]),
                "status": "COMPLETED",
                "appliedAt": iso(link["coupon_applied_at"]),
            }
        if link["status"] != "UNUSED":
            raise conflict("该链接当前不可使用卡券")

        product_ids = _product_ids_for_link(link)
        selection = _payload(link).get("selectedStore") or {}
        if selection and selection.get("storeCode") != store_code.strip():
            raise conflict("提交餐厅与当前选择不一致，请重新选择餐厅")
        coupon = _allocate_coupon(link, product_ids, ip)
        confirmation = db.fetchone(
            "SELECT id FROM order_confirmation_logs WHERE public_id=%s AND store_id=%s ORDER BY confirmed_at DESC,id DESC LIMIT 1",
            (normalized, store_code.strip()),
        )
        payload = {
            "selectedStore": selection,
            "confirmationId": confirmation["id"] if confirmation else None,
            "storeCode": store_code.strip(),
            "store": store_snapshot,
            "packType": pack_type,
            "items": items,
        }
        row = db.fetchone(
            """UPDATE access_links
               SET status='PROCESSING',coupon_code=%s,platform_id=%s,account_id=%s,
                   redemption_status='WAITING',redemption_payload=%s,pickup_type=%s,redemption_error='',
                   redemption_started_at=NULL,redemption_finished_at=NULL,
                   cart_id=NULL,coupon_cart_id=NULL,coupon_applied_at=NULL,internal_locked_at=NULL,"updatedAt"=now()
               WHERE id=%s RETURNING *""",
            (
                coupon["coupon_code"],
                coupon["platform_id"],
                coupon["account_id"],
                Jsonb(payload),
                pack_type,
                link["id"],
            ),
        )
        row["coupon_id"] = coupon["id"]
        row["coupon_product_id"] = coupon["product_id"]
    return _view(row)


def claim_next() -> dict[str, Any] | None:
    with db.transaction():
        row = db.fetchone(
            """SELECT * FROM access_links
               WHERE status='PROCESSING' AND redemption_status='WAITING'
               ORDER BY "updatedAt" ASC,id ASC
               FOR UPDATE SKIP LOCKED LIMIT 1"""
        )
        if not row:
            return None
        assignment = _latest_coupon_assignment(row["id"])
        if not assignment:
            db.execute(
                """UPDATE access_links
                   SET status='FAILED',redemption_status='FAILED',redemption_error='核销任务缺少卡券分配记录',
                       redemption_finished_at=now(),"updatedAt"=now()
                   WHERE id=%s""",
                (row["id"],),
            )
            return None
        row = db.fetchone(
            """UPDATE access_links
               SET redemption_status='PROCESSING',redemption_started_at=now(),redemption_error='',"updatedAt"=now()
               WHERE id=%s RETURNING *""",
            (row["id"],),
        )
        row.update(assignment)
    return _view(row)


def worker_queue_diagnostics() -> dict[str, Any]:
    """Read only operational metadata; never return coupon codes or auth state."""
    queue = db.fetchone(
        """SELECT count(*) FILTER (WHERE redemption_status='WAITING') AS waiting_count,
                  (array_agg(id ORDER BY "updatedAt",id) FILTER (WHERE redemption_status='WAITING'))[1] AS next_job_id
           FROM access_links WHERE status='PROCESSING'"""
    )
    paused = db.fetchone(
        """SELECT id,redemption_payload->'browserCheckpoint'->>'stage' AS stage,
                  redemption_payload->'browserCheckpoint'->>'verification' AS verification,
                  redemption_payload->'browserCheckpoint'->>'errorType' AS error_type
           FROM access_links WHERE status='PROCESSING' AND redemption_payload ? 'browserCheckpoint'
           ORDER BY id LIMIT 1"""
    )
    return {"waiting": int(queue["waiting_count"]), "nextJobId": queue["next_job_id"], "paused": paused}


def mark_browser_paused(job_id: str, *, checkpoint: dict[str, Any]) -> dict[str, Any]:
    row = db.fetchone(
        """UPDATE access_links
           SET redemption_payload=COALESCE(redemption_payload,'{}'::jsonb) || %s,
               redemption_error='',"updatedAt"=now()
           WHERE id=%s AND status='PROCESSING' AND redemption_status='PROCESSING' RETURNING *""",
        (Jsonb({"browserCheckpoint": checkpoint}), job_id),
    )
    if not row:
        raise conflict("核销任务当前不能暂停浏览器流程")
    return _view_with_coupon(row)


def mark_cart_created(job_id: str, cart_id: str) -> dict[str, Any]:
    row = db.fetchone(
        """UPDATE access_links
           SET redemption_status='CART_CREATED',cart_id=%s,coupon_cart_id=%s,"updatedAt"=now()
           WHERE id=%s AND redemption_status='PROCESSING' RETURNING *""",
        (cart_id.strip(), cart_id.strip(), job_id),
    )
    if not row:
        raise conflict("核销任务当前不能记录购物车")
    return _view_with_coupon(row)


def mark_coupon_applied(job_id: str, *, cart_id: str, upstream_code: Any = 0) -> dict[str, Any]:
    row = db.fetchone(
        """UPDATE access_links
           SET redemption_status='COUPON_APPLIED',coupon_cart_id=%s,redemption_error='',"updatedAt"=now()
           WHERE id=%s AND redemption_status='CART_CREATED' RETURNING *""",
        (cart_id.strip(), job_id),
    )
    if not row:
        raise conflict("核销任务当前不能记录用券成功")
    return _view_with_coupon(row)


def mark_completed(job_id: str, *, upstream_code: Any = 0) -> dict[str, Any]:
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (job_id,))
        if not link:
            raise not_found("核销任务不存在")
        if link.get("redemption_status") == "COMPLETED":
            return _view_with_coupon(link)
        if link.get("redemption_status") != "COUPON_APPLIED":
            raise conflict("核销任务当前不能完成")

        link = db.fetchone(
            """UPDATE access_links
               SET status='UNUSED',redemption_status='COMPLETED',redemption_error='',
                   redemption_finished_at=now(),coupon_applied_at=now(),internal_locked_at=NULL,"updatedAt"=now()
               WHERE id=%s RETURNING *""",
            (job_id,),
        )
    return _view_with_coupon(link)


def _finish_with_error(
    job_id: str,
    *,
    status: str,
    error_message: str,
    upstream_code: Any = "",
) -> dict[str, Any]:
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (job_id,))
        if not link:
            raise not_found("核销任务不存在")
        if link.get("redemption_status") in {"COMPLETED", "FAILED", "UNKNOWN"}:
            return _view_with_coupon(link)

        link = db.fetchone(
            """UPDATE access_links
               SET status='FAILED',redemption_status=%s,redemption_error=%s,
                   redemption_finished_at=now(),internal_locked_at=NULL,"updatedAt"=now()
               WHERE id=%s RETURNING *""",
            (status, error_message[:2000], job_id),
        )
    return _view_with_coupon(link)


def mark_failed(job_id: str, *, error_message: str, upstream_code: Any = "") -> dict[str, Any]:
    return _finish_with_error(
        job_id,
        status="FAILED",
        error_message=error_message,
        upstream_code=upstream_code,
    )


def mark_unknown(job_id: str, *, error_message: str, upstream_code: Any = "") -> dict[str, Any]:
    return _finish_with_error(
        job_id,
        status="UNKNOWN",
        error_message=error_message,
        upstream_code=upstream_code,
    )


def recover_stale_jobs(stale_seconds: int = 60) -> int:
    with db.transaction():
        rows = db.fetchall(
            """SELECT * FROM access_links
               WHERE status='PROCESSING'
                 AND redemption_status = ANY(%s)
                 AND COALESCE(redemption_started_at,"updatedAt") < now() - (%s * interval '1 second')
               ORDER BY id ASC FOR UPDATE""",
            (list(STALE_EXECUTION_STATUSES), stale_seconds),
        )
        for row in rows:
            db.execute(
                """UPDATE access_links
                   SET status='FAILED',redemption_status='FAILED',
                       redemption_error='核销任务超过60秒，已停止执行，请核对上游结果',
                       redemption_finished_at=now(),internal_locked_at=NULL,"updatedAt"=now()
                   WHERE id=%s""",
                (row["id"],),
            )
    return len(rows)
