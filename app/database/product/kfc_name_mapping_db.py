from __future__ import annotations

from typing import Any

from app.database import db
from app.errors import not_found
from app.utils import iso


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kfcProductName": row["kfc_product_name"],
        "productId": str(row["product_id"]),
        "productName": row.get("product_name") or "",
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def list_mappings() -> list[dict[str, Any]]:
    rows = db.fetchall(
        """SELECT m.*, p.name AS product_name
           FROM kfc_product_name_mappings m
           JOIN products p ON p.id=m.product_id
           ORDER BY m.kfc_product_name ASC"""
    )
    return [_view(row) for row in rows]


def upsert_mapping(kfc_product_name: str, product_id: str) -> dict[str, Any]:
    name = kfc_product_name.strip()
    with db.transaction():
        product = db.fetchone("SELECT id,name FROM products WHERE id=%s", (product_id,))
        if not product:
            raise not_found("映射目标餐品不存在")
        row = db.fetchone(
            """INSERT INTO kfc_product_name_mappings
               (kfc_product_name,product_id,"createdAt","updatedAt")
               VALUES (%s,%s,now(),now())
               ON CONFLICT (kfc_product_name) DO UPDATE SET
                 product_id=EXCLUDED.product_id,
                 "updatedAt"=now()
               RETURNING *""",
            (name, product_id),
        )
        row["product_name"] = product["name"]
    return _view(row)


def delete_mapping(mapping_id: str) -> dict[str, bool]:
    row = db.fetchone(
        "DELETE FROM kfc_product_name_mappings WHERE id=%s RETURNING id",
        (mapping_id,),
    )
    if not row:
        raise not_found("KFC 餐品名称映射不存在")
    return {"deleted": True}
