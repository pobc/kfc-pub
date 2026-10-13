"""回放 2026-10-09 商品 876437121568 的真实规格响应，不构造模拟价格。"""

import json
from decimal import Decimal
from pathlib import Path

from app.service.taobao_price_scraper import _prices_from_payload


def test_real_item_minimum_excludes_sold_out_skus() -> None:
    path = Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _prices_from_payload(payload, "")[2]

    assert min(price for _, price in rows) == Decimal("13.90")
    assert len(rows) == 43


def test_real_sold_out_subset_does_not_fall_back_to_product_starting_price() -> None:
    path = Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    # 只截取真实响应中的无货规格，保留原始汇总价，验证筛完不能回退成可售商品。
    info = payload["skuCore"]["sku2info"]
    payload["skuBase"]["skus"] = [sku for sku in payload["skuBase"]["skus"] if info[sku["skuId"]]["quantity"] == 0]

    assert _prices_from_payload(payload, "")[2] == []


def test_real_sold_out_prices_never_reach_persistence_callback() -> None:
    path = Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    emitted = []
    unavailable = []
    _, _, rows = _prices_from_payload(
        payload, "", lambda shop, name, price: emitted.append((name, price)), unavailable.append
    )

    assert emitted == rows
    assert len(unavailable) == 13
    assert set(unavailable).isdisjoint(name for name, _ in rows)
    assert all(price >= Decimal("13.90") for _, price in emitted)


def test_real_stock_text_still_excludes_unavailable_when_quantity_is_absent() -> None:
    path = Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for info in payload["skuCore"]["sku2info"].values():
        del info["quantity"]

    rows = _prices_from_payload(payload, "")[2]

    assert len(rows) == 43
    assert min(price for _, price in rows) == Decimal("13.90")


def test_missing_stock_fields_do_not_invent_unavailability() -> None:
    path = Path(__file__).parents[1] / "fixtures/taobao_876437121568_price.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    info = payload["skuCore"]["sku2info"]
    payload["skuBase"]["skus"] = [sku for sku in payload["skuBase"]["skus"] if info[sku["skuId"]]["quantity"] > 0]
    for value in info.values():
        del value["quantity"]
        del value["quantityText"]

    assert len(_prices_from_payload(payload, "")[2]) == 43
