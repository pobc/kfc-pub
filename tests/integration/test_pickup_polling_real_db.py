from __future__ import annotations

import secrets
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.api.app import create_app
from app.database import access_links, db, orders, products
from app.service import checkout_service
from app.web import mount_web


def _create_link() -> str:
    assert db.engine.url.database == "kfc"
    product = products.create_product(
        {"name": f"取餐号轮询回归-{secrets.token_hex(6)}", "parentLinkId": "__AUTO_MATCH_BY_NAME__"}
    )
    return access_links.create_access_links(product["id"], 1)[0]["accessId"]


@pytest.mark.parametrize("stage", ["SUBMITTED", "PREPARING", "READY", "COMPLETED"])
@pytest.mark.parametrize("number_field", ["queueNumber", "pickupCode"])
def test_saved_pickup_number_never_requires_kfc_account(stage: str, number_field: str) -> None:
    public_id = _create_link()
    orders.save_order_detail(
        public_id,
        f"pickup-cache-{public_id}",
        {"stage": stage, number_field: " A0101 ", "store": {"name": "取餐号回归餐厅"}},
    )
    before = orders.get_order_for_link(public_id)
    # 链接未绑定 KFC 账号，任何误入上游查询的行为都会失败，不能依靠模拟接口蒙混过关。
    assert access_links.get_link(public_id).get("account_id") is None
    for _ in range(2):
        result = checkout_service.get_order_info(public_id)
        assert result[number_field] == "A0101"
        assert result["stopPolling"] is True
        assert result["store"]["name"] == "取餐号回归餐厅"
        assert "externalOrderId" not in result
    assert orders.get_order_for_link(public_id)["last_queried_at"] == before["last_queried_at"]


@pytest.mark.parametrize("stage", ["SUBMITTED", "PREPARING", "READY"])
def test_no_number_keeps_polling_even_when_ready(stage: str) -> None:
    public_id = _create_link()
    saved = orders.save_order_detail(public_id, f"pickup-wait-{public_id}", {"stage": stage, "queueNumber": "  "})
    assert saved["queueNumber"] == ""
    assert saved["stopPolling"] is False


@pytest.fixture
def public_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    # 使用完整应用及真实数据库，但不启动会消费其他用户任务的后台 Worker。
    app = create_app()
    mount_web(app)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def test_browser_spins_polls_then_stops_after_number_is_saved(public_server: str) -> None:
    public_id = _create_link()
    db.execute("UPDATE access_links SET status='PROCESSING' WHERE public_id=%s", (public_id,))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": 393, "height": 852})
            requests: list[float] = []
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: requests.append(time.monotonic()) if "/orders/getOrderInfo" in request.url else None,
            )
            page.goto(f"{public_server}/order/{public_id}")
            expect(page.locator("#pickup-loading")).to_be_visible()
            expect(page.locator("#pickup-status")).to_contain_text("每 2 秒")
            with page.expect_response(lambda response: "/orders/getOrderInfo" in response.url, timeout=5000):
                pass
            assert len(requests) >= 2
            assert requests[1] - requests[0] >= 1.8
            assert (
                page.locator(".pickup-spinner").evaluate("node => getComputedStyle(node).animationName")
                == "pickup-spin"
            )

            # 在真实数据库推进订单，下一次真实 HTTP 查询必须取得号码并停止定时器。
            orders.save_order_detail(
                public_id,
                f"pickup-browser-{public_id}",
                {
                    "stage": "PREPARING",
                    "queueNumber": "A0101",
                    "store": {"name": "取餐号回归餐厅", "address": "真实数据库回归记录"},
                    "items": [{"name": "2+1随心配", "quantity": 1}],
                },
            )
            expect(page.locator("#pickup-number")).to_have_text("A0101", timeout=5000)
            expect(page.locator("#pickup-loading")).to_be_hidden()
            expect(page.locator("#pickup-status")).not_to_contain_text("自动更新")
            expect(page.locator("#pickup-store")).to_contain_text("取餐号回归餐厅")
            expect(page.locator("#pickup-item-list")).to_contain_text("2+1随心配")
            count = len(requests)
            page.evaluate(
                "document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('online'))"
            )
            page.wait_for_timeout(4300)
            assert len(requests) == count

            # 用户主动刷新时仍能显示本地订单，但不会继续轮询。
            page.reload()
            expect(page.locator("#pickup-number")).to_have_text("A0101")
            expect(page.locator("#pickup-loading")).to_be_hidden()
            count = len(requests)
            page.wait_for_timeout(2300)
            assert len(requests) == count
            assert not errors
        finally:
            browser.close()
