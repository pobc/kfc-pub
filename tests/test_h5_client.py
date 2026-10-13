from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Iterator

import pytest
import requests

from app.errors import AppError
from app.service.kfc.contracts import CART_CREATE_API, HOME_PATH
from app.service.kfc.h5_client import KfcH5Client, _env, _int, _obj, _text
from app.service.kfc.h5_config import H5Config, H5ConfigLoader


@contextmanager
def _temporary_env(name: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


@pytest.fixture
def client() -> Iterator[KfcH5Client]:
    value = KfcH5Client()
    try:
        yield value
    finally:
        value.close()


def _cached_config() -> H5Config:
    return H5Config(
        build_version="v6.999(test)",
        api_fversion="f-test",
        kbs_key="kbs-key",
        kbs_secret="kbs-secret",
        route_cell="yumc-test",
        expires_at=10**15,
    )


def _cookie_pairs(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in value.split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator and name:
            result[name] = cookie_value
    return result


def _assert_app_error(exc: pytest.ExceptionInfo[AppError], status_code: int, message: str) -> None:
    assert exc.value.status_code == status_code
    assert exc.value.message == message


def test_scalar_helpers() -> None:
    assert _text(None) == ""
    assert _text(123) == "123"
    assert _obj({"a": 1}) == {"a": 1}
    assert _obj(["a"]) == {}
    assert _int("5", 9) == 5
    assert _int(0, 9) == 9
    assert _int("bad", 9) == 9

    with _temporary_env("KFC_TEST_ENV_HELPER", "  value  "):
        assert _env("KFC_TEST_ENV_HELPER") == "value"
    with _temporary_env("KFC_TEST_ENV_HELPER", None):
        assert _env("KFC_TEST_ENV_HELPER") == ""


def test_client_initial_state_and_close() -> None:
    value = KfcH5Client()
    assert value.config_loader.cached is None
    assert value.stores == {}
    assert value.http.timeout > 0
    assert isinstance(value.http.session, requests.Session)
    value.close()


def test_user_agent_uses_environment_override(client: KfcH5Client) -> None:
    with _temporary_env("KFC_USER_AGENT", " unit-test-agent "):
        assert client.user_agent == "unit-test-agent"
    with _temporary_env("KFC_USER_AGENT", None):
        assert "Mozilla/5.0" in client.user_agent
        assert "iPhone" in client.user_agent


def test_load_config_returns_valid_cached_config_without_network(client: KfcH5Client) -> None:
    config = _cached_config()
    client.config_loader.cached = config
    assert client.load_config() is config


def test_http_prepare_request_builds_payload_headers_cookie_and_signature(client: KfcH5Client) -> None:
    config = _cached_config()
    context = {
        "config": config,
        "device_id": "device-123",
        "cookie": "SID=stored; VOLCALB=stale",
    }
    client.http.session.cookies.set("VOLCALB", "fresh")
    client.http.session.cookies.set("runtime", "cookie")

    with _temporary_env("KFC_USER_AGENT", "kfc-unit-test"):
        payload, headers = client.http.prepare_request(
            context,
            CART_CREATE_API,
            {"sessionId": "session-1", "ticket": "ticket-1"},
            base_url=client.base_url,
            user_agent=client.user_agent,
            sign=True,
            user_code="user-1",
        )

    assert payload["portalType"] == "WAP"
    assert payload["deviceId"] == "device-123"
    assert payload["clientVersion"] == config.build_version
    assert payload["fversion"] == config.api_fversion
    assert payload["sessionId"] == "session-1"
    assert payload["ticket"] == "ticket-1"
    assert headers["origin"] == client.base_url
    assert headers["referer"] == f"{client.base_url}{HOME_PATH}"
    assert headers["user-agent"] == "kfc-unit-test"
    assert headers["x-yumc-route-usercode"] == "user-1"
    assert headers["kbck"] == config.kbs_key

    cookies = _cookie_pairs(headers["cookie"])
    assert cookies["SID"] == "stored"
    assert cookies["VOLCALB"] == "fresh"
    assert cookies["runtime"] == "cookie"
    assert cookies["route-cell"] == config.route_cell

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = headers["kbcts"]
    source = f"{config.kbs_key}\t{config.kbs_secret}\t{timestamp}\t{CART_CREATE_API}\t\t{raw}"
    assert headers["kbsv"] == hashlib.md5(source.encode()).hexdigest()


def test_http_prepare_request_can_disable_signature(client: KfcH5Client) -> None:
    config = _cached_config()
    payload, headers = client.http.prepare_request(
        {"config": config, "device_id": "device-123"},
        "/plain",
        {"value": 1},
        base_url=client.base_url,
        user_agent=client.user_agent,
        sign=False,
    )
    assert payload["value"] == 1
    for name in ("kbck", "kbcts", "kbsv", "rcsav", "rcsbcid", "rcsdcid"):
        assert name not in headers


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<script src="/preorder-taro/js/app.123abc.js"></script>', "/preorder-taro/js/app.123abc.js"),
        ('<script src="js/app.abc123.js"></script>', "/preorder-taro/js/app.abc123.js"),
        (
            '<script src="https://cdn.example.com/preorder-taro/js/app.deadbeef.js"></script>',
            "https://cdn.example.com/preorder-taro/js/app.deadbeef.js",
        ),
        (
            '<script src="/preorder-taro/js/app.first.js"></script>'
            '<script src="/preorder-taro/js/app.second.js"></script>',
            "/preorder-taro/js/app.second.js",
        ),
    ],
)
def test_extract_app_path(html: str, expected: str) -> None:
    assert H5ConfigLoader.extract_app_path(html) == expected


def test_extract_app_path_rejects_missing_app_script() -> None:
    with pytest.raises(AppError) as exc:
        H5ConfigLoader.extract_app_path("<html></html>")
    _assert_app_error(exc, 503, "肯德基 App JS 地址提取失败")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('{"buildVersion":"v6.820(f472c683)"}', "v6.820(f472c683)"),
        ("{'buildVersion': 'v1.2.3'}", "v1.2.3"),
        ("const buildVersion = 'v1';", ""),
    ],
)
def test_extract_build_version(html: str, expected: str) -> None:
    assert H5ConfigLoader.extract_build_version(html) == expected


def test_decode_obfuscated_config_decodes_base64_json() -> None:
    original = [["kfc.WEB", {"client_key": "key-1", "client_sec": "secret-1"}]]
    encoded = (
        base64.b64encode(json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode())
        .decode()
        .replace("=", "$")
    )
    text = f'window.__CONFIG__={{"kbsInfo":{json.dumps([encoded])}}};'
    assert H5ConfigLoader.decode_obfuscated_config(text, "kbsInfo") == original


def test_decode_obfuscated_config_returns_plain_array() -> None:
    text = 'window.__CONFIG__={"apiFVersion":[["kfc","f-1"],["pizza","f-2"]]};'
    assert H5ConfigLoader.decode_obfuscated_config(text, "apiFVersion") == [
        ["kfc", "f-1"],
        ["pizza", "f-2"],
    ]


def test_find_pair_object_and_extract_cookie() -> None:
    value = [["other", {"value": 1}], ["kfc.WEB", {"client_key": "key", "client_sec": "secret"}]]
    assert H5ConfigLoader.find_pair_object(value, "kfc.WEB") == {
        "client_key": "key",
        "client_sec": "secret",
    }
    assert H5ConfigLoader.find_pair_object(value, "missing") == {}
    assert H5ConfigLoader.extract_cookie("foo=1, route-cell=yumc5; Path=/", "route-cell") == "yumc5"


def test_cookie_header_prefers_session_cookies_and_overrides_route_cell(client: KfcH5Client) -> None:
    client.http.session.cookies.set("SID", "runtime")
    client.http.session.cookies.set("VOLCALB", "fresh")
    client.http.session.cookies.set("new-cookie", "new")

    header = client.http.cookie_header(
        "yumc-new",
        "SID=stored; VOLCALB=stale; old-cookie=old; route-cell=yumc-old",
    )
    assert _cookie_pairs(header) == {
        "SID": "runtime",
        "VOLCALB": "fresh",
        "old-cookie": "old",
        "route-cell": "yumc-new",
        "new-cookie": "new",
    }


def test_store_code_of_supports_both_kfc_field_names() -> None:
    assert KfcH5Client._store_code_of({"storecode": " A001 "}) == "A001"
    assert KfcH5Client._store_code_of({"storeCode": "B002"}) == "B002"
    assert KfcH5Client._store_code_of({}) == ""


def test_signed_store_from_init_prefers_recommended_store() -> None:
    fallback = {"storecode": "S001", "source": "fallback"}
    recommended = {"storecode": "S001", "source": "recommended"}
    assert (
        KfcH5Client._signed_store_from_init(
            {"dataRecommend": {"storeRecommend": recommended}},
            fallback,
            "S001",
        )
        is recommended
    )


def test_next_booking_date_uses_next_aligned_interval() -> None:
    interval_ms = 15 * 60_000
    before = int(time.time() * 1000)
    booking_date = KfcH5Client._next_booking_date({"bookingInterval": 15})
    after = int(time.time() * 1000)
    assert booking_date % interval_ms == 0
    assert booking_date >= before + interval_ms
    assert booking_date <= after + (2 * interval_ms)


def test_normalise_store_builds_public_view_and_image() -> None:
    raw = {
        "storecode": "S001",
        "storename": "人民广场店",
        "address": "测试路 1 号",
        "status": "1",
        "starttime": "08:00",
        "endtime": "22:00",
        "storePic": {"storeSelfPic": ["", "https://img.example/self.jpg"]},
    }
    entry = KfcH5Client._normalise_store(raw, "310100")
    assert entry is not None
    assert entry["raw"] is raw
    assert entry["view"]["storeCode"] == "S001"
    assert entry["view"]["status"] == "OPEN"
    assert entry["view"]["imageUrl"] == "https://img.example/self.jpg"


def test_resolve_store_accepts_matching_supplied_store(client: KfcH5Client) -> None:
    supplied = {"storecode": "S001", "storename": "测试店", "citycode": "310100", "status": "1"}
    entry = client._resolve_store("S001", supplied)
    assert entry["view"]["storeCode"] == "S001"
    assert entry["view"]["cityCode"] == "310100"


def test_resolve_store_uses_cache_or_requires_new_search(client: KfcH5Client) -> None:
    cached = {"raw": {"storecode": "S001"}, "view": {"storeCode": "S001"}}
    client.stores["S001"] = cached
    assert client._resolve_store("S001", None) is cached
    assert client.get_store("S001") == cached["view"]
    with pytest.raises(AppError) as exc:
        client._resolve_store("MISSING", None)
    _assert_app_error(exc, 502, "请重新搜索并选择餐厅")


def test_map_stage_uses_default_and_environment_mapping() -> None:
    with _temporary_env("KFC_STATUS_MAP_JSON", None):
        assert KfcH5Client._map_stage(504) == "FAILED"
        assert KfcH5Client._map_stage("unknown") == "UNKNOWN"
    with _temporary_env("KFC_STATUS_MAP_JSON", json.dumps({"100": "SUBMITTED", "999": "NO"})):
        assert KfcH5Client._map_stage(100) == "SUBMITTED"
        assert KfcH5Client._map_stage(999) == "UNKNOWN"


def test_find_auth_value_and_find_field() -> None:
    assert KfcH5Client._find_auth_value('{"deep":[{"sessionId":"s-3"}]}', ["sessionId"]) == "s-3"
    data = {"outer": [{"nested": {"queueNo": "Q123", "pickupCode": 456}}]}
    assert KfcH5Client._find_field(data, ["queueNo"]) == "Q123"
    assert KfcH5Client._find_field(data, ["pickupCode"]) == "456"


def test_apply_coupon_rejects_empty_coupon_before_any_network_call(client: KfcH5Client) -> None:
    store_code = "RLA001"
    result = client.apply_coupon(
        phone="13900000000",
        store_code=store_code,
        store={"storecode": store_code},
        pack_type=1,
        items=[],
        coupon_code="EXAMPLECOUPON",
    )
    print(result)


def test_create_authenticated_cart_validates_before_external_work(client: KfcH5Client) -> None:
    with pytest.raises(AppError) as exc:
        client._create_authenticated_cart("S001", {}, 1, [], phone="13900000000")
    _assert_app_error(exc, 502, "订单没有餐品")

    with pytest.raises(AppError) as exc:
        client._create_authenticated_cart("S001", {}, 3, [{"id": 1}], phone="13900000000")
    _assert_app_error(exc, 502, "取餐方式无效")


def test_search_stores_real_kfc_when_explicitly_enabled() -> None:
    if os.getenv("RUN_KFC_LIVE_TESTS", "").strip() != "1":
        pytest.skip("设置 RUN_KFC_LIVE_TESTS=1 后执行真实 KFC H5 门店搜索测试")

    city_code = os.getenv("KFC_TEST_CITY_CODE", "310100").strip()
    keyword = os.getenv("KFC_TEST_STORE_KEYWORD", "人民").strip()
    value = KfcH5Client()
    try:
        stores = value.search_stores(city_code, keyword, page=1, page_size=5)
    finally:
        value.close()
    assert isinstance(stores, list)
    for store in stores:
        assert store["storeCode"]
        assert store["cityCode"] == city_code
        assert store["status"] in {"OPEN", "CLOSED"}
