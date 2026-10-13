from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.database import db
from app.database.price import taobao_price_db


def test_taobao_price_upsert_updates_existing_real_database_row():
    product_url = "https://item.taobao.com/item.htm?id=999999999999"
    item_name = "数据库集成验证规格"
    first_scraped_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    latest_scraped_at = datetime.now(timezone.utc)

    try:
        taobao_price_db.upsert_prices(
            [
                {
                    "shop_name": "数据库集成验证店铺",
                    "product_url": product_url,
                    "item_name": item_name,
                    "price": Decimal("6.38"),
                }
            ],
            first_scraped_at,
        )
        first = next(item for item in taobao_price_db.list_prices(item_name) if item["itemName"] == item_name)

        taobao_price_db.upsert_prices(
            [
                {
                    "shop_name": "数据库集成验证店铺（已更新）",
                    "product_url": product_url,
                    "item_name": item_name,
                    "price": Decimal("7.88"),
                }
            ],
            latest_scraped_at,
        )
        updated = next(item for item in taobao_price_db.list_prices(item_name) if item["itemName"] == item_name)

        assert updated["id"] == first["id"]
        assert updated["shopName"] == "数据库集成验证店铺（已更新）"
        assert updated["price"] == "7.88"
        assert updated["scrapedAt"] != first["scrapedAt"]
    finally:
        # 集成测试只验证 upsert，不把测试夹具留在后台真实价格列表中。
        db.execute(
            "DELETE FROM taobao_product_prices WHERE product_url=%s AND item_name=%s",
            (product_url, item_name),
        )


def test_taobao_price_list_sorts_complete_query_by_price() -> None:
    product_urls = (
        "https://item.taobao.com/item.htm?id=999999999991",
        "https://item.taobao.com/item.htm?id=999999999992",
    )
    scraped_at = datetime.now(timezone.utc)
    try:
        taobao_price_db.upsert_prices(
            [
                {
                    "shop_name": "排序集成验证店铺",
                    "product_url": product_urls[0],
                    "item_name": "排序集成验证低价",
                    "price": Decimal("3.20"),
                },
                {
                    "shop_name": "排序集成验证店铺",
                    "product_url": product_urls[1],
                    "item_name": "排序集成验证高价",
                    "price": Decimal("12.80"),
                },
            ],
            scraped_at,
        )

        ascending = taobao_price_db.list_prices("排序集成验证", sort_by="price", sort_order="asc")
        descending = taobao_price_db.list_prices("排序集成验证", sort_by="price", sort_order="desc")

        assert [item["price"] for item in ascending] == ["3.20", "12.80"]
        assert [item["price"] for item in descending] == ["12.80", "3.20"]
    finally:
        # 排序测试使用独立商品链接，完成后清除测试夹具。
        db.execute(
            "DELETE FROM taobao_product_prices WHERE product_url IN (%s, %s)",
            product_urls,
        )


def test_taobao_single_price_upsert_is_visible_before_next_item() -> None:
    product_url = "https://item.taobao.com/item.htm?id=999999999993"
    first_item_name = "逐条入库集成验证规格一"
    second_item_name = "逐条入库集成验证规格二"
    scraped_at = datetime.now(timezone.utc)
    try:
        taobao_price_db.upsert_price(
            {
                "shop_name": "逐条入库集成验证店铺",
                "product_url": product_url,
                "item_name": first_item_name,
                "price": Decimal("8.80"),
            },
            scraped_at,
        )

        after_first = taobao_price_db.list_prices("逐条入库集成验证")
        assert [item["itemName"] for item in after_first] == [first_item_name]

        taobao_price_db.upsert_price(
            {
                "shop_name": "逐条入库集成验证店铺",
                "product_url": product_url,
                "item_name": second_item_name,
                "price": Decimal("9.90"),
            },
            scraped_at,
        )

        after_second = taobao_price_db.list_prices("逐条入库集成验证", sort_by="itemName", sort_order="asc")
        assert [item["itemName"] for item in after_second] == [first_item_name, second_item_name]
    finally:
        # 分两次提交并读取，确保第一条不会等待第二条或整批数据完成。
        db.execute("DELETE FROM taobao_product_prices WHERE product_url=%s", (product_url,))
