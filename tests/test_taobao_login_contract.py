from __future__ import annotations

from contextlib import contextmanager
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.app import app
from app.database import db
from app.service import taobao_price_scraper
from app.service.taobao_price_scraper import (
    TaobaoBrowserScraper,
    _first_search_page_url,
    _normalize_search_url,
    _parse_json_text,
)
from app.service.taobao_price_service import TaobaoPriceService
from app.service import taobao_price_service as taobao_price_service_module


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_taobao_price_page_provides_explicit_login_capture_flow() -> None:
    """登录与抓取必须分步执行，避免抓取超时后丢失用户刚完成的登录态。"""
    html = (ROOT_DIR / "app/web/templates/admin/taobao-prices/index.html").read_text(encoding="utf-8")
    javascript = (ROOT_DIR / "app/web/static/admin/js/taobao-prices.js").read_text(encoding="utf-8")
    paths = {route.path for route in app.routes}

    assert 'id="taobao-login-start"' in html
    assert 'id="taobao-login-save"' in html
    assert "/api/admin/taobao-login/start" in paths
    assert "/api/admin/taobao-login/save" in paths
    assert "taobao-login/start" in javascript
    assert "taobao-login/save" in javascript
    assert 'class="taobao-status-grid"' in html
    assert 'data-taobao-sort="price"' in html
    assert 'id="taobao-scrape-keyword"' in html
    assert 'id="taobao-scrape-url"' not in html
    assert "sortOrder" in javascript
    assert "{ url }" in javascript
    assert "waiting_verification" in javascript
    assert "待验证" in javascript
    assert "'taobao-prices'" in (ROOT_DIR / "app/web/static/admin/js/app.js").read_text(encoding="utf-8")


def test_taobao_login_state_has_independent_persistence_table() -> None:
    """Cookies、localStorage 与 sessionStorage 需要独立持久化，不能只依赖临时浏览器进程。"""
    database_sql = (ROOT_DIR / "database.sql").read_text(encoding="utf-8")
    schema_source = (ROOT_DIR / "app/database/schema.py").read_text(encoding="utf-8")

    assert 'CREATE TABLE IF NOT EXISTS "taobao_browser_sessions"' in database_sql
    assert '"taobao_browser_sessions"' in schema_source
    assert '"cookies"' in schema_source
    assert '"session_storage"' in schema_source

    rows = db.fetchall(
        """SELECT column_name
           FROM information_schema.columns
           WHERE table_schema='public' AND table_name='taobao_browser_sessions'"""
    )
    columns = {row["column_name"] for row in rows}
    assert {"profile_name", "cookies", "origins", "session_storage", "captured_at"} <= columns


def test_taobao_detail_script_accepts_json_followed_by_javascript() -> None:
    payload = _parse_json_text('window.__DATA__ = {"skuBase":{"skus":[]},"skuCore":{"sku2info":{}}};next();')

    assert payload == {"skuBase": {"skus": []}, "skuCore": {"sku2info": {}}}


def test_taobao_detail_script_skips_javascript_object_before_sku_json() -> None:
    script = 'const tracker = { enabled: true };window.__DATA__ = {"skuBase":{"skus":[]},"skuCore":{"sku2info":{}}};'

    assert _parse_json_text(script) == {"skuBase": {"skus": []}, "skuCore": {"sku2info": {}}}


def test_taobao_scrape_uses_visible_first_search_page_only() -> None:
    source = (ROOT_DIR / "app/service/taobao_price_scraper.py").read_text(encoding="utf-8")
    first_page = _first_search_page_url("https://s.taobao.com/search?q=kfc&page=7&tab=all")

    assert parse_qs(urlsplit(first_page).query)["page"] == ["1"]
    assert parse_qs(urlsplit(first_page).query)["q"] == ["kfc"]
    assert "headless=False" in source
    assert "show_window=True" in source


def test_taobao_scrape_url_comes_from_page_and_must_be_search_url() -> None:
    normalized = _normalize_search_url("https://s.taobao.com/search?q=kfc&page=8&tab=all")

    parsed = parse_qs(urlsplit(normalized).query)
    assert parsed["q"] == ["kfc"]
    assert parsed["page"] == ["1"]
    assert parsed["tab"] == ["all"]
    with pytest.raises(ValueError, match="请输入"):
        _normalize_search_url("")
    with pytest.raises(ValueError, match="s.taobao.com"):
        _normalize_search_url("https://item.taobao.com/item.htm?id=123456789")


def test_taobao_detail_requests_wait_at_least_one_second(monkeypatch) -> None:
    class Clock:
        now = 100.0
        slept = 0.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds
            self.slept += seconds

    clock = Clock()
    monkeypatch.setattr(taobao_price_scraper.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(taobao_price_scraper.time, "sleep", clock.sleep)
    scraper = TaobaoBrowserScraper(lambda **_kwargs: None, threading.Event(), {}, job_id="interval-test")

    scraper._wait_for_detail_request_slot()
    first_started = scraper._last_detail_request_started
    scraper._wait_for_detail_request_slot()

    assert scraper._last_detail_request_started - first_started >= 1.0
    assert clock.slept >= 1.0


def test_taobao_detail_uses_chrome_network_link_request_instead_of_browser_navigation() -> None:
    document = """<html><head><title>测试商品</title></head><body><script>
window.__DATA__ = {
  "skuBase": {
    "props": [{"pid": "1", "values": [{"vid": "11", "name": "套餐一"}]}],
    "skus": [{"skuId": "100", "propPath": "1:11"}]
  },
  "skuCore": {"sku2info": {"100": {"price": {"priceText": "9.90"}}}},
  "sellerNick": "测试店铺",
  "title": "测试商品"
};
</script></body></html>"""

    class DetailCdp:
        calls: list[tuple[str, dict]] = []

        def send(self, command: str, payload: dict):
            self.calls.append((command, payload))
            if command == "Network.loadNetworkResource":
                return {
                    "resource": {
                        "success": True,
                        "httpStatusCode": 200,
                        "stream": "detail-stream",
                    }
                }
            if command == "IO.read":
                return {"data": document, "eof": True}
            if command == "IO.close":
                return {}
            raise AssertionError(f"未预期的 CDP 命令：{command}")

    cdp = DetailCdp()
    scraper = TaobaoBrowserScraper(lambda **_kwargs: None, threading.Event(), {}, job_id="request-test")
    scraper._last_detail_request_started = -100.0

    rows = scraper._scrape_detail(
        cdp,
        "search-frame",
        "https://item.taobao.com/item.htm?id=123456789",
    )

    assert cdp.calls[0] == (
        "Network.loadNetworkResource",
        {
            "frameId": "search-frame",
            "url": "https://item.taobao.com/item.htm?id=123456789",
            "options": {"disableCache": False, "includeCredentials": True},
        },
    )
    assert rows == [
        {
            "shop_name": "测试店铺",
            "product_url": "https://item.taobao.com/item.htm?id=123456789",
            "item_name": "套餐一",
            "price": taobao_price_scraper.Decimal("9.90"),
        }
    ]
    assert cdp.calls[-1] == ("IO.close", {"handle": "detail-stream"})


def test_taobao_detail_recognizes_captcha_response_headers() -> None:
    class CaptchaCdp:
        def send(self, command: str, _payload: dict):
            if command == "Network.loadNetworkResource":
                return {
                    "resource": {
                        "success": True,
                        "httpStatusCode": 200,
                        "headers": {"bxpunish": "1", "x-sec-reason": "FAIL_SYS_CAPTCHA"},
                    }
                }
            raise AssertionError(f"未预期的 CDP 命令：{command}")

    scraper = TaobaoBrowserScraper(lambda **_kwargs: None, threading.Event(), {}, job_id="captcha-test")
    scraper._last_detail_request_started = -100.0

    with pytest.raises(taobao_price_scraper.TaobaoVerificationRequired, match="验证码"):
        scraper._scrape_detail(
            CaptchaCdp(),
            "search-frame",
            "https://item.taobao.com/item.htm?id=123456789",
        )


def test_taobao_verification_opens_page_and_retries_current_product(monkeypatch) -> None:
    product_url = "https://item.taobao.com/item.htm?id=123456789"
    expected_rows = [
        {
            "shop_name": "验证完成店铺",
            "product_url": product_url,
            "item_name": "验证完成规格",
            "price": taobao_price_scraper.Decimal("9.90"),
        }
    ]
    status_events: list[dict] = []

    class SearchPage:
        url = "https://s.taobao.com/search?q=test&page=1"

        def on(self, *_args):
            return None

        def remove_listener(self, *_args):
            return None

        def goto(self, *_args, **_kwargs):
            return None

        def bring_to_front(self):
            return None

    class VerificationPage:
        def __init__(self):
            self.url = product_url
            self.opened_urls: list[str] = []
            self.brought_to_front = False
            self.closed = False
            self.verified = False
            self.wait_count = 0

        def goto(self, url: str, **_kwargs):
            self.opened_urls.append(url)
            self.url = product_url

        def bring_to_front(self):
            self.brought_to_front = True

        def title(self):
            return "商品详情" if self.verified else "安全验证"

        def locator(self, selector: str):
            assert selector == "body"
            return self

        def inner_text(self, **_kwargs):
            return "商品详情" if self.verified else "请完成验证"

        def wait_for_timeout(self, _timeout: int):
            self.wait_count += 1
            self.verified = True

        def is_closed(self):
            return self.closed

        def close(self):
            self.closed = True

    class Cdp:
        def send(self, command: str, _payload: dict | None = None):
            if command == "Network.enable":
                return {}
            if command == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "search-frame"}}}
            raise AssertionError(f"未预期的 CDP 命令：{command}")

    verification_page = VerificationPage()

    class Context:
        pages = [SearchPage()]

        def new_page(self):
            return verification_page

        def new_cdp_session(self, _page):
            return Cdp()

    @contextmanager
    def fake_playwright_runtime():
        yield object()

    @contextmanager
    def fake_browser_context(*_args, **_kwargs):
        yield Context()

    monkeypatch.setattr(taobao_price_scraper, "playwright_runtime", fake_playwright_runtime)
    monkeypatch.setattr(taobao_price_scraper, "taobao_browser_context", fake_browser_context)
    scraper = TaobaoBrowserScraper(
        lambda **changes: status_events.append(changes),
        threading.Event(),
        {},
        job_id="verification-retry-test",
        target_url="https://s.taobao.com/search?q=test&page=1",
    )
    monkeypatch.setattr(scraper, "_discover_links", lambda *_args: [product_url])
    attempts = 0

    def scrape_detail(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise taobao_price_scraper.TaobaoVerificationRequired("淘宝要求验证码")
        return expected_rows

    monkeypatch.setattr(scraper, "_scrape_detail", scrape_detail)

    rows, product_count, failed_count = scraper.scrape()

    assert rows == expected_rows
    assert product_count == 1
    assert failed_count == 0
    assert attempts == 2
    assert verification_page.opened_urls == [product_url]
    assert verification_page.brought_to_front is True
    assert verification_page.closed is True
    assert verification_page.wait_count == 1
    assert any(event.get("phase") == "waiting_verification" for event in status_events)


def test_taobao_service_persists_each_price_before_scrape_continues(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    received_targets: list[str] = []
    price_items = [
        {
            "shop_name": "即时写入店铺",
            "product_url": "https://item.taobao.com/item.htm?id=100000000001",
            "item_name": "即时写入规格一",
            "price": taobao_price_scraper.Decimal("8.80"),
        },
        {
            "shop_name": "即时写入店铺",
            "product_url": "https://item.taobao.com/item.htm?id=100000000001",
            "item_name": "即时写入规格二",
            "price": taobao_price_scraper.Decimal("9.90"),
        },
    ]

    class Scraper:
        def __init__(self, *_args, price_callback, target_url, **_kwargs):
            self.price_callback = price_callback
            received_targets.append(target_url)

        def scrape(self):
            for item in price_items:
                self.price_callback(item)
                events.append(("continue", item["item_name"]))
            return price_items, 1, 0

    def persist(item, _scraped_at):
        events.append(("saved", item["item_name"]))
        return 1

    monkeypatch.setattr(taobao_price_service_module, "TaobaoBrowserScraper", Scraper)
    monkeypatch.setattr(taobao_price_service_module.taobao_price_db, "upsert_price", persist)
    monkeypatch.setattr(
        taobao_price_service_module.taobao_price_db,
        "upsert_prices",
        lambda *_args, **_kwargs: pytest.fail("采集任务不应再批量写入价格"),
    )
    service = TaobaoPriceService()

    target_url = "https://s.taobao.com/search?q=test&page=1"
    service._run("immediate-write-test", {}, target_url)

    assert events == [
        ("saved", "即时写入规格一"),
        ("continue", "即时写入规格一"),
        ("saved", "即时写入规格二"),
        ("continue", "即时写入规格二"),
    ]
    assert service.status()["state"] == "succeeded"
    assert service.status()["itemCount"] == 2
    assert received_targets == [target_url]
