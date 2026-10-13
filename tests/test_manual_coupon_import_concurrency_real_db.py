from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor

from app.database import db
from app.database.coupon.coupon_db import import_manual_coupons
from app.database.platform.platform_db import ensure_kfc_source


def test_manual_coupon_import_concurrency_uses_on_conflict_without_aborted_transactions() -> None:
    marker = secrets.token_hex(4)
    code = f"MANUAL-CONCURRENT-{marker}"
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products
               (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"manual-concurrent-{marker}", f"manual-concurrent-link-{marker}"),
        )
        platform_id, _ = ensure_kfc_source()

    def run(index: int) -> dict:
        return import_manual_coupons(
            str(product["id"]),
            f"并发导入测试-{marker}-{index}",
            [code],
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(run, range(20)))

    inserted = sum(len(result["items"]) for result in results)
    duplicate_failures = sum(
        1
        for result in results
        for failure in result["failures"]
        if failure.get("couponCode") == code and "已导入" in str(failure.get("reason") or "")
    )
    coupon_count = db.fetchone(
        "SELECT COUNT(*) AS n FROM coupons WHERE platform_id=%s AND coupon_code=%s",
        (platform_id, code),
    )["n"]
    batches = db.fetchall(
        "SELECT success_count, failed_count FROM coupon_import_batches WHERE name LIKE %s",
        (f"并发导入测试-{marker}-%",),
    )

    assert inserted == 1
    assert duplicate_failures == 19
    assert coupon_count == 1
    assert len(batches) == 20
    assert sum(row["success_count"] for row in batches) == 1
    assert sum(row["failed_count"] for row in batches) == 19
