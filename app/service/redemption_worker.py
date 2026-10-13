from __future__ import annotations

from datetime import datetime, timezone
import logging
import multiprocessing
import subprocess
import signal
import os
import threading
import time
from typing import Any

from app.database import db, order_confirmation_logs, orders, products
from app.logging_utils import configure_logging
from app.database.coupon import redemption_job_db as redemption_jobs
from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task
from app.service.kfc.redemption_logging import log_redemption_failure
from app.service.kfc.redemption_response import CouponUnavailableError, response_succeeded
from app.service.kfc_account_service import get_enabled_auth_for_account

logger = logging.getLogger(__name__)

EXECUTION_PENDING = 0
EXECUTION_COMPLETED = 1
EXECUTION_FAILED = 2


class RedemptionWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_wait_report = 0.0
        self._last_wait_reason = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("KFC redemption worker start skipped because thread is already alive")
            return
        self._stop.clear()
        self._last_wait_report = 0.0
        self._last_wait_reason = ""
        self._thread = threading.Thread(target=self._run, name="kfc-redemption-worker", daemon=True)
        self._thread.start()
        logger.info("[核销] stage=worker_started pid=%s thread=%s", os.getpid(), self._thread.name)

    def stop(self) -> None:
        logger.info("KFC redemption worker stop requested")
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join()
        self._thread = None
        logger.info("KFC redemption worker stopped")

    def _run(self) -> None:
        logger.info("[核销] stage=queue_started 单 Worker 开始读取队列，无跨进程执行锁")
        while not self._stop.is_set():
            try:
                self._run_queue()
            except Exception:
                logger.exception("KFC redemption worker loop failed")
                self._stop.wait(2)
        logger.info("KFC redemption worker loop exited")

    def _report_wait(self, reason: str) -> None:
        now = time.monotonic()
        if reason == self._last_wait_reason and now - self._last_wait_report < 30:
            return
        state = redemption_jobs.worker_queue_diagnostics()
        self._last_wait_report = now
        self._last_wait_reason = reason
        logger.info(
            "[核销] stage=queue_idle Worker 正常轮询 waiting=%s next_job=%s",
            state["waiting"],
            state["nextJobId"],
        )

    def _run_queue(self) -> None:
        last_recovery = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_recovery >= 1:
                    recovered = redemption_jobs.recover_stale_jobs()
                    if recovered:
                        logger.warning("[核销] stage=timeout_recovery 超过60秒的任务已标记 FAILED count=%s", recovered)
                    last_recovery = now

                job = redemption_jobs.claim_next()
                if not job:
                    self._report_wait("waiting_queue")
                    self._stop.wait(0.5)
                    continue
                self._last_wait_reason = ""
                logger.info(
                    "[核销] job=%s stage=job_claimed 已领取任务 access_link=%s coupon_id=%s account_id=%s store_code=%s",
                    job.get("id"),
                    job.get("accessLinkId"),
                    job.get("couponId"),
                    job.get("accountId"),
                    job.get("storeCode"),
                )
                try:
                    self._process(job)
                except Exception as exc:
                    log_redemption_failure(
                        logger, job["id"], "job_failed", exc, secrets=(str(job.get("couponCode") or ""),)
                    )
                    try:
                        redemption_jobs.mark_failed(
                            job["id"],
                            error_message=f"核销 Worker 异常中断: {type(exc).__name__}",
                        )
                        logger.error("redemption job marked FAILED job=%s reason=worker_exception", job["id"])
                    except Exception:
                        logger.exception("failed to persist unexpected redemption worker failure job=%s", job["id"])
        finally:
            logger.info("[核销] stage=queue_stopped Worker 队列循环已退出")

    def _process(self, job: dict[str, Any]) -> None:
        # 使用独立进程执行浏览器任务，确保 Chrome、Playwright 或同步数据库调用卡死时仍能强制超时。
        started = datetime.fromisoformat(job["workerStartedAt"].replace("Z", "+00:00"))
        remaining = max(0.0, 60 - (datetime.now(timezone.utc) - started).total_seconds())
        context = multiprocessing.get_context("spawn")
        browser_pid = context.Value("i", 0, lock=False)
        execution_state = context.Value("i", EXECUTION_PENDING, lock=False)
        process = context.Process(
            target=_execute_job,
            args=(job, browser_pid, execution_state),
            name="kfc-redemption-job",
        )
        deadline = time.monotonic() + remaining
        reason = ""
        exit_code: int | None = None
        logger.info("[核销] job=%s stage=task_start remaining=%.2fs", job["id"], remaining)
        try:
            if remaining <= 0:
                reason = "核销任务超过60秒，已停止执行，请核对上游结果"
            else:
                process.start()
                while process.is_alive():
                    if self._stop.is_set():
                        reason = "核销 Worker 已停止，请核对上游结果"
                        break
                    if time.monotonic() >= deadline:
                        reason = "核销任务超过60秒，已停止执行，请核对上游结果"
                        break
                    process.join(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
        finally:
            # 先停止执行进程，避免监督进程判定失败后仍有迟到响应继续推进订单。
            if process.pid and process.is_alive():
                if os.name == "nt":
                    _terminate_process_tree(process.pid)
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
            if browser_pid.value:
                _terminate_process_tree(browser_pid.value)
            if process.pid:
                exit_code = process.exitcode
                process.close()
        if reason:
            result = redemption_jobs.mark_failed(job["id"], error_message=reason)
            logger.warning("[核销] job=%s stage=job_stopped result=%s reason=%s", job["id"], result["status"], reason)
        elif execution_state.value == EXECUTION_COMPLETED:
            logger.info("[核销] job=%s stage=task_completed exit_code=%s", job["id"], exit_code)
        elif execution_state.value == EXECUTION_FAILED:
            logger.warning("[核销] job=%s stage=task_failed exit_code=%s", job["id"], exit_code)
        else:
            # 子进程未写入终态，说明它绕过了业务异常边界，必须释放任务且禁止自动重放上游操作。
            result = redemption_jobs.mark_failed(
                job["id"],
                error_message="核销执行进程异常退出，请核对上游结果",
            )
            logger.error(
                "[核销] job=%s stage=task_aborted result=%s exit_code=%s",
                job["id"],
                result["status"],
                exit_code,
            )

    async def _process_browser(
        self,
        job: dict[str, Any],
        auth: dict[str, Any],
        browser_pid: Any = None,
    ) -> str:
        job_id = job["id"]
        browser = RedemptionBrowser(auth, stop=self._stop, job_id=str(job_id), browser_pid=browser_pid)
        try:
            async with browser:
                production_info = order_confirmation_logs.get_latest_production_info(job["publicId"])
                has_product_options = products.has_product_options(job["productId"])
                logger.info(
                    "[核销] job=%s stage=product_options_loaded product_id=%s has_options=%s selection_count=%s",
                    job_id,
                    job["productId"],
                    has_product_options,
                    len(production_info),
                )
                await browser.open_menu(
                    store_code=job["storeCode"],
                    city_name=job["cityName"],
                    store_name=job["storeName"],
                    image_url=job["storeImageUrl"],
                    pickup_type=job["packType"],
                )
                cart_id = await browser.use_coupon(
                    job["couponCode"],
                    job["packType"],
                    has_product_options=has_product_options,
                    production_info=production_info,
                )
                redemption_jobs.mark_cart_created(job_id, cart_id)
                redemption_jobs.mark_coupon_applied(job_id, cart_id=cart_id)
                submission = await browser.submit_order(job["packType"])
                response = submission["body"]
                succeeded = response_succeeded(response)
                saved = orders.save_browser_submission(str(job_id), response, succeeded=succeeded)
                logger.info(
                    "[核销] job=%s stage=order_completed result=%s order=%s http_status=%s",
                    job_id,
                    saved["stage"],
                    saved["id"],
                    submission["httpStatus"],
                )
                return "COMPLETED" if succeeded else "FAILED"
        except CouponUnavailableError as exc:
            order_confirmation_logs.save_unavailable_result(job_id, exc.reasons)
            redemption_jobs.mark_failed(job_id, error_message="目标卡券库存不足，无法下单")
            logger.warning("[核销] job=%s stage=coupon_unavailable result=FAILED", job_id)
            return "FAILED"
        except Exception as exc:
            log_redemption_failure(logger, job_id, browser.stage, exc, secrets=browser.log_secrets)
            redemption_jobs.mark_failed(
                job_id, error_message=f"核销失败，步骤 {browser.stage}，异常 {type(exc).__name__}"
            )
            return "FAILED"


def _terminate_process_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # Chrome 已正常退出，无需重复清理。
    except Exception:
        logger.exception("[核销] stage=browser_cleanup_failed pid=%s", pid)


def _execute_job(job: dict[str, Any], browser_pid: Any, execution_state: Any = None) -> None:
    configure_logging()
    worker = RedemptionWorker()
    try:
        logger.info("[核销] job=%s stage=auth_loading account_id=%s", job["id"], job["accountId"])
        auth = get_enabled_auth_for_account(job["accountId"])
        result = run_browser_task(worker._process_browser(job, auth, browser_pid))
        if execution_state is not None:
            execution_state.value = EXECUTION_COMPLETED if result == "COMPLETED" else EXECUTION_FAILED
    except Exception as exc:
        log_redemption_failure(logger, job["id"], "job_failed", exc, secrets=(str(job.get("couponCode") or ""),))
        redemption_jobs.mark_failed(job["id"], error_message=f"核销执行失败: {type(exc).__name__}")
        if execution_state is not None:
            execution_state.value = EXECUTION_FAILED
    finally:
        db.close()


redemption_worker = RedemptionWorker()
