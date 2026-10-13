from __future__ import annotations

import logging
from typing import Any

from app.errors import bad_gateway
from app.service.kfc.contracts import ORDER_SUBMIT_API
from app.service.kfc.h5_client import KfcH5Client

logger = logging.getLogger(__name__)

ORDER_SUBMIT_CLIENT_VERSION = "v6.305(8b4fd15d)"
ORDER_SUBMIT_FVERSION = "251029"
ORDER_SUBMIT_ENCODE_LIST = (
    "phone",
    "address.address",
    "address.name",
    "address.phone",
    "address.mainAddress",
    "address.enterpriseSubAddress",
    "address.supplementalAddress",
    "linkManName",
    "subAddress",
    "unrealGroupMainAddress",
    "unrealGroupAddress",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class KfcSubmitOrderClient:
    def __init__(self, h5_client: KfcH5Client | None = None) -> None:
        self._h5 = h5_client or KfcH5Client()
        self._owns_h5_client = h5_client is None

    def close(self) -> None:
        if self._owns_h5_client:
            self._h5.close()

    @staticmethod
    def build_payload(
        *,
        device_id: str,
        session_id: str,
        ticket: str,
        phone: str,
        order_id: str,
        pack_type: int = 2,
        remark: str = "",
        open_id: str = "",
    ) -> dict[str, Any]:
        rcsbc_id = f"bid_{device_id}"
        return {
            "portalType": "WAP",
            "channelName": "Mobile Web",
            "channelId": "13",
            "brand": "KFC_PRE",
            "business": "preorder",
            "sessionId": session_id,
            "deviceId": device_id,
            "clientVersion": ORDER_SUBMIT_CLIENT_VERSION,
            "ticket": ticket,
            "fversion": ORDER_SUBMIT_FVERSION,
            "versionNum": "5",
            "businessLine": "preorder",
            "phone": phone,
            "packType": pack_type,
            "iremark": remark,
            "orderId": order_id,
            "queryThirdParam": "",
            "accessoryRemark": "[]",
            "env": "qcpil",
            "rcsbcId": rcsbc_id,
            "rcsdcId": device_id,
            "riskCtrlParam": {
                "channelName": "Mobile Web",
                "openId": open_id,
                "browserId": rcsbc_id,
                "deviceId": device_id,
                "td_id": "",
            },
            "encodeList": list(ORDER_SUBMIT_ENCODE_LIST),
            "isFromCustomerClient": True,
            "secretKey": "kfc",
        }

    def submit_order(
        self,
        *,
        device_id: str,
        session_id: str,
        ticket: str,
        phone: str,
        order_id: str,
        pack_type: int = 2,
        remark: str = "",
        open_id: str = "",
        cookie: str = "",
        user_code: str = "",
    ) -> dict[str, Any]:
        device_id = device_id.strip()
        session_id = session_id.strip()
        ticket = ticket.strip()
        phone = phone.strip()
        order_id = order_id.strip()
        if not device_id:
            raise ValueError("device_id 不能为空")
        if not session_id:
            raise ValueError("session_id 不能为空")
        if not ticket:
            raise ValueError("ticket 不能为空")
        if not phone:
            raise ValueError("phone 不能为空")
        if not order_id:
            raise ValueError("order_id 不能为空")
        if pack_type not in (1, 2):
            raise bad_gateway("取餐方式无效")

        context = {
            "config": self._h5.load_config(),
            "device_id": device_id,
            "cookie": cookie.strip(),
        }
        response = self._h5.http.post(
            context,
            ORDER_SUBMIT_API,
            self.build_payload(
                device_id=device_id,
                session_id=session_id,
                ticket=ticket,
                phone=phone,
                order_id=order_id,
                pack_type=pack_type,
                remark=remark,
                open_id=open_id,
            ),
            base_url=self._h5.base_url,
            user_agent=self._h5.user_agent,
            sign=True,
            user_code=user_code.strip(),
        )
        data = _obj(response.get("data"))
        cart = _obj(data.get("cart"))
        store = _obj(cart.get("store"))
        cashier_data = _obj(data.get("cashierData"))
        items = cart.get("items")
        coupon_code = ""
        for item in items if isinstance(items, list) else []:
            coupon_code = _text(_obj(item).get("couponCode"))
            if coupon_code:
                break

        result = {
            "code": response.get("code"),
            "msg": _text(response.get("msg") or response.get("message")),
            "city": _text(store.get("cityName") or store.get("marketName")),
            "order_id": _text(cashier_data.get("orderId") or data.get("orderId") or cart.get("id") or order_id),
            "store_name": _text(store.get("storename") or store.get("storeName")),
            "store_code": _text(store.get("storecode") or store.get("storeCode")),
            "coupon_code": coupon_code,
            "raw_response": response,
        }
        logger.info(
            "KFC submitOrder result city=%s order_id=%s store_name=%s store_code=%s coupon_code=%s",
            result["city"],
            result["order_id"],
            result["store_name"],
            result["store_code"],
            result["coupon_code"],
        )
        return result


kfc_submit_order = KfcSubmitOrderClient()
