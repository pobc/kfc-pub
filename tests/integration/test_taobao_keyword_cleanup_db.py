"""真实 PostgreSQL 回归：清理一个关键词时必须保留其他关键词及无归属记录。"""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.database import db
from app.database.price import taobao_price_db
from app.service.taobao_price_service import TaobaoPriceService


def _cutoff():
    assert db.fetchone("SELECT current_database() AS name")["name"] == "kfc"
    return db.fetchone(
        """SELECT date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')
           AT TIME ZONE 'Asia/Shanghai' AS cutoff"""
    )["cutoff"]


def test_cleanup_only_deletes_old_prices_of_the_requested_keyword():
    marker = f"关键词隔离验证-{uuid4().hex[:10]}"
    keyword = marker + "肯德基100%_套餐'"
    other_keyword = keyword + "早餐"
    cutoff = _cutoff()
    for index, (source, when) in enumerate(
        [
            (keyword, cutoff - timedelta(microseconds=1)),
            (keyword, cutoff),
            (other_keyword, cutoff - timedelta(days=1)),
            ("", cutoff - timedelta(days=1)),
        ]
    ):
        taobao_price_db.upsert_price(
            {
                "shop_name": marker,
                "item_name": f"{marker}-{index}",
                "price": "6.00",
                "product_url": f"https://item.taobao.com/item.htm?id={uuid4().int}",
                "search_keyword": source,
            },
            when,
        )

    result = taobao_price_db.delete_prices_before_today(keyword)
    assert result["deleted_count"] == 1
    assert result["cutoff"] == cutoff
    remaining = taobao_price_db.list_prices(marker, sort_by="itemName", sort_order="asc")
    assert [row["itemName"] for row in remaining] == [f"{marker}-1", f"{marker}-2", f"{marker}-3"]
    assert taobao_price_db.delete_prices_before_today(keyword)["deleted_count"] == 0


def test_same_product_keeps_independent_prices_for_different_keywords():
    marker = f"共享商品验证-{uuid4().hex[:10]}"
    cutoff = _cutoff()
    item = {
        "shop_name": marker,
        "item_name": marker,
        "price": "6.00",
        "product_url": f"https://item.taobao.com/item.htm?id={uuid4().int}",
    }
    for keyword, price in [(marker + "甲", "6.00"), (marker + "乙", "7.00"), (marker + "乙", "8.00")]:
        taobao_price_db.upsert_price({**item, "price": price, "search_keyword": keyword}, cutoff - timedelta(days=1))
    assert len(taobao_price_db.list_prices(marker)) == 2
    taobao_price_db.delete_prices_before_today(marker + "甲")
    remaining = taobao_price_db.list_prices(marker)
    assert len(remaining) == 1
    assert remaining[0]["price"] == "8.00"
    assert remaining[0]["searchKeyword"] == marker + "乙"


@pytest.mark.parametrize("keyword", ["", "   ", "词" * 201])
def test_cleanup_rejects_missing_or_invalid_keyword(keyword):
    before = db.fetchone("SELECT COUNT(*) AS count FROM taobao_product_prices")["count"]
    with pytest.raises(ValueError, match="关键词"):
        taobao_price_db.delete_prices_before_today(keyword)
    assert db.fetchone("SELECT COUNT(*) AS count FROM taobao_product_prices")["count"] == before


def test_worker_rejects_ambiguous_keyword_without_deleting_prices(caplog):
    before = db.fetchone("SELECT COUNT(*) AS count FROM taobao_product_prices")["count"]
    service = TaobaoPriceService()
    with caplog.at_level("INFO", logger="kfc.taobao_price"):
        service._run("无关键词清理保护验证", {}, "https://s.taobao.com/search?q=甲&q=乙")
    assert service.status()["state"] == "failed"
    assert service.status()["deletedCount"] == 0
    assert db.fetchone("SELECT COUNT(*) AS count FROM taobao_product_prices")["count"] == before
    assert "淘宝价格采集任务失败" in caplog.text
