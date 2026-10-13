from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.errors import not_found, conflict

logger = logging.getLogger(__name__)


def save_browser_submission(job_id: str, response: dict[str, Any], *, succeeded: bool) -> dict[str, Any]:
    """Commit the response and terminal job state together; never resubmit on retry."""
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (job_id,))
        if not link:
            raise not_found("核销任务不存在")
        key = f"browser-submit:{link['public_id']}"
        existing = db.fetchone("SELECT * FROM orders WHERE idempotency_key=%s", (key,))
        if existing:
            return existing
        if link["redemption_status"] != "COUPON_APPLIED" or link["status"] != "PROCESSING":
            raise conflict("核销任务当前不能保存提交结果")
        data = response.get("data")
        data = data if isinstance(data, dict) else {}
        cart = data.get("cart")
        cart = cart if isinstance(cart, dict) else {}
        cashier = data.get("cashierData")
        cashier = cashier if isinstance(cashier, dict) else {}
        payload = link.get("redemption_payload") or {}
        store = cart.get("store") or payload.get("store") or {}
        items = cart.get("items") or payload.get("items") or []
        external_id = str(cashier.get("orderId") or data.get("orderId") or cart.get("id") or link["cart_id"])
        row = db.fetchone(
            """INSERT INTO orders (
                 access_link_public_id,idempotency_key,external_order_id,store_snapshot,items_snapshot,
                 stage,status_code,submit_response,result_unknown
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false) RETURNING *""",
            (
                link["public_id"],
                key,
                external_id,
                Jsonb(store if isinstance(store, dict) else {}),
                Jsonb(items if isinstance(items, list) else []),
                "SUBMITTED" if succeeded else "FAILED",
                str(response.get("code", ""))[:32],
                Jsonb(response),
            ),
        )
        db.execute(
            """UPDATE access_links SET status=%s,redemption_status=%s,redemption_error=%s,
                 redemption_finished_at=now(),coupon_applied_at=now(),internal_locked_at=NULL,
                 ordered_at=CASE WHEN %s THEN now() ELSE ordered_at END,"updatedAt"=now()
               WHERE id=%s""",
            (
                "PICKING_UP" if succeeded else "FAILED",
                "COMPLETED" if succeeded else "FAILED",
                "" if succeeded else "KFC submit 返回下单失败",
                succeeded,
                job_id,
            ),
        )
        db.execute(
            """INSERT INTO access_link_status_history (access_link_id,from_status,to_status,source,reason)
               VALUES (%s,%s,%s,'system','浏览器提交订单结果')""",
            (job_id, link["status"], "PICKING_UP" if succeeded else "FAILED"),
        )
    logger.info("[核销] job=%s stage=order_saved order=%s result=%s", job_id, row["id"], row["stage"])
    return row


def order_view(row: dict[str, Any]) -> dict[str, Any]:
    stage = row["stage"]
    messages = {
        "SUBMITTED": "订单已提交",
        "PREPARING": "餐厅正在制作",
        "READY": "请取餐",
        "COMPLETED": "订单已完结",
        "FAILED": "下单失败",
        "UNKNOWN": "订单正在核对",
    }
    items = row.get("items_snapshot") if isinstance(row.get("items_snapshot"), list) else []
    amount = 0.0
    for item in items:
        try:
            amount += float(item.get("price", 0)) * int(item.get("quantity", 0))
        except Exception:
            pass
    status_name = str(row.get("status_name") or "").strip()
    queue_number = str(row.get("queue_number") or "").strip()
    pickup_code = str(row.get("pickup_code") or "").strip()
    return {
        "id": str(row["id"]),
        "externalOrderId": row.get("external_order_id") or "",
        "orderNumber": row.get("external_order_id") or "",
        "stage": stage,
        "statusName": status_name,
        "statusText": status_name or messages.get(stage, "订单正在核对"),
        "queueNumber": queue_number,
        "pickupCode": pickup_code,
        "store": row.get("store_snapshot") or {},
        "items": items,
        "amount": amount,
        # 取餐号一旦生成就停止查询；仅有 READY 状态但无号码时仍需等待。
        "stopPolling": bool(queue_number or pickup_code) or stage in {"COMPLETED", "FAILED"},
    }


def find_order_for_link(access_id: str) -> dict[str, Any] | None:
    return db.fetchone(
        'SELECT * FROM orders WHERE access_link_public_id=%s ORDER BY "createdAt" DESC LIMIT 1',
        (access_id,),
    )


def get_order_for_link(access_id: str) -> dict[str, Any]:
    order = find_order_for_link(access_id)
    if not order:
        raise not_found("当前链接没有关联订单")
    return order


def save_order_detail(
    access_id: str,
    external_order_id: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    """Insert the first order snapshot and backfill newly available customer details once."""
    with db.transaction():
        link = db.fetchone(
            "SELECT * FROM access_links WHERE public_id=%s FOR UPDATE",
            (access_id,),
        )
        if not link:
            raise not_found("链接不存在或已失效")

        order = db.fetchone(
            'SELECT * FROM orders WHERE access_link_public_id=%s ORDER BY "createdAt" DESC LIMIT 1 FOR UPDATE',
            (link["public_id"],),
        )
        raw_status = status.get("rawStatus") if isinstance(status.get("rawStatus"), dict) else {}

        if order is None:
            store = status.get("store") if isinstance(status.get("store"), dict) else {}
            items = status.get("items") if isinstance(status.get("items"), list) else []
            row = db.fetchone(
                """INSERT INTO orders (
                       access_link_public_id,idempotency_key,external_order_id,
                       store_snapshot,items_snapshot,stage,status_code,status_name,
                       queue_number,pickup_code,raw_status,result_unknown,last_queried_at,
                       "createdAt","updatedAt"
                   ) VALUES (
                       %s,%s,%s,%s,%s,%s,%s,%s,NULLIF(%s,''),NULLIF(%s,''),%s,%s,now(),now(),now()
                   )
                   RETURNING *""",
                (
                    link["public_id"],
                    f"order-detail:{link['public_id']}",
                    external_order_id.strip(),
                    Jsonb(store),
                    Jsonb(items),
                    status["stage"],
                    status.get("statusCode", ""),
                    status.get("statusName", ""),
                    status.get("queueNumber", ""),
                    status.get("pickupCode", ""),
                    Jsonb(raw_status),
                    status["stage"] == "UNKNOWN",
                ),
            )
        else:
            incoming_items = status.get("items") if isinstance(status.get("items"), list) else []
            existing_items = order.get("items_snapshot") if isinstance(order.get("items_snapshot"), list) else []
            refresh_items = bool(incoming_items) and (
                order.get("last_queried_at") is None
                or not existing_items
                or any(not isinstance(item, dict) or "components" not in item for item in existing_items)
            )
            backfilled_components = refresh_items and order.get("last_queried_at") is not None
            row = db.fetchone(
                """UPDATE orders
                   SET store_snapshot=CASE WHEN submit_response IS NOT NULL AND last_queried_at IS NULL THEN %s ELSE store_snapshot END,
                       items_snapshot=CASE WHEN %s THEN %s ELSE items_snapshot END,
                       stage=%s,
                       status_code=COALESCE(NULLIF(%s,''),status_code),
                       status_name=COALESCE(NULLIF(%s,''),status_name),
                       queue_number=COALESCE(NULLIF(%s,''),queue_number),
                       pickup_code=COALESCE(NULLIF(%s,''),pickup_code),
                       raw_status=%s,
                       result_unknown=%s,
                       last_queried_at=now(),
                       "updatedAt"=now()
                   WHERE id=%s
                   RETURNING *""",
                (
                    Jsonb(status.get("store") if isinstance(status.get("store"), dict) else {}),
                    refresh_items,
                    Jsonb(incoming_items),
                    status["stage"],
                    status.get("statusCode", ""),
                    status.get("statusName", ""),
                    status.get("queueNumber", ""),
                    status.get("pickupCode", ""),
                    Jsonb(raw_status),
                    status["stage"] == "UNKNOWN",
                    order["id"],
                ),
            )
            if backfilled_components:
                logger.info(
                    "order meal components backfilled access_id=%s order_id=%s item_count=%s",
                    access_id,
                    order["id"],
                    len(incoming_items),
                )

        next_status = link["status"]
        if link["status"] not in {"FAILED", "COMPLETED"}:
            if status["stage"] == "FAILED":
                next_status = "FAILED"
            elif status["stage"] in {"READY", "COMPLETED"}:
                next_status = "COMPLETED"
            else:
                next_status = "PICKING_UP"

        if next_status != link["status"]:
            db.execute(
                """UPDATE access_links
                   SET status=%s,
                       ordered_at=CASE
                           WHEN %s='PICKING_UP' THEN COALESCE(ordered_at,now())
                           ELSE ordered_at
                       END,
                       completed_at=CASE WHEN %s='COMPLETED' THEN now() ELSE completed_at END,
                       "updatedAt"=now()
                   WHERE id=%s""",
                (next_status, next_status, next_status, link["id"]),
            )
            db.execute(
                """INSERT INTO access_link_status_history (
                       id,access_link_id,from_status,to_status,source,reason,"createdAt","updatedAt"
                   ) VALUES (gen_random_uuid(),%s,%s,%s,'system','肯德基订单状态同步',now(),now())""",
                (link["id"], link["status"], next_status),
            )
    return order_view(row)
