from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor

from app.database import db
from app.database.access.coupon_application_db import (
    fail_coupon_application,
    reserve_coupon_application,
)
from app.database.coupon.assignment_log_db import assign_coupon


def _prepare_link_with_coupons(coupon_count: int = 2) -> tuple[str, str, int]:
    marker = secrets.token_hex(4)
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products
               (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"recovery-{marker}", f"recovery-link-{marker}"),
        )
        platform = db.fetchone(
            """INSERT INTO platforms (id,name,code,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,true,'recovery-test',now(),now()) RETURNING id""",
            (f"recovery-{marker}", f"REC{marker}"),
        )
        account = db.fetchone(
            """INSERT INTO platform_accounts
               (id,platform_id,name,identifier,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,true,'recovery-test',now(),now()) RETURNING id""",
            (platform["id"], f"recovery-{marker}", f"recovery-{marker}"),
        )
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches
               (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,0,now(),now(),now()) RETURNING id""",
            (f"recovery-{marker}", platform["id"], account["id"], product["id"], coupon_count, coupon_count),
        )
        for index in range(coupon_count):
            db.execute(
                """INSERT INTO coupons
                   (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,
                    imported_at,last_seen_at,"createdAt","updatedAt")
                   VALUES (%s,'wait',%s,'AVAILABLE',%s,%s,%s,%s,now(),now(),now(),now())""",
                (
                    f"RECOVERY-{marker}-{index}",
                    f"recovery-{marker}",
                    platform["id"],
                    account["id"],
                    product["id"],
                    batch["id"],
                ),
            )
        public_id = secrets.token_hex(8)
        link = db.fetchone(
            """INSERT INTO access_links
               (public_id,status,product_id,platform_id,account_id,internal_locked_at,"createdAt","updatedAt")
               VALUES (%s,'UNUSED',%s,%s,%s,now(),now(),now()) RETURNING id""",
            (public_id, product["id"], platform["id"], account["id"]),
        )
    return public_id, str(product["id"]), int(link["id"])


def test_same_access_link_100_calls_never_reassigns_coupon() -> None:
    public_id, _, link_id = _prepare_link_with_coupons(2)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda index: assign_coupon(public_id, f"127.0.1.{index + 1}"), range(100)))

    coupon_ids = {result["couponId"] for result in results}
    assert len(coupon_ids) == 1
    link = db.fetchone("SELECT coupon_code FROM access_links WHERE id=%s", (link_id,))
    logs = db.fetchall(
        "SELECT coupon_id FROM coupon_assignment_logs WHERE access_link_id=%s",
        (link_id,),
    )
    statuses = db.fetchall(
        """SELECT local_status FROM coupons
           WHERE product_id=(SELECT product_id FROM access_links WHERE id=%s)
           ORDER BY id""",
        (link_id,),
    )
    assert link["coupon_code"]
    assert len(logs) == 1
    assert [row["local_status"] for row in statuses].count("assigned") == 1
    assert [row["local_status"] for row in statuses].count("wait") == 1


def test_stale_operation_lock_can_be_recovered_and_failure_keeps_coupon() -> None:
    public_id, product_id, link_id = _prepare_link_with_coupons(1)
    db.execute(
        "UPDATE access_links SET internal_locked_at=now() - interval '10 minutes' WHERE id=%s",
        (link_id,),
    )

    reserve_coupon_application(
        public_id,
        {"status": "OPEN"},
        [{"id": product_id, "minQty": 1, "maxQty": 10}],
        [{"productId": product_id, "quantity": 1}],
    )
    assigned = assign_coupon(public_id, "127.0.0.1")
    fail_coupon_application(public_id)

    link = db.fetchone(
        "SELECT coupon_code, internal_locked_at FROM access_links WHERE id=%s",
        (link_id,),
    )
    log_count = db.fetchone(
        "SELECT COUNT(*) AS n FROM coupon_assignment_logs WHERE access_link_id=%s",
        (link_id,),
    )["n"]
    assert link["coupon_code"]
    assert link["internal_locked_at"] is None
    assert log_count == 1
    assert assigned["couponId"]
