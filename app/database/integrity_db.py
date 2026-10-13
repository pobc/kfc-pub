from __future__ import annotations

from app.database import db

ORPHAN_QUERIES = {
    "access_links.product_id": """
        SELECT COUNT(*) AS n FROM access_links a
        WHERE a.product_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM products p WHERE p.id=a.product_id)
    """,
    "access_links.product_group_id": """
        SELECT COUNT(*) AS n FROM access_links a
        WHERE a.product_group_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM product_groups g WHERE g.id=a.product_group_id)
    """,
    "coupons.platform_id": """
        SELECT COUNT(*) AS n FROM coupons c
        WHERE NOT EXISTS (SELECT 1 FROM platforms p WHERE p.id=c.platform_id)
    """,
    "coupons.account_id": """
        SELECT COUNT(*) AS n FROM coupons c
        WHERE NOT EXISTS (SELECT 1 FROM platform_accounts a WHERE a.id=c.account_id)
    """,
    "coupons.product_id": """
        SELECT COUNT(*) AS n FROM coupons c
        WHERE c.product_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM products p WHERE p.id=c.product_id)
    """,
    "coupons.batch_id": """
        SELECT COUNT(*) AS n FROM coupons c
        WHERE NOT EXISTS (SELECT 1 FROM coupon_import_batches b WHERE b.id=c.batch_id)
    """,
    "coupon_assignment_logs.access_link_id": """
        SELECT COUNT(*) AS n FROM coupon_assignment_logs l
        WHERE NOT EXISTS (SELECT 1 FROM access_links a WHERE a.id=l.access_link_id)
    """,
    "coupon_assignment_logs.coupon_id": """
        SELECT COUNT(*) AS n FROM coupon_assignment_logs l
        WHERE NOT EXISTS (SELECT 1 FROM coupons c WHERE c.id=l.coupon_id)
    """,
    "orders.access_link_id": """
        SELECT COUNT(*) AS n FROM orders o
        WHERE NOT EXISTS (SELECT 1 FROM access_links a WHERE a.id=o.access_link_id)
    """,
    "product_group_items.product_group_id": """
        SELECT COUNT(*) AS n FROM product_group_items i
        WHERE NOT EXISTS (SELECT 1 FROM product_groups g WHERE g.id=i.product_group_id)
    """,
    "product_group_items.product_id": """
        SELECT COUNT(*) AS n FROM product_group_items i
        WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id=i.product_id)
    """,
    "kfc_product_name_mappings.product_id": """
        SELECT COUNT(*) AS n FROM kfc_product_name_mappings m
        WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id=m.product_id)
    """,
}


def orphan_counts() -> dict[str, int]:
    return {relation: int(db.fetchone(query)["n"]) for relation, query in ORPHAN_QUERIES.items()}
