from __future__ import annotations

import os
import secrets

import pytest
from playwright.async_api import async_playwright

from app.database import access_links, db, order_confirmation_logs, products
from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task


def test_live_meal_replaces_defaults_and_expands_all_groups() -> None:
    """使用已打开的真实套餐页和持久化确认记录，仅验证选择，不加入购物车。"""
    endpoint = os.environ.get("KFC_MEAL_CDP_URL")
    if not endpoint:
        pytest.skip("需设置 KFC_MEAL_CDP_URL，连接已打开目标套餐的真实 Chrome")
    assert db.engine.url.database == "kfc"
    names = ["老北京鸡肉卷", "黄金鸡块(5块)", "红豆派(1只)"]
    product = products.create_product(
        {"name": f"套餐默认选择回归-{secrets.token_hex(6)}", "parentLinkId": "__AUTO_MATCH_BY_NAME__"}
    )
    config = products.replace_product_options(
        product["id"],
        [
            {"name": "主食", "selectCount": 2, "options": [{"name": name} for name in names[:2]]},
            {"name": "饮料及甜品", "selectCount": 1, "options": [{"name": names[2]}]},
        ],
    )
    option_ids = [option["id"] for group in config["groups"] for option in group["options"]]
    snapshot = products.selected_option_snapshot(product["id"], option_ids)
    link = access_links.create_access_links(product["id"], 1)[0]
    order_confirmation_logs.record_order_confirmation(link["accessId"], "127.0.0.1", "页面选择回归", "页面选择回归")
    order_confirmation_logs.save_production_info(link["accessId"], "页面选择回归", snapshot)
    saved = order_confirmation_logs.get_latest_production_info(link["accessId"])

    async def check() -> None:
        async with async_playwright() as playwright:
            chrome = await playwright.chromium.connect_over_cdp(endpoint)
            pages = [page for context in chrome.contexts for page in context.pages if "menu-detail" in page.url]
            assert len(pages) == 1, "必须唯一定位真实套餐选择页面"
            browser = RedemptionBrowser({}, job_id=f"meal-selection-{product['id']}")
            browser.page = pages[0]
            cards = browser.page.locator(".m-m-i").filter(visible=True)
            initial = [
                index for index in range(await cards.count()) if await browser._meal_card_selected(cards.nth(index))
            ]
            assert initial, "回归场景需要页面存在默认选中的餐品"
            assert await browser.page.get_by_text("更多", exact=True).count() >= 2, "回归场景需要两个折叠分组"
            try:
                for _ in range(2):
                    await browser._select_meal_options(saved)
                    assert await browser.page.get_by_text("更多", exact=True).count() == 0
                    selected = [
                        cards.nth(i)
                        for i in range(await cards.count())
                        if await browser._meal_card_selected(cards.nth(i))
                    ]
                    assert len(selected) == len(names)
                    for name in names:
                        assert any([await browser._meal_card_matches(card, name) for card in selected]), name
            finally:
                # 恢复用户原来的选择和折叠状态，不关闭用户浏览器，不触发下单请求。
                for index in range(await cards.count()):
                    card = cards.nth(index)
                    if await browser._meal_card_selected(card) and index not in initial:
                        await browser._click_frontmost(card.locator(".m-m-i-grey-radio"), scroll=True)
                        await browser._wait_for_meal_card_selected(card, str(index), selected=False)
                for index in initial:
                    card = cards.nth(index)
                    if not await browser._meal_card_selected(card):
                        await browser._click_frontmost(card.locator(".m-m-i-grey-radio"), scroll=True)
                        await browser._wait_for_meal_card_selected(card, str(index))
                folded = browser.page.get_by_text("收起", exact=True).filter(visible=True)
                for _ in range(2):
                    if not await folded.count():
                        break
                    await browser._click_frontmost(folded.first, scroll=True)
                    await browser._wait(300)

    run_browser_task(check())
