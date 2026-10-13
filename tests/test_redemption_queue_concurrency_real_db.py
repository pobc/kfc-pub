from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor

from app.database import db
from app.database.coupon import redemption_job_db as redemption_jobs


def _prepare_queue_case() -> tuple[str, int, str]:
    marker = secrets.token_hex(4)
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products
               (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"queue-{marker}", f"queue-link-{marker}"),
        )
        platform = db.fetchone(
            """INSERT INTO platforms (id,name,code,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,true,'queue-test',now(),now()) RETURNING id""",
            (f"queue-{marker}", f"QUE{marker}"),
        )
        account = db.fetchone(
            """INSERT INTO platform_accounts
               (id,platform_id,name,identifier,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,true,'queue-test',now(),now()) RETURNING id""",
            (platform["id"], f"queue-{marker}", f"queue-{marker}"),
        )
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches
               (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,%s,1,1,0,now(),now(),now()) RETURNING id""",
            (f"queue-{marker}", platform["id"], account["id"], product["id"]),
        )
        coupon = db.fetchone(
            """INSERT INTO coupons
               (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,
                imported_at,last_seen_at,"createdAt","updatedAt")
               VALUES (%s,'wait',%s,'AVAILABLE',%s,%s,%s,%s,now(),now(),now(),now()) RETURNING id""",
            (
                f"QUEUE-{marker}",
                f"queue-{marker}",
                platform["id"],
                account["id"],
                product["id"],
                batch["id"],
            ),
        )
        public_id = secrets.token_hex(8)
        db.execute(
            """INSERT INTO access_links
               (public_id,status,product_id,platform_id,account_id,"createdAt","updatedAt")
               VALUES (%s,'UNUSED',%s,%s,%s,now(),now())""",
            (public_id, product["id"], platform["id"], account["id"]),
        )
    return public_id, int(coupon["id"]), str(product["id"])


def _enqueue(public_id: str, product_id: str, index: int = 0) -> dict[str, object]:
    return redemption_jobs.enqueue(
        public_id,
        store_code="TEST-STORE",
        store_snapshot={"storecode": "TEST-STORE", "status": "1"},
        pack_type=2,
        items=[{"productId": product_id, "quantity": 1}],
        visitor_ip=f"127.1.0.{index + 1}",
    )


def test_same_link_concurrent_requests_use_access_link_as_single_redemption_job() -> None:
    public_id, coupon_id, product_id = _prepare_queue_case()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda index: _enqueue(public_id, product_id, index), range(10)))

    link = db.fetchone(
        """SELECT id,status,coupon_code,redemption_status,redemption_payload,redemption_error
           FROM access_links WHERE public_id=%s""",
        (public_id,),
    )
    logs = db.fetchall(
        "SELECT coupon_id FROM coupon_assignment_logs WHERE access_link_id=%s",
        (link["id"],),
    )
    coupon = db.fetchone("SELECT local_status FROM coupons WHERE id=%s", (coupon_id,))

    assert {result["id"] for result in results} == {str(link["id"])}
    assert link["status"] == "PROCESSING"
    assert link["redemption_status"] == "WAITING"
    assert link["redemption_payload"]["storeCode"] == "TEST-STORE"
    assert link["redemption_payload"]["items"] == [{"productId": product_id, "quantity": 1}]
    assert link["redemption_error"] == ""
    assert len(logs) == 1
    assert int(logs[0]["coupon_id"]) == coupon_id
    assert coupon["local_status"] == "assigned"
    assert not db.fetchall(
        "SELECT id FROM access_link_status_history WHERE access_link_id=%s",
        (link["id"],),
    )

    redemption_jobs.mark_failed(str(link["id"]), error_message="pytest queue cleanup")
    failed = db.fetchone(
        "SELECT status,redemption_status,redemption_error FROM access_links WHERE id=%s",
        (link["id"],),
    )
    assert failed == {
        "status": "FAILED",
        "redemption_status": "FAILED",
        "redemption_error": "pytest queue cleanup",
    }
    assert not db.fetchall(
        "SELECT id FROM access_link_status_history WHERE access_link_id=%s",
        (link["id"],),
    )


def test_redemption_job_state_is_persisted_only_on_access_link() -> None:
    public_id, _, product_id = _prepare_queue_case()
    queued = _enqueue(public_id, product_id)

    claimed = redemption_jobs.claim_next()
    assert claimed is not None
    assert claimed["id"] == queued["id"]
    assert claimed["productId"] == product_id
    assert claimed["status"] == "PROCESSING"

    redemption_jobs.mark_cart_created(claimed["id"], "cart-real-db")
    redemption_jobs.mark_coupon_applied(claimed["id"], cart_id="cart-real-db", upstream_code=0)
    completed = redemption_jobs.mark_completed(claimed["id"], upstream_code=0)

    assert completed["status"] == "COMPLETED"
    link = db.fetchone(
        """SELECT status,redemption_status,coupon_cart_id,coupon_applied_at,
                  redemption_started_at,redemption_finished_at
           FROM access_links WHERE id=%s""",
        (claimed["id"],),
    )
    assert link["status"] == "UNUSED"
    assert link["redemption_status"] == "COMPLETED"
    assert link["coupon_cart_id"] == "cart-real-db"
    assert link["coupon_applied_at"] is not None
    assert link["redemption_started_at"] is not None
    assert link["redemption_finished_at"] is not None
    assert db.fetchone("SELECT COUNT(*) AS n FROM orders WHERE access_link_public_id=%s", (public_id,))["n"] == 0
