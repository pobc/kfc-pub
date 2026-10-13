from __future__ import annotations

import json
import os

import pytest

from app.database import kfc_tickets
from app.service.kfc.query_order_detail import KfcQueryOrderDetailClient, kfc_query_order_detail


def test_query_order_detail_payload() -> None:
    payload = KfcQueryOrderDetailClient.build_payload(
        token="test-token",
        order_id="1787636826613127468",
    )

    assert payload == {
        "brand": "KFC_PRE",
        "sys_channel": "preorder",
        "appChannel": "H5",
        "encrypted": True,
        "token": "test-token",
        "userId": "",
        "orderId": "1787636826613127468",
        "isFinalStatus": True,
        "encodeList": [],
        "isFromCustomerClient": True,
        "secretKey": "kfc",
    }


def test_query_order_info_extracts_captured_order_detail() -> None:
    response = {
        "data": {
            "posOrderNumber": "A0631",
            "orderNumber": "A0631",
            "status": "212",
            "statusShortName": "准备中",
            "statusName": "餐厅准备中",
            "storeCode": "GZ1091",
            "storeName": "环球都会广场餐厅",
            "storeAddress": "天河区花城大道 68 号环球都会广场第 1 层 05、06 单元",
            "storePhone": "020-62909407",
            "city": "广州",
            "cityCode": "00012",
            "items": [
                {"nameCN": "YUM 优惠", "promotionType": 8, "quantity": 1, "price": 310},
                {
                    "nameCN": "经典草莓圣代",
                    "promotionType": 1,
                    "quantity": 1,
                    "price": 1200,
                    "realPrice": 0,
                    "imageName": "/1/01012/images/100071435__ALL__S__20240328150104031.jpg",
                    "itemDesc": '["草莓酱"]',
                    "mealItems": [
                        {
                            "nameCN": "原味圣代",
                            "quantity": 1,
                            "mealItems": [
                                {"nameCN": "加草莓酱", "quantity": 1},
                            ],
                        }
                    ],
                },
                {"nameCN": "*平台补贴", "promotionType": 7, "quantity": 1, "price": 300},
                {"nameCN": "KFC 百胜会支付宝", "promotionType": 6, "quantity": 1, "price": 890},
                {"nameCN": "应付金额", "promotionType": 6, "quantity": 1, "price": 0},
            ],
        },
        "errCode": 0,
        "errorCode": "0",
    }

    assert KfcQueryOrderDetailClient.extract_pickup_number(response) == "A0631"
    assert KfcQueryOrderDetailClient.extract_status_name(response) == "餐厅准备中"
    assert KfcQueryOrderDetailClient.extract_stage("212", "餐厅准备中") == "PREPARING"
    assert KfcQueryOrderDetailClient.extract_store(response) == {
        "storeCode": "GZ1091",
        "name": "环球都会广场餐厅",
        "address": "天河区花城大道 68 号环球都会广场第 1 层 05、06 单元",
        "phone": "020-62909407",
        "city": "广州",
        "cityCode": "00012",
    }
    assert KfcQueryOrderDetailClient.extract_items(response) == [
        {
            "name": "经典草莓圣代",
            "quantity": 1,
            "description": '["草莓酱"]',
            "imageUrl": "/1/01012/images/100071435__ALL__S__20240328150104031.jpg",
            "price": 1200,
            "realPrice": 0,
            "components": [
                {
                    "name": "原味圣代",
                    "quantity": 1,
                    "components": [{"name": "加草莓酱", "quantity": 1, "components": []}],
                }
            ],
        }
    ]


def test_query_order_info_completed_status() -> None:
    assert KfcQueryOrderDetailClient.extract_stage("215", "已完成") == "COMPLETED"


def test_query_order_detail_real_request() -> None:
    if os.getenv("RUN_KFC_LIVE_TESTS", "").strip() != "1":
        pytest.skip("设置 RUN_KFC_LIVE_TESTS=1 后执行真实 KFC 接口测试")

    phone = os.getenv("KFC_TEST_PHONE", "").strip()
    if not phone:
        pytest.skip("设置 KFC_TEST_PHONE 后执行指定手机号的真实 KFC 接口测试")
    auth = kfc_tickets.get_latest_enabled_auth_for_phone(phone)
    if not auth:
        pytest.skip(f"手机号 {phone} 没有可用的 KFC 登录信息")

    order_id = os.getenv("KFC_TEST_ORDER_ID", "1787636826613127468").strip()
    result = kfc_query_order_detail.query_order_detail(
        phone=phone,
        order_id=order_id,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert isinstance(result, dict)
