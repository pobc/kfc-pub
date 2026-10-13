from __future__ import annotations

import logging
from typing import Any

from app.service.kfc.contracts import CART_UPDATE_API
from app.service.kfc.h5_client import KfcH5Client
from app.service.kfc.http_client import business_result, response_ok

logger = logging.getLogger(__name__)

CART_UPDATE_CLIENT_VERSION = "v6.305(8b4fd15d)"
CART_UPDATE_FVERSION = "251029"

_COMMON_FIELDS = frozenset(
    {
        "portalType",
        "channelName",
        "channelId",
        "brand",
        "business",
        "sessionId",
        "deviceId",
        "clientVersion",
        "ticket",
        "fversion",
        "versionNum",
        "env",
        "rcsbcId",
        "rcsdcId",
        "riskCtrlParam",
        "encodeList",
        "isFromCustomerClient",
        "secretKey",
    }
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class KfcCartUpdateClient:
    """封装 KFC ``/cart/update``。

    该接口在当前业务中用于取消已使用的券。取消动作本身的字段通过
    ``cancel_data`` 原样传入；会话、设备、风控和公共字段由这里统一补齐，
    避免调用方重复拼装 KFC 公共请求结构。
    """

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
        cancel_data: dict[str, Any],
        open_id: str = "",
    ) -> dict[str, Any]:
        device_id = _text(device_id)
        session_id = _text(session_id)
        ticket = _text(ticket)
        if not device_id:
            raise ValueError("device_id 不能为空")
        if not session_id:
            raise ValueError("session_id 不能为空")
        if not ticket:
            raise ValueError("ticket 不能为空")
        if not isinstance(cancel_data, dict) or not cancel_data:
            raise ValueError("cancel_data 不能为空")

        conflicts = sorted(_COMMON_FIELDS.intersection(cancel_data))
        if conflicts:
            raise ValueError(f"cancel_data 不能覆盖公共字段: {', '.join(conflicts)}")

        rcsbc_id = f"bid_{device_id}"
        return {
            "portalType": "WAP",
            "channelName": "Mobile Web",
            "channelId": "13",
            "brand": "KFC_PRE",
            "business": "preorder",
            "sessionId": session_id,
            "deviceId": device_id,
            "clientVersion": CART_UPDATE_CLIENT_VERSION,
            "ticket": ticket,
            "fversion": CART_UPDATE_FVERSION,
            "versionNum": "5",
            **cancel_data,
            "env": "qcpil",
            "rcsbcId": rcsbc_id,
            "rcsdcId": device_id,
            "riskCtrlParam": {
                "channelName": "Mobile Web",
                "openId": _text(open_id),
                "browserId": rcsbc_id,
                "deviceId": device_id,
                "td_id": "",
            },
            "encodeList": [],
            "isFromCustomerClient": True,
            "secretKey": "kfc",
        }

    def cancel_coupon(
        self,
        *,
        device_id: str,
        session_id: str,
        ticket: str,
        cancel_data: dict[str, Any],
        open_id: str = "",
        cookie: str = "",
        user_code: str = "",
    ) -> dict[str, Any]:
        payload = self.build_payload(
            device_id=device_id,
            session_id=session_id,
            ticket=ticket,
            cancel_data=cancel_data,
            open_id=open_id,
        )
        context = {
            "config": self._h5.load_config(),
            "device_id": _text(device_id),
            "cookie": _text(cookie),
        }
        logger.info(
            "KFC cart/update cancel coupon request cancel_fields=%s",
            sorted(cancel_data),
        )
        response = self._h5.http.post(
            context,
            CART_UPDATE_API,
            payload,
            base_url=self._h5.base_url,
            user_agent=self._h5.user_agent,
            sign=True,
            user_code=_text(user_code),
            accept_business_error=True,
        )
        if not response_ok(response):
            return business_result(response, fallback_msg="肯德基取消用券失败")
        return response


kfc_cart_update = KfcCartUpdateClient()
