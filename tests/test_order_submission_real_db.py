import time

import pytest

from app.database import db, order_confirmation_logs, orders
from app.database.coupon import redemption_job_db as jobs
from app.service import checkout_service
from app.service.redemption_worker import RedemptionWorker
from app.service.kfc.redemption_response import CouponUnavailableError, require_available_coupon
from test_redemption_queue_concurrency_real_db import _enqueue, _prepare_queue_case


def test_store_confirmation_public_route_records_click_and_rejects_raw_result():
    import socket
    import threading
    import requests
    import uvicorn
    from app.api.app import create_app

    public_id, _, _ = _prepare_queue_case()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(), lifespan="off", log_config=None))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/api/public/access-links/{public_id}/stores/confirm"
        body = {
            "storeCode": "TEST-STORE",
            "storeAddress": "测试餐厅",
            "storeName": "沙汕车速取餐厅",
            "cityName": "广州",
            "storeImageUrl": "https://pcp-pic.hwwt8.com/SPH/039b451d-fbf8-43e1-aee2-121caf2ab532.jpg",
        }
        response = requests.post(url, json=body, timeout=5)
        assert response.status_code == 200
        assert set(response.json()["data"]) == {"confirmedAt"}
        selection = db.fetchone(
            "SELECT store_image_url,store_code,redemption_payload FROM access_links WHERE public_id=%s", (public_id,)
        )
        assert selection["store_image_url"] == body["storeImageUrl"]
        assert selection["store_code"] == body["storeCode"]
        assert selection["redemption_payload"]["selectedStore"]["cityName"] == "广州"
        assert requests.post(url, json={**body, "storeImageUrl": "javascript:alert(1)"}, timeout=5).status_code == 400
        assert requests.post(url, json={**body, "result": ["客户端伪造原因"]}, timeout=5).status_code == 400
        assert requests.post(url, json={**body, "storeCode": "  "}, timeout=5).status_code == 400
        rows = db.fetchall(
            "SELECT visitor_ip,store_id,store_address,result FROM order_confirmation_logs WHERE public_id=%s",
            (public_id,),
        )
        assert rows == [
            {"visitor_ip": "127.0.0.1", "store_id": "TEST-STORE", "store_address": "测试餐厅", "result": None}
        ]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_checkpoint_older_than_one_minute_fails_and_queue_continues():
    public_id, _, product_id = _prepare_queue_case()
    queued = _enqueue(public_id, product_id)
    next_job = None
    try:
        assert jobs.claim_next()["id"] == queued["id"]
        jobs.mark_browser_paused(queued["id"], checkpoint={"stage": "slider-verified"})
        db.execute(
            "UPDATE access_links SET redemption_started_at=now()-interval '61 seconds' WHERE id=%s", (queued["id"],)
        )
        assert jobs.recover_stale_jobs() == 1
        row = db.fetchone("SELECT status,redemption_status FROM access_links WHERE id=%s", (queued["id"],))
        assert row == {"status": "FAILED", "redemption_status": "FAILED"}
        next_public_id, _, next_product_id = _prepare_queue_case()
        next_job = _enqueue(next_public_id, next_product_id)
        assert jobs.claim_next()["id"] == next_job["id"]
    finally:
        jobs.mark_failed(queued["id"], error_message="pytest timeout inspected")
        if next_job:
            jobs.mark_failed(next_job["id"], error_message="pytest queue inspected")


def test_selected_restaurant_survives_queue_claim_and_reselection():
    from app.errors import AppError

    public_id, _, product_id = _prepare_queue_case()
    image_url = "https://pcp-pic.hwwt8.com/SPH/039b451d-fbf8-43e1-aee2-121caf2ab532.jpg"
    checkout_service.confirm_store(
        public_id, "OLD-STORE", "广州", "127.0.0.1", store_name="旧餐厅", city_name="广州", image_url=image_url
    )
    order_confirmation_logs.save_production_info(
        public_id, "OLD-STORE", [{"id": 1, "group_no": 1, "name": "旧餐品选项"}]
    )
    checkout_service.confirm_store(
        public_id, "TEST-STORE", "广州", "127.0.0.1", store_name="沙汕车速取餐厅", city_name="广州", image_url=image_url
    )
    expected_production_info = [{"id": 2, "group_no": 1, "name": "最新餐品选项"}]
    order_confirmation_logs.save_production_info(public_id, "TEST-STORE", expected_production_info)
    assert order_confirmation_logs.get_latest_production_info(public_id) == expected_production_info
    with pytest.raises(AppError, match="提交餐厅与当前选择不一致"):
        jobs.enqueue(
            public_id,
            store_code="OLD-STORE",
            store_snapshot={"storename": "旧餐厅"},
            pack_type=1,
            items=[],
            visitor_ip="127.0.0.1",
        )
    queued = _enqueue(public_id, product_id)
    try:
        claimed = jobs.claim_next()
        assert claimed["id"] == queued["id"]
        assert claimed["cityName"] == "广州"
        assert claimed["storeName"] == "沙汕车速取餐厅"
        assert claimed["storeImageUrl"] == image_url
        assert claimed["storeCode"] == "TEST-STORE"
        assert claimed["packType"] == 2
        with pytest.raises(AppError, match="该链接当前不可选择餐厅"):
            checkout_service.confirm_store(
                public_id, "OLD-STORE", "广州", "127.0.0.1", store_name="旧餐厅", city_name="广州", image_url=image_url
            )
        assert (
            db.fetchone("SELECT count(*) AS n FROM order_confirmation_logs WHERE public_id=%s", (public_id,))["n"] == 2
        )
    finally:
        jobs.mark_failed(queued["id"], error_message="pytest restaurant selection inspected")


def test_access_link_without_pickup_parameter_defaults_to_takeaway():
    public_id, _, _ = _prepare_queue_case()
    row = db.fetchone("SELECT pickup_type FROM access_links WHERE public_id=%s", (public_id,))
    assert row["pickup_type"] == 1


@pytest.mark.parametrize("count", [-1, 0, 1, 2])
def test_restaurant_confirmation_inventory_and_pickup_persist(count):
    public_id, _, product_id = _prepare_queue_case()
    checkout_service.confirm_store(public_id, "TEST-STORE", "测试餐厅", "127.0.0.1")
    job = _enqueue(public_id, product_id)
    try:
        row = db.fetchone("SELECT pickup_type FROM access_links WHERE id=%s", (job["id"],))
        assert row["pickup_type"] == job["packType"] == 2
        # Persist protocol test inputs in the real database; no mocked upstream
        # client or repository is used by this contract/persistence regression.
        db.execute(
            """UPDATE access_links SET redemption_payload=redemption_payload || jsonb_build_object(
              'couponResponse',jsonb_build_object('code',0,'data',jsonb_build_object(
                'unavailableCouponList',jsonb_build_array(jsonb_build_object(
                  'couponCode',coupon_code,'availableCount',%s::int,'unUseReason',jsonb_build_array('所选餐厅无货'))))))
              WHERE id=%s""",
            (count, job["id"]),
        )
        response = db.fetchone(
            "SELECT redemption_payload->'couponResponse' AS response FROM access_links WHERE id=%s", (job["id"],)
        )["response"]
        if count < 1:
            with pytest.raises(CouponUnavailableError) as caught:
                require_available_coupon(response, job["couponCode"])
            order_confirmation_logs.save_unavailable_result(job["id"], caught.value.reasons)
        else:
            assert require_available_coupon(response, job["couponCode"])["availableCount"] == count
        logs = db.fetchall("SELECT result FROM order_confirmation_logs WHERE public_id=%s", (public_id,))
        assert len(logs) == 1
        assert logs[0]["result"] == (["所选餐厅无货"] if count < 1 else None)
    finally:
        jobs.mark_failed(job["id"], error_message="pytest inventory inspected")


@pytest.mark.parametrize("succeeded", [True, False])
def test_submit_saved_atomically_once_and_not_exposed_to_public(succeeded):
    public_id, _, product_id = _prepare_queue_case()
    job = jobs.enqueue(
        public_id,
        store_code="TEST-STORE",
        store_snapshot={"name": "测试餐厅"},
        pack_type=1,
        items=[{"productId": product_id, "quantity": 1}],
        visitor_ip="127.0.0.1",
    )
    try:
        claimed = jobs.claim_next()
        assert claimed["id"] == job["id"]
        assert claimed["packType"] == 1
        jobs.mark_cart_created(job["id"], "database-contract-cart")
        jobs.mark_coupon_applied(job["id"], cart_id="database-contract-cart")
        assert checkout_service.get_order_info(public_id)["stage"] == "SUBMITTED"
        assert db.fetchone("SELECT status,cart_id FROM access_links WHERE id=%s", (job["id"],)) == {
            "status": "PROCESSING",
            "cart_id": "database-contract-cart",
        }
        db.execute(
            """UPDATE access_links SET redemption_payload=redemption_payload || jsonb_build_object(
              'submitResponse',jsonb_build_object('code',%s::int,'data',jsonb_build_object('orderId','database-contract-order')))
              WHERE id=%s""",
            (0 if succeeded else 1, job["id"]),
        )
        response = db.fetchone(
            "SELECT redemption_payload->'submitResponse' AS response FROM access_links WHERE id=%s", (job["id"],)
        )["response"]
        first = orders.save_browser_submission(job["id"], response, succeeded=succeeded)
        assert orders.save_browser_submission(job["id"], response, succeeded=succeeded)["id"] == first["id"]
        saved = orders.get_order_for_link(public_id)
        assert saved["submit_response"] == response
        assert saved["external_order_id"] == "database-contract-order"
        assert saved["stage"] == ("SUBMITTED" if succeeded else "FAILED")
        assert "submit_response" not in checkout_service._public_order_view(orders.order_view(saved))
        link = db.fetchone("SELECT status,redemption_status FROM access_links WHERE id=%s", (job["id"],))
        assert link == {
            "status": "PICKING_UP" if succeeded else "FAILED",
            "redemption_status": "COMPLETED" if succeeded else "FAILED",
        }
        assert db.fetchone("SELECT count(*) AS n FROM orders WHERE access_link_public_id=%s", (public_id,))["n"] == 1
        if succeeded:
            orders.save_order_detail(
                public_id,
                saved["external_order_id"],
                {
                    "stage": "READY",
                    "store": saved["store_snapshot"],
                    "items": saved["items_snapshot"],
                    "rawStatus": response,
                    "queueNumber": "A001",
                },
            )
            refreshed = orders.get_order_for_link(public_id)
            assert refreshed["submit_response"] == response
            assert refreshed["stage"] == "READY"
            assert refreshed["queue_number"] == "A001"
            assert orders.order_view(refreshed)["stopPolling"] is True
            completed_link = db.fetchone(
                "SELECT status,completed_at FROM access_links WHERE id=%s",
                (job["id"],),
            )
            assert completed_link["status"] == "COMPLETED"
            assert completed_link["completed_at"] is not None
            enriched_items = [
                {
                    "name": "测试套餐",
                    "quantity": 1,
                    "internalItemId": "must-not-leak",
                    "components": [
                        {
                            "name": "经典餐品",
                            "quantity": 1,
                            "internalItemId": "must-not-leak",
                            "components": [{"name": "少冰", "quantity": 1, "components": []}],
                        }
                    ],
                }
            ]
            orders.save_order_detail(
                public_id,
                saved["external_order_id"],
                {
                    "stage": "READY",
                    "store": saved["store_snapshot"],
                    "items": enriched_items,
                    "rawStatus": response,
                    "queueNumber": "A001",
                },
            )
            refreshed = orders.get_order_for_link(public_id)
            assert refreshed["items_snapshot"] == enriched_items
            public_view = checkout_service._public_order_view(orders.order_view(refreshed))
            assert public_view["orderNumber"] == "database-contract-order"
            assert public_view["items"] == [
                {
                    "name": "测试套餐",
                    "quantity": 1,
                    "components": [
                        {
                            "name": "经典餐品",
                            "quantity": 1,
                            "components": [{"name": "少冰", "quantity": 1, "components": []}],
                        }
                    ],
                }
            ]
    finally:
        jobs.mark_failed(job["id"], error_message="pytest submit inspected")


def test_worker_kills_blocked_auth_at_deadline_and_does_not_replay():
    # 本机 kfc 数据库就是项目约定的真实测试环境，不创建临时测试库。
    assert db.engine.url.database == "kfc"
    public_id, _, product_id = _prepare_queue_case()
    job = _enqueue(public_id, product_id)
    try:
        claimed = jobs.claim_next()
        assert claimed["id"] == job["id"]
        row = db.fetchone(
            "UPDATE access_links SET redemption_started_at=now()-interval '56 seconds' WHERE id=%s RETURNING redemption_started_at",
            (job["id"],),
        )
        claimed["workerStartedAt"] = row["redemption_started_at"].isoformat()
        worker = RedemptionWorker()
        with db.engine.begin() as blocker:
            blocker.exec_driver_sql("LOCK TABLE platform_accounts IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            worker._process(claimed)
            assert 3 <= time.monotonic() - started < 8
        row = db.fetchone(
            "SELECT status,redemption_status,redemption_error FROM access_links WHERE id=%s", (job["id"],)
        )
        assert row["status"] == row["redemption_status"] == "FAILED"
        assert "60秒" in row["redemption_error"]
        assert jobs.claim_next() is None
    finally:
        jobs.mark_failed(job["id"], error_message="pytest hard deadline inspected")
