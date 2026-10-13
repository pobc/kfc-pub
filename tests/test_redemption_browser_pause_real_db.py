from __future__ import annotations

from app.database import db
from app.database.coupon import redemption_job_db as jobs
from test_redemption_queue_concurrency_real_db import _enqueue, _prepare_queue_case


def test_legacy_browser_checkpoint_expires_without_blocking_next_job() -> None:
    public_id, coupon_id, product_id = _prepare_queue_case()
    job = _enqueue(public_id, product_id)
    next_public_id, _, next_product_id = _prepare_queue_case()
    next_job = None
    try:
        assert jobs.claim_next()["id"] == job["id"]
        checkpoint = {"stage": "slider-verified", "verification": "verified"}
        jobs.mark_browser_paused(job["id"], checkpoint=checkpoint)
        db.execute(
            "UPDATE access_links SET redemption_started_at=now()-interval '1 hour' WHERE id=%s",
            (job["id"],),
        )
        assert jobs.recover_stale_jobs() == 1
        next_job = _enqueue(next_public_id, next_product_id)
        assert jobs.claim_next()["id"] == next_job["id"]
        link = db.fetchone(
            """SELECT status,redemption_status,store_code,redemption_payload,
                      coupon_applied_at,coupon_cart_id,redemption_finished_at
               FROM access_links WHERE id=%s""",
            (job["id"],),
        )
        assert link["status"] == "FAILED"
        assert link["redemption_status"] == "FAILED"
        assert link["store_code"] == "TEST-STORE"
        assert link["redemption_payload"]["browserCheckpoint"] == checkpoint
        assert link["coupon_applied_at"] is None
        assert link["coupon_cart_id"] is None
        assert link["redemption_finished_at"] is not None
        assert db.fetchone("SELECT local_status FROM coupons WHERE id=%s", (coupon_id,))["local_status"] == "assigned"
        assert db.fetchone("SELECT count(*) AS n FROM orders WHERE access_link_public_id=%s", (public_id,))["n"] == 0
    finally:
        # Preserve all test rows, release the queue checkpoint explicitly.
        jobs.mark_failed(job["id"], error_message="pytest browser checkpoint inspected")
        if next_job:
            jobs.mark_failed(next_job["id"], error_message="pytest queued browser case inspected")


def test_claim_reads_access_link_generated_store_code() -> None:
    public_id, _, product_id = _prepare_queue_case()
    job = _enqueue(public_id, product_id)
    try:
        db.execute(
            "UPDATE access_links SET redemption_payload=jsonb_set(redemption_payload,'{storeCode}',%s::jsonb) WHERE id=%s",
            ('"GZ1091"', job["id"]),
        )
        claimed = jobs.claim_next()
        assert claimed["id"] == job["id"]
        assert claimed["storeCode"] == "GZ1091"
        assert claimed["storeSnapshot"]["storecode"] == "TEST-STORE"
    finally:
        jobs.mark_failed(job["id"], error_message="pytest store source inspected")
