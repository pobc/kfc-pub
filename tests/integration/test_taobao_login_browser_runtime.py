from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.config import settings
from app.service.taobao_login_service import TaobaoLoginService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name != "nt", reason="仅验证 Windows Uvicorn 的事件循环兼容性")
def test_taobao_login_browser_starts_under_windows_selector_policy() -> None:
    """开发服务器使用 Selector 策略时，Playwright 仍必须能创建驱动子进程。"""
    original_policy = asyncio.get_event_loop_policy()
    service = TaobaoLoginService()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        capture = service.start()
        assert capture["active"] is True
        assert capture["loginUrl"].startswith("https://s.taobao.com/")
        profile_root = Path(settings().taobao_browser_profile_dir)
        if not profile_root.is_absolute():
            profile_root = PROJECT_ROOT / profile_root
        task_profile = profile_root.resolve() / f"login-{capture['captureId']}"
        assert task_profile.is_dir()
        service.close(capture["captureId"])
        assert not task_profile.exists()
        assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)
    finally:
        service.close_all()
        asyncio.set_event_loop_policy(original_policy)
