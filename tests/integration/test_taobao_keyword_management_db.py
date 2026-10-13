"""用本机 kfc 数据库和真实 HTTP 服务验证关键词管理、筛选与清理边界。"""

from __future__ import annotations

import os
import hashlib
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import uvicorn

from app.config import settings
from app.database import db
from app.database.price import taobao_keyword_db, taobao_price_db
from app.database.user import admin_session_db
from app.service.auth_service import local_admin_bypass


@pytest.fixture(scope="module")
def admin_http_client():
    # 使用真实管理员会话，测试在关闭本机免登录的环境中也走正式鉴权。
    user = db.fetchone("SELECT id FROM users WHERE enabled=true ORDER BY username LIMIT 1")
    assert user, "真实数据库中至少需要一个可用管理员"
    token = secrets.token_urlsafe(32)
    admin_session_db.create_admin_session(
        user_id=str(user["id"]),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        ttl_minutes=10,
    )
    with requests.Session() as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest.fixture(scope="module")
def admin_http_url():
    assert db.fetchone("SELECT current_database() AS name")["name"] == "kfc"
    # 独立端口使用真实路由；关闭后台生命周期，避免重复启动无关的核销任务。
    from main import app

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", access_log=False, log_level="warning"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "真实 HTTP 测试服务未启动"
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()


def test_keyword_crud_through_real_http_and_database(admin_http_url, admin_http_client):
    marker = f"关键词集成验证-{uuid.uuid4().hex[:10]}"
    endpoint = f"{admin_http_url}/api/admin/taobao-keywords"
    if not local_admin_bypass():
        assert requests.get(endpoint, timeout=10).status_code == 401
    payload = {"keyword": f"  {marker}  ", "url": "https://s.taobao.com/search?q=肯德基&page=2"}
    response = admin_http_client.post(endpoint, json=payload, timeout=10)
    assert response.status_code == 200, response.text
    created = response.json()["data"]
    assert isinstance(created["id"], int) and created["id"] > 0
    assert created["keyword"] == marker
    assert created["createdAt"] and created["updatedAt"]
    assert taobao_keyword_db.get_keyword(created["id"])["keyword"] == marker

    detail_url = f"{endpoint}/{created['id']}"
    assert admin_http_client.get(detail_url, timeout=10).json()["data"] == created
    listed = admin_http_client.get(endpoint, params={"search": marker}, timeout=10).json()["data"]["items"]
    assert [item["id"] for item in listed] == [created["id"]]
    updated = admin_http_client.put(
        detail_url,
        json={"keyword": marker + "-已修改", "url": "https://s.taobao.com/search?q=汉堡"},
        timeout=10,
    ).json()["data"]
    assert updated["createdAt"] == created["createdAt"]
    assert updated["updatedAt"] > created["updatedAt"]
    assert updated["keyword"] == marker + "-已修改"
    assert taobao_keyword_db.get_keyword(created["id"])["url"] == updated["url"]

    # 保留新增和修改的数据，另外创建一条记录专门验证删除用例。
    to_delete = admin_http_client.post(
        endpoint, json={"keyword": marker + "-删除用例", "url": payload["url"]}, timeout=10
    ).json()["data"]
    assert to_delete["id"] > created["id"]
    delete_url = f"{endpoint}/{to_delete['id']}"
    assert admin_http_client.delete(delete_url, timeout=10).status_code == 200
    assert taobao_keyword_db.get_keyword(to_delete["id"]) is None
    assert admin_http_client.get(delete_url, timeout=10).status_code == 404
    assert admin_http_client.delete(delete_url, timeout=10).status_code == 404
    assert admin_http_client.put(delete_url, json=payload, timeout=10).status_code == 404
    assert admin_http_client.get(f"{endpoint}/0", timeout=10).status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"keyword": "  ", "url": "https://s.taobao.com/search?q=肯德基"},
        {"keyword": "验证", "url": "javascript:alert(1)"},
        {"keyword": "验证", "url": "https://example.com/search?q=肯德基"},
        {"keyword": "验" * 201, "url": "https://s.taobao.com/search?q=肯德基"},
        {"keyword": "验证", "url": "https://s.taobao.com/", "id": 99},
    ],
)
def test_keyword_input_validation(admin_http_url, admin_http_client, body):
    response = admin_http_client.post(f"{admin_http_url}/api/admin/taobao-keywords", json=body, timeout=10)
    assert response.status_code == 400


@pytest.fixture(scope="module")
def price_search_records():
    """保留真实搜索记录，包含商品项命中和仅其他字段命中的干扰数据。"""
    marker = f"搜索框验证-{uuid.uuid4().hex[:10]}"
    words = [marker + "鸡腿堡", marker + "可乐"]
    names = [words[0], words[1], "加餐".join(words), marker + "薯条", marker + "100%_", marker + "100XY"]
    for index, name in enumerate(names):
        taobao_price_db.upsert_price(
            {
                "product_url": f"https://item.taobao.com/item.htm?id={uuid.uuid4().int}&check={words[0]}",
                "item_name": name,
                "shop_name": words[1],
                "search_keyword": words[0],
                "price": str(index + 1),
            },
            datetime.now(timezone.utc),
        )
    return words, names


def test_search_box_matches_any_item_word_only(admin_http_url, admin_http_client, price_search_records):
    words, names = price_search_records
    for search, expected in [
        (" ".join(words), names[:3]),
        (f"  {words[0]}   {words[1]} {words[0]}  ", names[:3]),
        ("　".join(words), names[:3]),
        (words[0], [names[0], names[2]]),
        (names[4], [names[4]]),
    ]:
        response = admin_http_client.get(
            f"{admin_http_url}/api/admin/taobao-prices",
            params={"search": search, "sortBy": "price", "sortOrder": "asc"},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert [item["itemName"] for item in data["items"]] == expected
        assert data["summary"]["itemCount"] == data["summary"]["productCount"] == len(expected)


@pytest.mark.parametrize("search", ["", "   ", "　"])
def test_empty_search_box_returns_all_data(admin_http_url, admin_http_client, search):
    expected = db.fetchone("SELECT COUNT(*) AS count FROM taobao_product_prices")["count"]
    response = admin_http_client.get(
        f"{admin_http_url}/api/admin/taobao-prices", params={"search": search}, timeout=10
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["items"]) == data["summary"]["itemCount"] == expected
    assert len({item["id"] for item in data["items"]}) == expected


@pytest.mark.skipif(os.getenv("TAOBAO_ADMIN_BROWSER_TEST") != "1", reason="按需启用真实 Chrome 页面验证")
def test_left_search_box_in_browser(admin_http_url, admin_http_client, price_search_records):
    """只操作左侧搜索框，验证多词命中、无匹配及删空后恢复全部记录。"""
    from playwright.sync_api import expect, sync_playwright

    words, names = price_search_records
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(extra_http_headers={"Authorization": admin_http_client.headers["Authorization"]})
            page.goto(f"{admin_http_url}/{settings().admin_web_md5}/admin/taobao-prices")
            page.locator("#global-loading").wait_for(state="hidden")
            search = page.locator("#taobao-price-search")
            for value, expected in [(" ".join(words), names[:3]), (words[0] + "不存在", [])]:
                search.fill(value)
                with page.expect_response(lambda response: "/api/admin/taobao-prices?" in response.url) as response:
                    page.locator("#taobao-price-filter button[type=submit]").click()
                assert response.value.status == 200
                data = response.value.json()["data"]
                assert [item["itemName"] for item in data["items"]] == expected
                expect(page.locator("#taobao-price-rows .taobao-price-row")).to_have_count(len(expected))
                expect(page.locator("#taobao-price-keywords")).to_have_value("")
                expect(page.locator("[data-taobao-keyword]:checked")).to_have_count(0)

            search.press("ControlOrMeta+A")
            search.press("Backspace")
            with page.expect_response(lambda response: "/api/admin/taobao-prices?" in response.url) as response:
                page.locator("#taobao-price-filter button[type=submit]").click()
            data = response.value.json()["data"]
            assert len(data["items"]) == data["summary"]["itemCount"]
            expect(page.locator("#taobao-price-rows .taobao-price-row")).to_have_count(data["summary"]["itemCount"])
        finally:
            browser.close()


def test_multiple_keywords_filter_before_sort_limit_and_summary(admin_http_url, admin_http_client):
    marker = f"多词验证-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    for index, (name, shop, price) in enumerate(
        [
            ("香辣鸡腿堡", "主店", "8.80"),
            ("可乐", "主店", "3.20"),
            ("薯条", "分店", "5.00"),
            ("百分号100%_规格", "主店", "7.00"),
        ]
    ):
        taobao_price_db.upsert_price(
            {
                "product_url": f"https://item.taobao.com/item.htm?id={uuid.uuid4().int}&check={marker}-{index}",
                "item_name": marker + name,
                "search_keyword": marker,
                "shop_name": marker + shop,
                "price": price,
            },
            now,
        )
    endpoint = f"{admin_http_url}/api/admin/taobao-prices"
    response = admin_http_client.get(
        endpoint,
        params=[
            ("search", marker),
            ("keywords", "鸡腿堡"),
            ("keywords", "可乐"),
            ("sortBy", "price"),
            ("sortOrder", "asc"),
        ],
        timeout=10,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["price"] for item in data["items"]] == ["3.20", "8.80"]
    assert data["summary"]["itemCount"] == data["summary"]["productCount"] == 2
    limited = taobao_price_db.list_prices(marker, limit=1, sort_by="price", keywords=["鸡腿堡", "可乐"])
    assert [item["price"] for item in limited] == ["8.80"]
    assert taobao_price_db.list_prices(marker, keywords=["主店"]) == []
    assert taobao_price_db.list_prices(marker, keywords=[f"{marker}-2"]) == []
    assert len(taobao_price_db.list_prices(marker, keywords=["100%_"])) == 1
    assert len(taobao_price_db.list_prices(marker, keywords=["可乐", "可乐"])) == 1
    assert taobao_price_db.list_prices(marker, keywords=["' OR 1=1 --"]) == []
    assert len(taobao_price_db.list_prices(marker)) == 4
    assert (
        admin_http_client.get(endpoint, params=[("keywords", str(i)) for i in range(21)], timeout=10).status_code == 400
    )
    assert admin_http_client.get(endpoint, params={"keywords": "验" * 201}, timeout=10).status_code == 400


def test_price_filter_matches_source_keyword_with_unrelated_product_text(admin_http_url, admin_http_client):
    """商品规格不包含搜索词时，仍能按采集来源查出价格，并与普通搜索取交集。"""
    marker = f"来源筛选-{uuid.uuid4().hex[:10]}"
    sources = [marker + "十翅一桶", marker + "100%_套餐"]
    now = datetime.now(timezone.utc)
    for source, name, price in zip(sources, ["单人兑换券", "双人兑换券"], ["12.00", "18.00"]):
        taobao_price_db.upsert_price(
            {
                "product_url": f"https://item.taobao.com/item.htm?id={uuid.uuid4().int}",
                "item_name": name,
                "shop_name": "来源筛选专用店铺",
                "search_keyword": source,
                "price": price,
            },
            now,
        )

    endpoint = f"{admin_http_url}/api/admin/taobao-prices"
    response = admin_http_client.get(endpoint, params={"sourceKeywords": sources[0]}, timeout=10)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["searchKeyword"] for item in data["items"]] == [sources[0]]
    assert data["summary"]["itemCount"] == data["summary"]["productCount"] == 1
    assert data["summary"]["latestScrapedAt"] == data["items"][0]["scrapedAt"]

    params = [("sourceKeywords", source) for source in sources]
    combined = admin_http_client.get(endpoint, params=params, timeout=10).json()["data"]
    assert {item["searchKeyword"] for item in combined["items"]} == set(sources)
    assert combined["summary"]["itemCount"] == combined["summary"]["productCount"] == 2
    narrowed = admin_http_client.get(endpoint, params=params + [("search", "双人")], timeout=10).json()["data"]
    assert [item["searchKeyword"] for item in narrowed["items"]] == [sources[1]]
    assert narrowed["summary"]["itemCount"] == 1

    # 来源勾选不能让不匹配的商品项搜索失效，文本条件与来源条件必须取交集。
    mismatch = admin_http_client.get(
        endpoint, params=params + [("keywords", marker + "不存在的商品项")], timeout=10
    ).json()["data"]
    assert mismatch["items"] == []
    assert mismatch["summary"]["itemCount"] == 0
    item_only = admin_http_client.get(endpoint, params={"keywords": sources[0]}, timeout=10).json()["data"]
    assert item_only["items"] == []


def test_source_filter_matches_only_search_keyword(admin_http_url, admin_http_client, price_search_records):
    words, names = price_search_records
    endpoint = f"{admin_http_url}/api/admin/taobao-prices"
    # 同一词只出现在店铺名和部分商品项中，来源筛选不能借这些字段命中。
    for source in (words[1], words[0][:-1]):
        response = admin_http_client.get(endpoint, params={"sourceKeywords": source}, timeout=10)
        assert response.status_code == 200
        assert response.json()["data"]["summary"]["itemCount"] == 0
    response = admin_http_client.get(endpoint, params={"sourceKeywords": words[0]}, timeout=10)
    assert {item["itemName"] for item in response.json()["data"]["items"]} == set(names)
    for text_field in ("search", "keywords"):
        response = admin_http_client.get(
            endpoint, params={"sourceKeywords": words[0], text_field: words[1]}, timeout=10
        )
        assert {item["itemName"] for item in response.json()["data"]["items"]} == {names[1], names[2]}


def test_cleanup_keeps_beijing_midnight_and_today_rows(admin_http_url, admin_http_client):
    marker = f"清理边界验证-{uuid.uuid4().hex[:10]}"
    cutoff = db.fetchone(
        """SELECT date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')
           AT TIME ZONE 'Asia/Shanghai' AS cutoff"""
    )["cutoff"]
    for name, when in [
        ("昨日", cutoff - timedelta(microseconds=1)),
        ("零点", cutoff),
        ("今日", cutoff + timedelta(hours=1)),
    ]:
        taobao_price_db.upsert_price(
            {
                "product_url": f"https://item.taobao.com/item.htm?id={uuid.uuid4().int}",
                "item_name": marker + name,
                "shop_name": marker,
                "search_keyword": marker,
                "price": "1.00",
            },
            when,
        )

    invalid = admin_http_client.post(
        f"{admin_http_url}/api/admin/taobao-prices/scrape",
        json={"url": "https://example.com/"},
        timeout=10,
    )
    assert invalid.status_code == 400
    assert len(taobao_price_db.list_prices(marker)) == 3, "无效任务不能提前清理历史数据"
    keywords_before = taobao_keyword_db.list_keywords()
    result = taobao_price_db.delete_prices_before_today(marker)
    assert result["cutoff"] == cutoff
    assert result["deleted_count"] == 1
    remaining = taobao_price_db.list_prices(marker, sort_by="scrapedAt", sort_order="asc")
    assert [row["itemName"] for row in remaining] == [marker + "零点", marker + "今日"]
    assert taobao_keyword_db.list_keywords() == keywords_before
    assert taobao_price_db.delete_prices_before_today(marker)["deleted_count"] == 0


@pytest.mark.skipif(os.getenv("TAOBAO_ADMIN_BROWSER_TEST") != "1", reason="按需启用真实 Chrome 页面验证")
def test_keyword_management_and_price_filter_in_browser(admin_http_url, admin_http_client):
    from playwright.sync_api import sync_playwright

    marker = f"页面验证-{uuid.uuid4().hex[:8]}"
    source_keyword = marker + "已编辑"
    taobao_price_db.upsert_price(
        {
            "product_url": f"https://item.taobao.com/item.htm?id={uuid.uuid4().int}",
            "item_name": "单份套餐兑换券",
            "shop_name": "页面来源验证店铺",
            "search_keyword": source_keyword,
            "price": "15.50",
        },
        datetime.now(timezone.utc),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(
                viewport={"width": 1440, "height": 1000},
                extra_http_headers={"Authorization": admin_http_client.headers["Authorization"]},
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{admin_http_url}/{settings().admin_web_md5}/admin/taobao-keywords")
            page.locator("#global-loading").wait_for(state="hidden")
            page.locator("#taobao-keyword-name").fill(marker)
            page.locator("#taobao-keyword-url").fill(f"https://s.taobao.com/search?q={marker}")
            page.locator("#taobao-keyword-save").click()
            row = page.locator(".taobao-keyword-row", has_text=marker)
            row.wait_for()
            row.get_by_role("button", name="编辑").click()
            page.locator("#taobao-keyword-name").fill(marker + "已编辑")
            page.locator("#taobao-keyword-url").fill(f"https://s.taobao.com/search?q={source_keyword}")
            page.locator("#taobao-keyword-save").click()
            page.locator(".taobao-keyword-row strong", has_text=marker + "已编辑").wait_for()
            page.locator("#taobao-keyword-search").fill(marker)
            page.locator("#taobao-keyword-filter button[type=submit]").click()
            page.wait_for_function("document.getElementById('taobao-keyword-count').textContent === '共 1 条'")
            output = Path(".kfc/verification")
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / "taobao-keywords-desktop.png"), full_page=False)
            page.locator('[data-section="taobao-prices"]').click()
            assert page.locator("#taobao-scrape-url").count() == 0
            assert page.locator("#taobao-price-scrape").is_disabled()
            option = page.locator("#taobao-scrape-keyword option", has_text=marker + "已编辑")
            selected_id = option.get_attribute("value")
            page.locator("#taobao-scrape-keyword").select_option(selected_id)
            assert page.locator("#taobao-scrape-keyword").input_value() == selected_id
            login = admin_http_client.get(f"{admin_http_url}/api/admin/taobao-login/status", timeout=10).json()["data"]
            assert page.locator("#taobao-price-scrape").is_enabled() == bool(login["saved"])
            page.locator("#taobao-scrape-keyword").select_option("")
            assert page.locator("#taobao-price-scrape").is_disabled()
            page.locator("#taobao-scrape-keyword").select_option(selected_id)
            page.locator("#taobao-price-keywords").fill("鸡腿堡 可乐")
            with page.expect_response(lambda response: "/api/admin/taobao-prices?" in response.url) as response:
                page.locator("#taobao-price-filter button[type=submit]").click()
            assert response.value.status == 200
            assert response.value.url.count("keywords=") == 2
            page.locator("#taobao-price-rows").get_by_text("多词验证-", exact=False).first.wait_for()
            page.screenshot(path=str(output / "taobao-prices-desktop.png"), full_page=False)
            page.locator("#taobao-price-reset").click()
            assert page.locator("#taobao-price-keywords").input_value() == ""
            checkbox = page.get_by_label(source_keyword, exact=True)
            checkbox.check()
            # 来源单独传参，商品项不包含来源文字时也能按来源查出。
            with page.expect_response(
                lambda response: "/api/admin/taobao-prices?" in response.url and "sourceKeywords=" in response.url
            ) as selected_response:
                page.locator("#taobao-price-filter button[type=submit]").click()
            assert selected_response.value.status == 200
            assert selected_response.value.url.count("sourceKeywords=") == 1
            assert "keywords=" not in selected_response.value.url
            data = selected_response.value.json()["data"]
            assert [item["searchKeyword"] for item in data["items"]] == [source_keyword]
            assert data["summary"]["itemCount"] == data["summary"]["productCount"] == 1
            page.wait_for_function("document.getElementById('taobao-price-item-count').textContent === '1'")
            assert page.locator("#taobao-price-rows .taobao-price-row").count() == 1
            page.screenshot(path=str(output / "taobao-prices-source-keyword.png"), full_page=False)
            # 两个文本输入都只查商品项；勾选来源后，文字仍必须有独立筛选效果。
            for selector in ("#taobao-price-search", "#taobao-price-keywords"):
                for value, expected_count in [("单份 不存在的商品项", 1), (marker + "不存在的商品项", 0), ("", 1)]:
                    page.locator(selector).fill(value)
                    with page.expect_response(lambda response: "/api/admin/taobao-prices?" in response.url) as response:
                        page.locator("#taobao-price-filter button[type=submit]").click()
                    assert response.value.status == 200
                    data = response.value.json()["data"]
                    assert len(data["items"]) == data["summary"]["itemCount"] == expected_count
                    assert all(item["searchKeyword"] == source_keyword for item in data["items"])
                    assert page.locator("[data-taobao-keyword]:checked").count() == 1
            page.locator("#taobao-price-reset").click()
            assert page.locator("[data-taobao-keyword]:checked").count() == 0
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "taobao-prices-mobile.png"), full_page=False)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator('[data-section="taobao-keywords"]').click()
            page.locator("#taobao-keyword-search").fill(marker)
            page.locator("#taobao-keyword-filter button[type=submit]").click()
            page.screenshot(path=str(output / "taobao-keywords-mobile.png"), full_page=False)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            # 删除专用页面记录，接口用例创建和修改的记录继续保留。
            page.once("dialog", lambda dialog: dialog.accept())
            row.get_by_role("button", name="删除").click()
            page.wait_for_function("document.getElementById('taobao-keyword-count').textContent === '共 0 条'")
            assert errors == []
        finally:
            browser.close()
