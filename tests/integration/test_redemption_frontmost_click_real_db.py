from __future__ import annotations

from app.database import db
from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task


def test_frontmost_click_handles_an_exposed_but_continuously_moving_button() -> None:
    # 集成测试必须连接项目约定的真实 PostgreSQL，避免误跑到临时数据库。
    assert db.engine.url.database == "kfc"

    async def click_moving_button() -> None:
        async with RedemptionBrowser({}, job_id="frontmost-moving-button-check") as browser:
            await browser.page.set_content(
                """
                <style>
                  @keyframes move-continuously {
                    from { left: 20px; }
                    to { left: 220px; }
                  }
                  #close {
                    position: absolute;
                    top: 40px;
                    animation: move-continuously 1s linear infinite alternate;
                  }
                </style>
                <button id="close" onclick="this.dataset.clicked = 'yes'">关闭</button>
                """
            )
            browser.page.set_default_timeout(500)
            close = browser.page.locator("#close").filter(visible=True)

            await browser._click_frontmost(close)

            assert await close.get_attribute("data-clicked") == "yes"

    run_browser_task(click_moving_button())
