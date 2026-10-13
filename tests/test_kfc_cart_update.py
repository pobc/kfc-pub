from __future__ import annotations

import pytest

from app.service.kfc.cart_update import KfcCartUpdateClient
from app.service.kfc.contracts import CART_UPDATE_API


def test_build_cancel_coupon_payload_adds_kfc_common_fields() -> None:
    payload = KfcCartUpdateClient.build_payload(
        device_id="device-1",
        session_id="session-1",
        ticket="ticket-1",
        open_id="open-1",
        cancel_data={
            "cartId": "cart-1",
            "couponCode": "ABC123",
        },
    )

    assert payload["portalType"] == "WAP"
    assert payload["channelName"] == "Mobile Web"
    assert payload["channelId"] == "13"
    assert payload["brand"] == "KFC_PRE"
    assert payload["business"] == "preorder"
    assert payload["sessionId"] == "session-1"
    assert payload["deviceId"] == "device-1"
    assert payload["ticket"] == "ticket-1"
    assert payload["cartId"] == "cart-1"
    assert payload["couponCode"] == "ABC123"
    assert payload["rcsbcId"] == "bid_device-1"
    assert payload["rcsdcId"] == "device-1"
    assert payload["riskCtrlParam"] == {
        "channelName": "Mobile Web",
        "openId": "open-1",
        "browserId": "bid_device-1",
        "deviceId": "device-1",
        "td_id": "",
    }
    assert payload["encodeList"] == []
    assert payload["isFromCustomerClient"] is True
    assert payload["secretKey"] == "kfc"


def test_build_cancel_coupon_payload_rejects_common_field_override() -> None:
    with pytest.raises(ValueError, match="不能覆盖公共字段"):
        KfcCartUpdateClient.build_payload(
            device_id="device-1",
            session_id="session-1",
            ticket="ticket-1",
            cancel_data={"sessionId": "other-session"},
        )


def test_cart_update_api_path() -> None:
    assert CART_UPDATE_API == "/preorder-portal/api/v2/cart/update"
