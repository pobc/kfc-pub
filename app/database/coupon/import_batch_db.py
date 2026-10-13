from __future__ import annotations

from typing import Any

from app.database import db
from app.errors import conflict, not_found
from app.serializers import batch_view


def list_coupon_batches() -> list[dict[str, Any]]:
    return [batch_view(row) for row in db.fetchall("SELECT * FROM coupon_import_batches ORDER BY imported_at DESC")]


def update_coupon_batch(batch_id: str, name: str) -> dict[str, Any]:
    row = db.fetchone(
        'UPDATE coupon_import_batches SET name = %s, "updatedAt" = now() WHERE id = %s RETURNING *',
        (name.strip(), batch_id),
    )
    if not row:
        raise not_found("导入批次不存在")
    return batch_view(row)


def delete_coupon_batch(batch_id: str) -> dict[str, Any]:
    with db.transaction():
        batch = db.fetchone(
            "SELECT * FROM coupon_import_batches WHERE id = %s FOR UPDATE",
            (batch_id,),
        )
        if not batch:
            raise not_found("导入批次不存在")
        coupons = db.fetchall(
            "SELECT id, coupon_code FROM coupons WHERE batch_id = %s",
            (batch_id,),
        )
        for coupon in coupons:
            if db.fetchone(
                "SELECT 1 FROM access_links WHERE coupon_code = %s LIMIT 1",
                (coupon["coupon_code"],),
            ):
                raise conflict("卡券已分配，不能删除")
        db.execute("DELETE FROM coupons WHERE batch_id = %s", (batch_id,))
        db.execute("DELETE FROM coupon_import_batches WHERE id = %s", (batch_id,))
    return {"deleted": True, "deletedCoupons": len(coupons)}
