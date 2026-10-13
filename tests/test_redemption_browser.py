from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
import time

import pytest
import requests
from playwright.sync_api import sync_playwright

from app.service.kfc.redemption_browser import (
    RedemptionBrowser,
    VISION_SCRIPT,
    patch_coupon_bundle,
    run_browser_task,
)


def test_real_kfc_bundle_patch_and_recorded_slider_in_headed_chrome() -> None:
    response = requests.get("https://order.kfc.com.cn/preorder-taro/js/1816.054ffbb3.js", timeout=30)
    response.raise_for_status()
    source = response.text
    modified = patch_coupon_bundle(source)
    assert modified.count('"txt":Ze||"立即使用#"+d') == 1
    for contract in (
        '"className":"oc-pp-pc"',
        '"className":"oc-pp-pc-cartb_b"',
        '"className":"oc-pp-pc-cartb_v_t"',
        '"className":"m-m-i-grey-radio"',
        '"className":"m-m-i-cover"',
        '"className":"btn-circle"',
        '"shopCartText":"确定"',
        "superComoboDetails",
        "resultItems",
    ):
        assert contract in source
    with pytest.raises(RuntimeError):
        patch_coupon_bundle(modified)

    fixtures = Path(__file__).parent / "fixtures" / "slider"
    layers = [
        "data:image/png;base64," + base64.b64encode((fixtures / name).read_bytes()).decode("ascii")
        for name in ("background-with-gap.png", "background-full.png", "slice.png")
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        try:
            page = browser.new_page()
            assert page.evaluate("(source) => { new Function(source); return true; }", modified)
            page.evaluate(VISION_SCRIPT)
            geometry = page.evaluate(
                """async sources => {
              const layers = await Promise.all(sources.map(async src => {
                const image = new Image(); image.src = src; await image.decode();
                const canvas = document.createElement('canvas');
                canvas.width = image.width; canvas.height = image.height;
                const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                return {width: image.width, height: image.height,
                        data: context.getImageData(0, 0, image.width, image.height).data};
              }));
              const result = kfcSliderVision.detectSliderGeometry(
                ...layers.map(layer => layer.data), layers[0].width, layers[0].height);
              return {distance: result.distance, source: result.distanceSource};
            }""",
                layers,
            )
            # Captured challenge verified successfully by the supplied reference tool.
            assert geometry == {"distance": 126, "source": "photometric"}
            trajectory = page.evaluate("kfcSliderVision.buildTrajectory(30, 160, 126)")
            assert trajectory[-1]["x"] == 156
            assert trajectory[-1]["y"] == 160
            classification = page.evaluate(
                "kfcSliderVision.classifyVerificationResult(['验证成功', '服务端 forbidden'])"
            )
            assert classification["verificationPassed"] is False
        finally:
            browser.close()


def test_home_selection_requires_complete_restaurant_metadata() -> None:
    with pytest.raises(ValueError, match="餐厅城市、名称或图片缺失"):
        run_browser_task(
            RedemptionBrowser({}).open_menu(
                store_code="GZ1091",
                city_name="",
                store_name="",
                image_url="",
                pickup_type=1,
            )
        )


def test_confirmation_pickup_type_is_selected_before_submit() -> None:
    async def select() -> None:
        async with RedemptionBrowser({}, job_id="confirmation-pickup-check") as browser:
            await browser.page.set_content(
                """
                <style>
                  .option { border: 1px solid rgb(204, 204, 204); }
                  .selected { border: 2px solid rgb(228, 0, 43); }
                </style>
                <button class="option" onclick="selectType(this)">堂食</button>
                <button class="option" onclick="selectType(this)">外带</button>
                <script>
                  function selectType(target) {
                    document.querySelectorAll('.option').forEach(node => node.classList.remove('selected'));
                    target.classList.add('selected');
                  }
                </script>
                """
            )
            await browser._select_confirmation_pickup_type(2)
            assert await browser.page.get_by_text("堂食", exact=True).get_attribute("class") == "option selected"
            await browser._select_confirmation_pickup_type(1)
            assert await browser.page.get_by_text("外带", exact=True).get_attribute("class") == "option selected"

    run_browser_task(select())


def test_coupon_without_product_options_does_not_wait_for_add_to_cart() -> None:
    async def complete_coupon() -> None:
        async with RedemptionBrowser({}, job_id="coupon-without-options-check") as browser:
            await browser.page.set_content('<button class="spcb-fixed-text">选好了</button>')
            started = time.monotonic()
            completed_optional_meal = await browser._complete_optional_meal(
                asyncio.Queue(), [], has_product_options=False
            )
            assert completed_optional_meal is False
            assert time.monotonic() - started < 1

    run_browser_task(complete_coupon())


def test_coupon_with_product_options_requires_selection_snapshot() -> None:
    async def complete_coupon() -> None:
        async with RedemptionBrowser({}, job_id="coupon-with-options-check") as browser:
            await browser.page.set_content("<button>加入购物车</button>")
            with pytest.raises(RuntimeError, match="缺少 order_confirmation_logs 餐品选择记录"):
                await browser._complete_optional_meal(asyncio.Queue(), [], has_product_options=True)

    run_browser_task(complete_coupon())


def test_headed_chrome_launch_reports_each_startup_step(caplog) -> None:
    async def startup() -> None:
        async with RedemptionBrowser({}, job_id="startup-log-check") as browser:
            try:
                assert browser.page.url == "about:blank"
                for stage in (
                    "playwright-starting",
                    "chrome-resolving",
                    "chrome-launching",
                    "chrome-cdp-waiting",
                    "playwright-connecting",
                    "browser-open",
                ):
                    assert any(
                        f"stage={stage}" in r.message and "job=startup-log-check" in r.message for r in caplog.records
                    )
                # Exercise the same CDP preload used for login restoration and
                # menu cache reset. Without Page.enable this silently never runs.
                async with browser._preload_script("window.redemptionPreloadVerified = true"):
                    await browser.page.goto("data:text/html,<title>preload check</title>")
                    assert await browser.page.evaluate("window.redemptionPreloadVerified === true")
                await browser.page.goto("data:text/html,<title>preload removed</title>")
                assert await browser.page.evaluate("window.redemptionPreloadVerified === undefined")
            finally:
                # Close only the isolated, unauthenticated window created by this test.
                await browser.browser.close()

    with caplog.at_level(logging.INFO, logger="app.service.kfc.redemption_browser"):
        run_browser_task(startup())
    assert any("stage=browser_closed" in r.message for r in caplog.records)
