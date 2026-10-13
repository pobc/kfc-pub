from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.database import kfc_tickets
from app.errors import bad_gateway, unavailable
from app.service.kfc.h5_client import KfcH5Client

logger = logging.getLogger(__name__)

QUERY_ORDER_DETAIL_API = "/cashier/preorder/queryOrderDetail"


@dataclass(frozen=True)
class KfcOrderInfo:
    order_id: str
    pickup_number: str
    status_code: str
    status_name: str
    stage: str
    store: dict[str, Any]
    items: list[dict[str, Any]]
    raw_status: dict[str, Any]


class KfcQueryOrderDetailClient:
    def __init__(self, client: KfcH5Client | None = None) -> None:
        self._client = client or KfcH5Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def build_payload(
        *,
        token: str,
        order_id: str,
        user_id: str = "",
        is_final_status: bool = True,
    ) -> dict[str, Any]:
        return {
            "brand": "KFC_PRE",
            "sys_channel": "preorder",
            "appChannel": "H5",
            "encrypted": True,
            "token": token,
            "userId": user_id,
            "orderId": order_id,
            "isFinalStatus": is_final_status,
            "encodeList": [],
            "isFromCustomerClient": True,
            "secretKey": "kfc",
        }

    def get_order_info(self, *, phone: str, order_id: str) -> KfcOrderInfo:
        response = self.query_order_detail(phone=phone, order_id=order_id)
        data = response.get("data")
        if not isinstance(data, dict):
            raise bad_gateway("KFC 订单详情未返回订单数据")

        status_name = self.extract_status_name(response)
        status_code = str(data.get("status") or "").strip()
        return KfcOrderInfo(
            order_id=order_id,
            pickup_number=self.extract_pickup_number(response),
            status_code=status_code,
            status_name=status_name,
            stage=self.extract_stage(status_code, status_name),
            store=self.extract_store(response),
            items=self.extract_items(response),
            raw_status=self.extract_raw_status(response),
        )

    def query_order_detail(
        self,
        *,
        phone: str,
        order_id: str,
        is_final_status: bool = True,
    ) -> dict[str, Any]:
        phone = phone.strip()
        order_id = order_id.strip()
        if not phone:
            raise ValueError("phone 不能为空")
        if not order_id:
            raise ValueError("order_id 不能为空")

        auth = self._load_auth(phone)
        ticket = str(auth["ticket"])
        route_user_code = str(auth["user_code"])
        kbck = str(auth["kbck"])
        config = self._client.load_config()
        context = {
            "config": config,
            "device_id": str(auth["device_id"]),
            "cookie": str(auth.get("cookie") or ""),
        }
        body = self.build_payload(
            token=ticket,
            order_id=order_id,
            user_id=route_user_code,
            is_final_status=is_final_status,
        )
        payload, headers = self._client.http.prepare_request(
            context,
            QUERY_ORDER_DETAIL_API,
            body,
            base_url=self._client.base_url,
            user_agent=self._client.user_agent,
            sign=False,
            user_code=route_user_code,
        )
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        source = f"{kbck}\t{config.kbs_secret}\t{timestamp}\t{QUERY_ORDER_DETAIL_API}\t\t{raw}"
        headers.update(
            {
                "kbck": kbck,
                "kbcts": timestamp,
                "kbsv": hashlib.md5(source.encode()).hexdigest(),
                "rcsav": "",
                "rcsbcid": f"bid_{context['device_id']}",
                "rcsdcid": context["device_id"],
            }
        )
        url = f"{self._client.base_url}{QUERY_ORDER_DETAIL_API}"

        logger.info("KFC queryOrderDetail request phone=%s order_id=%s", phone, order_id)
        started = time.perf_counter()
        try:
            http_response = self._client.http.session.post(
                url,
                headers=headers,
                data=raw.encode("utf-8"),
                timeout=self._client.http.timeout,
            )
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception(
                "KFC queryOrderDetail exception phone=%s order_id=%s duration=%.1fms",
                phone,
                order_id,
                duration,
            )
            raise bad_gateway("KFC 订单详情接口暂时不可用") from exc

        duration = (time.perf_counter() - started) * 1000
        logger.info(
            "KFC queryOrderDetail response phone=%s order_id=%s status=%s duration=%.1fms",
            phone,
            order_id,
            http_response.status_code,
            duration,
        )
        if http_response.status_code >= 400:
            raise bad_gateway(f"KFC 订单详情接口 HTTP {http_response.status_code}")
        try:
            response = http_response.json()
        except Exception as exc:
            raise bad_gateway("KFC 订单详情接口未返回合法 JSON") from exc
        if not isinstance(response, dict):
            raise bad_gateway("KFC 订单详情接口返回格式错误")

        self._ensure_success(response)
        return response

    @staticmethod
    def extract_pickup_number(response: dict[str, Any]) -> str:
        data = response.get("data")
        if not isinstance(data, dict):
            return ""
        for field in ("posOrderNumber", "orderNumber"):
            value = str(data.get(field) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def extract_status_name(response: dict[str, Any]) -> str:
        data = response.get("data")
        if not isinstance(data, dict):
            return ""
        return str(data.get("statusName") or "").strip()

    @staticmethod
    def extract_stage(status_code: str, status_name: str) -> str:
        if status_code == "215" or status_name == "已完成":
            return "COMPLETED"
        if "失败" in status_name or "取消" in status_name:
            return "FAILED"
        if "取餐" in status_name and "准备" not in status_name:
            return "READY"
        if status_code == "212" or "准备" in status_name:
            return "PREPARING"
        if status_code or status_name:
            return "SUBMITTED"
        return "UNKNOWN"

    @staticmethod
    def extract_store(response: dict[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        if not isinstance(data, dict):
            return {}
        return {
            "storeCode": str(data.get("storeCode") or "").strip(),
            "name": str(data.get("storeName") or data.get("mainStoreName") or "").strip(),
            "address": str(data.get("storeAddress") or "").strip(),
            "phone": str(data.get("storePhone") or "").strip(),
            "city": str(data.get("city") or "").strip(),
            "cityCode": str(data.get("cityCode") or "").strip(),
        }

    @classmethod
    def extract_items(cls, response: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return []
        result: list[dict[str, Any]] = []
        for item in data["items"]:
            if not isinstance(item, dict) or not cls._is_display_item(item):
                continue
            name = str(item.get("nameCN") or item.get("nameEN") or "").strip()
            if not name:
                continue
            try:
                quantity = max(1, int(item.get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            result.append(
                {
                    "name": name,
                    "quantity": quantity,
                    "description": str(item.get("itemDesc") or "").strip(),
                    "imageUrl": str(item.get("imageName") or "").strip(),
                    "price": item.get("price") or 0,
                    "realPrice": item.get("realPrice") or 0,
                    "components": cls._extract_components(item),
                }
            )
        return result

    @classmethod
    def _extract_components(cls, item: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
        """Convert KFC's nested meal data into a bounded Chinese customer-facing tree."""
        if depth >= 3:
            return []
        result: list[dict[str, Any]] = []
        for field in ("mealItems", "modifyItems", "toppingItems"):
            children = item.get(field)
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                name = str(child.get("nameCN") or "").strip()
                if not name:
                    continue
                try:
                    quantity = max(1, int(child.get("quantity") or 1))
                except (TypeError, ValueError):
                    quantity = 1
                result.append(
                    {
                        "name": name,
                        "quantity": quantity,
                        "components": cls._extract_components(child, depth + 1),
                    }
                )
        return result

    @staticmethod
    def _is_display_item(item: dict[str, Any]) -> bool:
        name = str(item.get("nameCN") or "").strip()
        if not name or name in {"YUM 优惠", "KFC 百胜会支付宝", "应付金额"}:
            return False
        try:
            promotion_type = int(item.get("promotionType"))
        except (TypeError, ValueError):
            promotion_type = None
        return promotion_type not in {6, 7, 8}

    @staticmethod
    def extract_raw_status(response: dict[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        if not isinstance(data, dict):
            return {}
        return {
            "status": data.get("status"),
            "statusShortName": data.get("statusShortName"),
            "statusName": data.get("statusName"),
            "statusList": data.get("statusList") if isinstance(data.get("statusList"), list) else [],
            "updateTime": data.get("updateTime"),
        }

    @staticmethod
    def _load_auth(phone: str) -> dict[str, Any]:
        row = kfc_tickets.get_latest_enabled_auth_for_phone(phone)
        if not row:
            raise unavailable(f"手机号 {phone} 没有可用的 KFC 登录信息")
        auth = dict(row)
        missing = [
            field
            for field in ("ticket", "user_code", "device_id", "kbck")
            if not str(auth.get(field) or "").strip()
        ]
        if missing:
            raise unavailable(f"KFC 登录信息缺少 {', '.join(missing)}")
        return auth

    @staticmethod
    def _ensure_success(response: dict[str, Any]) -> None:
        code = response.get("code")
        if code is None:
            code = response.get("errCode", response.get("errorCode"))
        if str(code) in {"0", "0.0"}:
            return
        message = str(
            response.get("msg")
            or response.get("message")
            or response.get("errMsg")
            or "KFC 订单详情查询失败"
        )
        raise bad_gateway(message)


def main() -> None:
    phone = os.getenv("KFC_TEST_PHONE", "").strip()
    if not phone:
        raise ValueError("请设置 KFC_TEST_PHONE 后再运行测试")
    client = KfcQueryOrderDetailClient()
    try:
        result = client.get_order_info(phone=phone, order_id="1786963385134051967")
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    finally:
        client.close()


kfc_query_order_detail = KfcQueryOrderDetailClient()


if __name__ == "__main__":
    main()
