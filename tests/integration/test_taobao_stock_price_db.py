"""使用真实淘宝价格样本与本机 PostgreSQL 验证无货旧价格的清理边界。"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.database import db
from app.database.price import taobao_price_db, taobao_session_db
from app.service.taobao_browser import playwright_runtime, taobao_browser_context
from app.service.taobao_price_scraper import (
    TaobaoBrowserScraper,
    _decimal_price,
    _prices_from_payload,
    _sku_item_name,
    _sku_name_map,
)

PRODUCT_URL = "https://item.taobao.com/item.htm?id=876437121568"


def _seed_captured_prices(search_keyword: str) -> None:
    """按原始响应重建旧版本实际写入的价格，测试数据保留在真实数据库。"""
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json").read_text(encoding="utf-8")
    )
    names = _sku_name_map(payload["skuBase"])
    for sku in payload["skuBase"]["skus"]:
        taobao_price_db.upsert_price(
            {
                "shop_name": payload["seller"]["shopName"],
                "product_url": PRODUCT_URL,
                "item_name": _sku_item_name(sku, names, payload["item"]["title"]),
                "price": _decimal_price(payload["skuCore"]["sku2info"][sku["skuId"]]),
                "search_keyword": search_keyword,
            },
            datetime.now(timezone.utc),
        )


def test_real_captured_unavailable_cleanup_is_scoped_and_idempotent() -> None:
    assert db.fetchone("SELECT current_database() AS name")["name"] == "kfc"
    keyword = "库存回归-" + uuid.uuid4().hex
    other_keyword = keyword + "-其他来源"
    _seed_captured_prices(keyword)
    _seed_captured_prices(other_keyword)
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json").read_text(encoding="utf-8")
    )
    unavailable = []
    rows = _prices_from_payload(payload, "", unavailable_callback=unavailable.append)[2]

    assert taobao_price_db.delete_unavailable_prices(PRODUCT_URL, unavailable, keyword) == 13
    assert taobao_price_db.delete_unavailable_prices(PRODUCT_URL, unavailable, keyword) == 0
    saved = taobao_price_db.list_prices(source_keywords=[keyword])
    assert len(saved) == len(rows) == 43
    assert min(Decimal(item["price"]) for item in saved) == Decimal("13.90")
    assert len(taobao_price_db.list_prices(source_keywords=[other_keyword])) == 56
    # 验证来源隔离后，再显式更新第二个来源；保留真实有货价格，避免测试遗留错误低价。
    assert taobao_price_db.delete_unavailable_prices(PRODUCT_URL, unavailable, other_keyword) == 13
    assert taobao_price_db.delete_unavailable_prices(PRODUCT_URL, [], keyword) == 0
    with pytest.raises(ValueError, match="搜索关键词"):
        taobao_price_db.delete_unavailable_prices(PRODUCT_URL, unavailable, "")


@pytest.mark.skipif(os.getenv("TAOBAO_LIVE_STOCK_TEST") != "1", reason="需显式启用真实淘宝浏览器验证")
def test_live_scrape_filters_stock_before_writing_and_removes_old_prices(caplog) -> None:
    caplog.set_level("INFO", logger="kfc.taobao_price")
    assert db.fetchone("SELECT current_database() AS name")["name"] == "kfc"
    auth = taobao_session_db.get_current_session()
    assert auth, "缺少本机已保存的淘宝登录态"
    keyword = "实时库存回归-" + uuid.uuid4().hex
    _seed_captured_prices(keyword)
    unavailable = []
    persisted = []

    def persist(item: dict) -> None:
        assert item["price"] >= Decimal("13.90"), "无货低价不得进入写库回调"
        taobao_price_db.upsert_price({**item, "search_keyword": keyword}, datetime.now(timezone.utc))
        persisted.append(item)

    def cleanup(url: str, names: list[str]) -> None:
        unavailable.extend(names)
        taobao_price_db.delete_unavailable_prices(url, names, keyword)

    scraper = TaobaoBrowserScraper(
        lambda **changes: None,
        threading.Event(),
        auth,
        job_id=keyword,
        price_callback=persist,
        unavailable_callback=cleanup,
    )
    with playwright_runtime() as playwright:
        with taobao_browser_context(
            playwright, task_name=keyword, headless=False, show_window=False, auth_state=auth
        ) as context:
            page = context.pages[0]
            page.goto("https://s.taobao.com/", wait_until="commit", timeout=30000)
            cdp = context.new_cdp_session(page)
            frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            rows = scraper._scrape_detail(cdp, frame_id, PRODUCT_URL)

    assert rows == persisted
    assert len(unavailable) == 13
    saved = taobao_price_db.list_prices(source_keywords=[keyword])
    assert len(saved) == len(rows) == 43
    assert min(Decimal(item["price"]) for item in saved) == Decimal("13.90")
    assert "operation=parse_price result=success" in caplog.text
    assert "unavailableCount=13" in caplog.text
