"""使用真实淘宝详情与 PostgreSQL 验证每条规格解析完成后的提交时机。"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError

from app.database import db
from app.database.price import taobao_price_db, taobao_session_db
from app.service.taobao_browser import playwright_runtime, taobao_browser_context
from app.service.taobao_price_scraper import TaobaoBrowserScraper, TaobaoPricePersistenceError, _sku_item_name
from app.service.taobao_price_service import TaobaoPriceService


@pytest.mark.skipif(
    os.getenv("TAOBAO_LIVE_PERSISTENCE_TEST") != "1",
    reason="需显式启用真实淘宝详情抓取，并使用本机已保存的登录态",
)
@pytest.mark.parametrize("fail_on_second", [False, True])
def test_each_real_sku_is_committed_before_parsing_the_next_sku(fail_on_second: bool) -> None:
    assert db.fetchone("SELECT current_database() AS name")["name"] == "kfc"
    auth_state = taobao_session_db.get_current_session()
    assert auth_state, "请先保存真实淘宝登录态"
    product_url = os.getenv("TAOBAO_TEST_PRODUCT_URL", "https://item.taobao.com/item.htm?id=834531475905")
    job_id = f"commit-verification-{uuid.uuid4().hex}"
    service = TaobaoPriceService()
    parsed_count = 0
    persisted_count = 0
    first_saved_item = None
    first_saved_at = None

    def observe_parse(frame, event, arg):
        nonlocal parsed_count
        if event == "return" and frame.f_code is _sku_item_name.__code__:
            parsed_count += 1

    def persist_price(item: dict) -> None:
        nonlocal persisted_count, first_saved_item, first_saved_at
        assert parsed_count == persisted_count + 1, "下一条规格已解析，上一条规格才开始入库"
        if fail_on_second and persisted_count == 1:
            # 由真实 PostgreSQL 拒绝超出列精度的第二条写入，验证故障会中止解析。
            item = {**item, "price": Decimal("100000000000000000000")}
        scraped_at = datetime.now(timezone.utc)
        assert taobao_price_db.upsert_price(item, scraped_at) == 1
        with db.engine.connect() as connection:
            saved = (
                connection.exec_driver_sql(
                    "SELECT price, scraped_at FROM taobao_product_prices WHERE product_url=%s AND item_name=%s",
                    (item["product_url"], item["item_name"]),
                )
                .mappings()
                .one()
            )
        assert saved["price"] == item["price"]
        assert saved["scraped_at"] == scraped_at
        persisted_count += 1
        if persisted_count == 1:
            first_saved_item, first_saved_at = item, scraped_at

    scraper = TaobaoBrowserScraper(
        service._update_status,
        threading.Event(),
        auth_state,
        job_id=job_id,
        price_callback=persist_price,
    )
    with playwright_runtime() as playwright:
        with taobao_browser_context(
            playwright, task_name=job_id, headless=False, show_window=False, auth_state=auth_state
        ) as context:
            page = context.pages[0]
            page.goto("https://s.taobao.com/", wait_until="commit", timeout=30000)
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            previous_profile = sys.getprofile()
            sys.setprofile(observe_parse)
            try:
                if fail_on_second:
                    with pytest.raises(TaobaoPricePersistenceError) as error:
                        scraper._scrape_detail(cdp, frame_id, product_url)
                    assert isinstance(error.value.__cause__, DataError)
                else:
                    rows = scraper._scrape_detail(cdp, frame_id, product_url)
            finally:
                sys.setprofile(previous_profile)
    if fail_on_second:
        assert persisted_count == 1
        assert parsed_count == 2, "第二条数据库写入失败后，不得继续解析第三条"
        with db.engine.connect() as connection:
            saved = (
                connection.exec_driver_sql(
                    "SELECT price, scraped_at FROM taobao_product_prices WHERE product_url=%s AND item_name=%s",
                    (first_saved_item["product_url"], first_saved_item["item_name"]),
                )
                .mappings()
                .one()
            )
        assert saved["price"] == first_saved_item["price"]
        assert saved["scraped_at"] == first_saved_at, "第二条失败不能回滚第一条已提交的价格"
        return
    assert len(rows) >= 2, "真实商品至少需要两条规格才能验证先提交再解析下一条"
    assert persisted_count == len(rows), "详情解析已返回，但价格还没有逐条提交到真实数据库"
    # 保留真实商品的最新价格；不注入假接口，也不删除已提交数据。
