from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.errors import conflict, not_found
from app.utils import iso

logger = logging.getLogger(__name__)


def record_order_confirmation(
    public_id: str,
    visitor_ip: str,
    store_id: str,
    store_address: str,
    *,
    store_name: str = "",
    city_name: str = "",
    image_url: str = "",
) -> dict[str, str]:
    with db.transaction():
        link = db.fetchone(
            "SELECT id,status,coupon_applied_at FROM access_links WHERE public_id=%s FOR UPDATE", (public_id,)
        )
        if not link:
            raise not_found("链接不存在或已失效")
        if link["status"] != "UNUSED" or link.get("coupon_applied_at"):
            raise conflict("该链接当前不可选择餐厅")
        row = db.fetchone(
            """INSERT INTO order_confirmation_logs
               (public_id,visitor_ip,store_id,store_address,production_info,confirmed_at)
               VALUES (%s,%s,%s,%s,%s,now()) RETURNING id,confirmed_at""",
            (
                public_id,
                visitor_ip or "unknown",
                store_id.strip(),
                store_address.strip(),
                Jsonb([]),
            ),
        )
        db.execute(
            """UPDATE access_links SET store_image_url=NULLIF(%s,''),
               redemption_payload=COALESCE(redemption_payload,'{}'::jsonb) || %s,"updatedAt"=now()
               WHERE id=%s""",
            (
                image_url.strip(),
                Jsonb(
                    {
                        "storeCode": store_id.strip(),
                        "confirmationId": row["id"],
                        "selectedStore": {
                            "storeCode": store_id.strip(),
                            "name": store_name.strip(),
                            "cityName": city_name.strip(),
                            "imageUrl": image_url.strip(),
                        },
                    }
                ),
                link["id"],
            ),
        )
    logger.info(
        "order confirmation recorded access_id=%s store=%s city=%s image_saved=%s result=success",
        public_id,
        store_id,
        city_name,
        bool(image_url),
    )
    return {"confirmedAt": iso(row["confirmed_at"]) or ""}


def save_production_info(public_id: str, store_id: str, production_info: list[dict[str, Any]]) -> None:
    link = db.fetchone("SELECT redemption_payload FROM access_links WHERE public_id=%s", (public_id,))
    if not link:
        raise not_found("链接不存在或已失效")
    payload = link.get("redemption_payload") or {}
    confirmation_id = payload.get("confirmationId")

    row = None
    if confirmation_id:
        row = db.fetchone(
            """UPDATE order_confirmation_logs SET production_info=%s
               WHERE id=%s AND public_id=%s AND store_id=%s RETURNING id""",
            (Jsonb(production_info), confirmation_id, public_id, store_id.strip()),
        )
    if not row:
        row = db.fetchone(
            """UPDATE order_confirmation_logs SET production_info=%s
               WHERE id=(
                   SELECT id FROM order_confirmation_logs
                   WHERE public_id=%s AND store_id=%s
                   ORDER BY confirmed_at DESC,id DESC LIMIT 1
               ) RETURNING id""",
            (Jsonb(production_info), public_id, store_id.strip()),
        )
    if not row:
        raise not_found("未找到当前订单确认记录")
    logger.info(
        "order production info saved access_id=%s confirmation=%s count=%s",
        public_id,
        row["id"],
        len(production_info),
    )


def get_latest_production_info(public_id: str) -> list[dict[str, Any]]:
    value = public_id.strip().lower()
    if not value:
        raise not_found("核销任务缺少 public_id")
    row = db.fetchone(
        """SELECT id, production_info
           FROM order_confirmation_logs
           WHERE public_id=%s
           ORDER BY confirmed_at DESC NULLS LAST, id DESC
           LIMIT 1""",
        (value,),
    )
    if not row:
        raise not_found("未找到当前订单确认记录")
    production_info = row.get("production_info")
    if not isinstance(production_info, list):
        raise conflict("当前订单确认记录的餐品选择格式无效")
    logger.info(
        "latest order production info loaded access_id=%s confirmation=%s count=%s",
        value,
        row["id"],
        len(production_info),
    )
    return production_info


def save_unavailable_result(job_id: str, reasons: list[Any]) -> None:
    with db.transaction():
        link = db.fetchone("SELECT * FROM access_links WHERE id=%s FOR UPDATE", (job_id,))
        if not link:
            raise not_found("核销任务不存在")
        payload = link.get("redemption_payload") or {}
        confirmation_id = payload.get("confirmationId")
        row = (
            db.fetchone(
                """UPDATE order_confirmation_logs SET result=%s
               WHERE id=%s AND public_id=%s AND store_id=%s RETURNING id""",
                (Jsonb(reasons), confirmation_id, link["public_id"], link["store_code"]),
            )
            if confirmation_id
            else None
        )
        if not row:
            # Old queued tasks predate restaurant-selection logging.
            row = db.fetchone(
                """INSERT INTO order_confirmation_logs
                   (public_id,visitor_ip,store_id,store_address,production_info,result)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    link["public_id"],
                    "unknown",
                    link["store_code"],
                    str(payload.get("store", {}).get("address") or ""),
                    Jsonb([]),
                    Jsonb(reasons),
                ),
            )
        db.execute(
            "UPDATE access_links SET redemption_payload=COALESCE(redemption_payload,'{}'::jsonb) || %s WHERE id=%s",
            (Jsonb({"confirmationId": row["id"]}), job_id),
        )
    logger.warning(
        "coupon unavailable recorded job=%s confirmation=%s reason_count=%s", job_id, row["id"], len(reasons)
    )
