from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.errors import bad_request, conflict, not_found
from app.serializers import product_view


def count_products() -> int:
    return db.fetchone("SELECT COUNT(*) AS n FROM products")["n"]


def has_product_options(product_id: str) -> bool:
    row = db.fetchone(
        """SELECT p.id, COUNT(o.id) > 0 AS has_options
           FROM products p
           LEFT JOIN product_options o ON o.product_id = p.id
           WHERE p.id = %s
           GROUP BY p.id""",
        (product_id,),
    )
    if not row:
        raise not_found("餐品不存在")
    return bool(row["has_options"])


def list_products() -> list[dict[str, Any]]:
    rows = db.fetchall(
        """SELECT p.*,
                  COALESCE(o.option_group_count, 0) AS option_group_count,
                  COALESCE(o.option_count, 0) AS option_count
           FROM products p
           LEFT JOIN (
               SELECT product_id,
                      COUNT(DISTINCT group_no) AS option_group_count,
                      COUNT(*) AS option_count
               FROM product_options
               GROUP BY product_id
           ) o ON o.product_id = p.id
           ORDER BY p."createdAt" DESC"""
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = product_view(row)
        item["optionGroupCount"] = int(row.get("option_group_count") or 0)
        item["optionCount"] = int(row.get("option_count") or 0)
        result.append(item)
    return result


def create_product(data: dict[str, Any]) -> dict[str, Any]:
    row = db.fetchone(
        """INSERT INTO products (id, name, description, image_url, parent_link_id, spec_link_ids, condiment_link_ids, "createdAt", "updatedAt")
           VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, now(), now()) RETURNING *""",
        (
            data["name"].strip(),
            data.get("description", "").strip(),
            (data.get("imageUrl") or "").strip() or None,
            data["parentLinkId"].strip(),
            Jsonb([v.strip() for v in data.get("specLinkIds", []) if v.strip()]),
            Jsonb([v.strip() for v in data.get("condimentLinkIds", []) if v.strip()]),
        ),
    )
    return product_view(row)


def update_product(product_id: str, data: dict[str, Any]) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM products WHERE id = %s", (product_id,))
    if not row:
        raise not_found("餐品不存在")
    fields: list[str] = []
    values: list[Any] = []
    mapping = {
        "name": "name",
        "description": "description",
        "imageUrl": "image_url",
        "parentLinkId": "parent_link_id",
    }
    for key, column in mapping.items():
        if key in data and data[key] is not None:
            value = data[key]
            if isinstance(value, str):
                value = value.strip()
                if key == "imageUrl" and not value:
                    value = None
            fields.append(f"{column} = %s")
            values.append(value)
    if fields:
        fields.append('"updatedAt" = now()')
        values.append(product_id)
        row = db.fetchone(
            f"UPDATE products SET {', '.join(fields)} WHERE id = %s RETURNING *",
            tuple(values),
        )
    return product_view(row)


def list_product_options(product_id: str) -> dict[str, Any]:
    product = db.fetchone("SELECT id, name FROM products WHERE id = %s", (product_id,))
    if not product:
        raise not_found("餐品不存在")

    rows = db.fetchall(
        """SELECT id, group_no, group_name, select_count, option_name, image_url, sort
           FROM product_options
           WHERE product_id = %s
           ORDER BY group_no ASC, sort ASC, id ASC""",
        (product_id,),
    )

    groups: list[dict[str, Any]] = []
    current_group_no: int | None = None
    current: dict[str, Any] | None = None
    for row in rows:
        group_no = int(row["group_no"])
        if group_no != current_group_no:
            current_group_no = group_no
            current = {
                "groupNo": group_no,
                "name": row["group_name"],
                "selectCount": int(row["select_count"]),
                "options": [],
            }
            groups.append(current)
        current["options"].append(
            {
                "id": int(row["id"]),
                "name": row["option_name"],
                "imageUrl": row.get("image_url"),
            }
        )

    return {
        "productId": str(product["id"]),
        "productName": product["name"],
        "groups": groups,
    }


def selected_option_snapshot(product_id: str, option_ids: list[int]) -> list[dict[str, Any]]:
    if any(option_id <= 0 for option_id in option_ids):
        raise bad_request("可选餐品ID无效")
    if len(option_ids) != len(set(option_ids)):
        raise bad_request("存在重复的可选餐品")

    rows = db.fetchall(
        """SELECT id, group_no, group_name, select_count, option_name
           FROM product_options
           WHERE product_id = %s
           ORDER BY group_no ASC, sort ASC, id ASC""",
        (product_id,),
    )
    selected_ids = set(option_ids)
    selected_rows = [row for row in rows if int(row["id"]) in selected_ids]
    if len(selected_rows) != len(option_ids):
        raise bad_request("存在不属于当前餐品的可选餐品")

    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(
            int(row["group_no"]),
            {
                "name": row["group_name"],
                "required": int(row["select_count"]),
                "selected": 0,
            },
        )
        if int(row["id"]) in selected_ids:
            group["selected"] += 1
    for group in groups.values():
        if group["selected"] != group["required"]:
            raise bad_request(
                f"分组“{group['name']}”需选择{group['required']}个不同餐品，当前已选{group['selected']}个"
            )

    return [
        {
            "id": int(row["id"]),
            "group_no": int(row["group_no"]),
            "name": row["option_name"],
        }
        for row in selected_rows
    ]


def replace_product_options(product_id: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    normalized: list[tuple[str, int, list[dict[str, str | None]]]] = []
    seen_group_names: set[str] = set()

    for group in groups:
        group_name = (group.get("name") or "").strip()
        if not group_name:
            raise bad_request("分组名称不能为空")
        normalized_group_name = group_name.casefold()
        if normalized_group_name in seen_group_names:
            raise bad_request(f"分组名称“{group_name}”重复")
        seen_group_names.add(normalized_group_name)

        raw_options = group.get("options") or []
        if not raw_options:
            raise bad_request(f"分组“{group_name}”至少添加一个可选餐品")
        select_count = group.get("selectCount", 1)
        if type(select_count) is not int or not 1 <= select_count <= len(raw_options):
            raise bad_request(f"分组“{group_name}”的必选数量须为1至{len(raw_options)}的整数")

        option_names: set[str] = set()
        normalized_options: list[dict[str, str | None]] = []
        for option in raw_options:
            option_name = (option.get("name") or "").strip()
            if not option_name:
                raise bad_request(f"分组“{group_name}”存在未填写名称的可选餐品")
            normalized_option_name = option_name.casefold()
            if normalized_option_name in option_names:
                raise bad_request(f"分组“{group_name}”存在重复餐品“{option_name}”")
            option_names.add(normalized_option_name)
            image_url = (option.get("imageUrl") or "").strip() or None
            normalized_options.append({"name": option_name, "imageUrl": image_url})

        normalized.append((group_name, select_count, normalized_options))

    with db.transaction():
        product = db.fetchone("SELECT id FROM products WHERE id = %s FOR UPDATE", (product_id,))
        if not product:
            raise not_found("餐品不存在")

        db.execute("DELETE FROM product_options WHERE product_id = %s", (product_id,))
        for group_no, (group_name, select_count, options) in enumerate(normalized, start=1):
            for index, option in enumerate(options, start=1):
                db.execute(
                    """INSERT INTO product_options
                       (product_id, group_no, group_name, select_count, option_name, image_url, sort)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (product_id, group_no, group_name, select_count, option["name"], option["imageUrl"], index * 10),
                )

    return list_product_options(product_id)


def delete_product(product_id: str) -> dict[str, bool]:
    with db.transaction():
        row = db.fetchone("SELECT id FROM products WHERE id = %s FOR UPDATE", (product_id,))
        if not row:
            raise not_found("餐品不存在")
        if db.fetchone("SELECT 1 FROM coupons WHERE product_id = %s LIMIT 1", (product_id,)):
            raise conflict("餐品仍有关联卡券，不能删除")
        if db.fetchone("SELECT 1 FROM access_links WHERE product_id = %s LIMIT 1", (product_id,)):
            raise conflict("餐品仍有关联访问链接，不能删除")
        db.execute(
            'UPDATE coupon_import_batches SET product_id = NULL, "updatedAt" = now() WHERE product_id = %s',
            (product_id,),
        )
        db.execute("DELETE FROM product_options WHERE product_id = %s", (product_id,))
        db.execute("DELETE FROM products WHERE id = %s", (product_id,))
    return {"deleted": True}
