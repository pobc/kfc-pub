from __future__ import annotations

import logging
import multiprocessing

from app.database import db
from app.database.coupon import redemption_job_db as jobs
from app.service.redemption_worker import EXECUTION_FAILED, EXECUTION_PENDING, RedemptionWorker, _execute_job
from app.service.kfc.redemption_browser import run_browser_task
from test_redemption_queue_concurrency_real_db import _enqueue, _prepare_queue_case


def test_browser_startup_interruption_fails_without_pausing_queue(caplog) -> None:
    public_id, coupon_id, product_id = _prepare_queue_case()
    queued = _enqueue(public_id, product_id)
    worker = RedemptionWorker()
    claimed = jobs.claim_next()
    assert claimed["id"] == queued["id"]
    worker._stop.set()
    run_browser_task(worker._process_browser(claimed, {}))
    link = db.fetchone(
        "SELECT status,redemption_status,redemption_payload FROM access_links WHERE id=%s", (queued["id"],)
    )
    assert link["status"] == link["redemption_status"] == "FAILED"
    assert "browserCheckpoint" not in link["redemption_payload"]
    assert db.fetchone("SELECT local_status FROM coupons WHERE id=%s", (coupon_id,))["local_status"] == "assigned"
    assert any(
        "stage=playwright-starting" in r.message and "error=InterruptedError" in r.message for r in caplog.records
    )


def test_executor_reaches_auth_even_when_legacy_lock_is_held(caplog) -> None:
    public_id, _, product_id = _prepare_queue_case()
    queued = _enqueue(public_id, product_id)
    claimed = jobs.claim_next()
    assert claimed["id"] == queued["id"]
    with db.engine.connect() as blocker:
        assert blocker.exec_driver_sql("SELECT pg_try_advisory_lock(1262895954,1)").scalar_one()
        try:
            execution_state = multiprocessing.get_context("spawn").Value("i", EXECUTION_PENDING, lock=False)
            with caplog.at_level(logging.INFO, logger="app.service.redemption_worker"):
                _execute_job(
                    claimed,
                    multiprocessing.get_context("spawn").Value("i", 0, lock=False),
                    execution_state,
                )
            assert execution_state.value == EXECUTION_FAILED
            assert any("stage=auth_loading" in r.message for r in caplog.records)
            assert any("error=AppError" in r.message for r in caplog.records)
            assert (
                db.fetchone("SELECT redemption_status FROM access_links WHERE id=%s", (queued["id"],))[
                    "redemption_status"
                ]
                == "FAILED"
            )
        finally:
            blocker.exec_driver_sql("SELECT pg_advisory_unlock(1262895954,1)")


def test_checkpoint_does_not_pause_queue_and_idle_logs_are_limited(caplog) -> None:
    public_id, _, product_id = _prepare_queue_case()
    queued = _enqueue(public_id, product_id)
    next_job = None
    try:
        assert jobs.claim_next()["id"] == queued["id"]
        jobs.mark_browser_paused(queued["id"], checkpoint={"stage": "slider-verified"})
        next_public_id, _, next_product_id = _prepare_queue_case()
        next_job = _enqueue(next_public_id, next_product_id)
        assert jobs.claim_next()["id"] == next_job["id"]
        worker = RedemptionWorker()
        with caplog.at_level(logging.INFO, logger="app.service.redemption_worker"):
            worker._report_wait("waiting_queue")
            worker._report_wait("waiting_queue")
        assert not any("stage=queue_paused" in r.message for r in caplog.records)
        assert len([r for r in caplog.records if "stage=queue_idle" in r.message]) == 1
    finally:
        jobs.mark_failed(queued["id"], error_message="pytest legacy checkpoint inspected")
        if next_job:
            jobs.mark_failed(next_job["id"], error_message="pytest queue inspected")
