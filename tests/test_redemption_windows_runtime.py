from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys

import pytest
from uvicorn.loops.asyncio import asyncio_setup

from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Uvicorn reload regression")
def test_browser_starts_in_worker_with_uvicorn_reload_policy() -> None:
    original_policy = asyncio.get_event_loop_policy()
    try:
        asyncio_setup(use_subprocess=True)
        server_policy = asyncio.get_event_loop_policy()

        async def worker_startup() -> str:
            async with RedemptionBrowser({}, job_id="windows-runtime-check") as browser:
                try:
                    assert browser.playwright is not None
                    return browser.stage
                finally:
                    await browser.browser.close()

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="kfc-redemption-worker") as worker:
            assert worker.submit(lambda: run_browser_task(worker_startup())).result(timeout=40) == "browser-open"
        assert asyncio.get_event_loop_policy() is server_policy
    finally:
        asyncio.set_event_loop_policy(original_policy)
