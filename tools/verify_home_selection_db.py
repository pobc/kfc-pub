from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from pathlib import Path

from playwright.async_api import expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import db
from app.database.kfc.redemption_auth_db import get_enabled_auth_for_account
from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task
from app.service.kfc.redemption_logging import log_redemption_failure
from app.service.redemption_worker import _terminate_process_tree


def verify_home_selection(link_id: int, pickup_type: int) -> None:
    # Opt in explicitly: this reads an existing link/login and only navigates
    # the real site. It never opens coupons, redeems a voucher or submits an order.
    row = db.fetchone("SELECT * FROM access_links WHERE id = %s", (link_id,))
    assert row is not None
    payload = row.get("redemption_payload") or {}
    selected = payload.get("selectedStore") or {}
    store = payload.get("store") or {}
    pictures = store.get("storePic") or {}
    image_url = (
        row.get("store_image_url")
        or selected.get("imageUrl")
        or next(
            (urls[0] for key in ("storeSelfPic", "storeEnvirPic", "storeBuzAreaPic") if (urls := pictures.get(key))), ""
        )
    )
    store_name = selected.get("name") or store.get("storename") or store.get("storeName") or ""
    city_name = selected.get("cityName") or store.get("cityName") or store.get("cityname") or ""
    auth = get_enabled_auth_for_account(str(row["account_id"]))
    assert auth is not None

    async def check() -> None:
        browser = RedemptionBrowser(auth, job_id=f"home-selection-test-{pickup_type}")
        try:
            async with browser:
                async with asyncio.timeout(55):
                    await browser.open_menu(
                        store_code=row["store_code"],
                        city_name=city_name,
                        store_name=store_name,
                        image_url=image_url,
                        pickup_type=pickup_type,
                    )
                    assert browser.stage == "menu-open"
                    await expect(browser.page.locator(".address-store-name-text").filter(visible=True)).to_contain_text(
                        store_name
                    )
                    choice = browser.page.locator(".address-store-d-info-store").filter(visible=True)
                    if await choice.count():
                        await expect(choice).to_have_text("外带" if pickup_type == 1 else "堂食")
                    assert await browser.page.get_by_text("优惠助手", exact=True).filter(visible=True).count() > 0
                    assert (
                        await browser.page.evaluate(
                            "async () => (await navigator.permissions.query({name:'geolocation'})).state"
                        )
                        == "denied"
                    )
                    browser._step(
                        "home-selection-verified",
                        "真实菜单已验证餐厅、可见取餐选择器及定位权限 pickup_type=%s",
                        pickup_type,
                    )
        except Exception as exc:
            log_redemption_failure(
                logging.getLogger("app.tools.home_selection"),
                browser.job_id,
                browser.stage,
                exc,
                secrets=browser.log_secrets,
            )
            raise SystemExit(1) from None
        finally:
            if browser.process and browser.process.poll() is None:
                _terminate_process_tree(browser.process.pid)

    run_browser_task(check())


if __name__ == "__main__":
    from app.logging_utils import configure_logging

    parser = argparse.ArgumentParser(description="使用现有门店和登录验证首页选店，不用券或提交订单")
    parser.add_argument("--link-id", type=int, required=True)
    parser.add_argument("--pickup-type", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    configure_logging()
    try:
        verify_home_selection(args.link_id, args.pickup_type)
    finally:
        db.close()
