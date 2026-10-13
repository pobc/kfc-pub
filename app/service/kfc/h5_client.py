from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

from app.config import settings
from app.database import kfc_tickets
from app.errors import bad_gateway, unavailable
from app.service.kfc.contracts import (
    CART_CONFIRM_API,
    CART_CREATE_API,
    CART_CREATE_ENCODE_LIST,
    HOME_PATH,
    INIT_PREORDER_API,
    ORDER_STATUS_API,
    STORE_SEARCH_API,
)
from app.service.kfc.h5_config import H5Config, H5ConfigLoader
from app.service.kfc.http_client import KfcHttpClient, business_result, response_code, response_msg, response_ok
from app.service.kfc.init_session import kfc_init_session

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


class KfcH5Client:
    """KFC H5 business client.

    Store search, checkout and order-status behavior stay together here because
    they are one preorder workflow. HTTP transport and dynamic H5 config remain
    separate infrastructure concerns.
    """

    def __init__(self) -> None:
        self.http = KfcHttpClient()
        self.config_loader = H5ConfigLoader()
        self.stores: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.http.close()

    @property
    def base_url(self) -> str:
        return settings().kfc_h5_base_url

    @property
    def user_agent(self) -> str:
        return _env("KFC_USER_AGENT") or (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )

    def load_config(self) -> H5Config:
        cached_before = self.config_loader.cached
        config = self.config_loader.get(
            lambda path: self.http.fetch_text(
                base_url=self.base_url,
                user_agent=self.user_agent,
                path_or_url=path,
            ),
            HOME_PATH,
        )
        if config is not cached_before:
            logger.info(
                "KFC H5 config loaded build_version=%s api_fversion=%s route_cell=%s",
                config.build_version,
                config.api_fversion,
                config.route_cell,
            )
        return config

    def _auth_for_phone(self, phone: str) -> dict[str, Any]:
        value = phone.strip()
        if not value:
            raise unavailable("KFC Session 缺少 phone")
        row = kfc_tickets.get_latest_enabled_auth_for_phone(value)
        if not row:
            raise unavailable(f"手机号 {value} 没有可用的 KFC Session")
        auth = dict(row)
        required = {
            "ticket": "ticket",
            "user_code": "userCode",
            "phone": "phone",
            "cookie": "cookie",
            "device_id": "deviceId",
        }
        missing = [label for field, label in required.items() if not _text(auth.get(field)).strip()]
        if missing:
            raise unavailable(f"KFC Session 缺少 {', '.join(missing)}")

        config = self.load_config()
        auth["session_id"] = kfc_init_session.get_or_create_session_id(
            phone=_text(auth.get("phone")).strip(),
            device_id=_text(auth.get("device_id")).strip(),
            ticket=_text(auth.get("ticket")).strip(),
            client_version=config.build_version,
            fversion=config.api_fversion,
        )
        logger.info("KFC auth loaded from database phone=%s with current cached/init sessionId", value)
        return auth

    def search_stores(
        self,
        gb_city_code: str,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        context = {"config": self.load_config(), "device_id": uuid.uuid4().hex}
        response = self.http.post(
            context,
            STORE_SEARCH_API,
            {
                "keyword": keyword,
                "gbCityCode": gb_city_code,
                "pageNum": page,
                "pageSize": page_size,
                "mylat": "",
                "mylng": "",
                "mylatPhone": "",
                "mylngPhone": "",
            },
            base_url=self.base_url,
            user_agent=self.user_agent,
        )
        stores = _obj(response.get("data")).get("stores") or []
        result: list[dict[str, Any]] = []
        for value in stores if isinstance(stores, list) else []:
            entry = self._normalise_store(value, gb_city_code)
            if entry:
                self.stores[entry["view"]["storeCode"]] = entry
                result.append(entry["view"])
        return result

    def get_store(self, store_code: str) -> dict[str, Any]:
        return self._resolve_store(store_code, None)["view"]

    def apply_coupon(
        self,
        *,
        phone: str,
        store_code: str,
        store: dict[str, Any],
        pack_type: int,
        items: list[dict[str, Any]],
        coupon_code: str,
    ) -> dict[str, Any]:
        coupon_code = coupon_code.strip()
        if not coupon_code:
            raise bad_gateway("当前链接尚未分配卡券")

        logger.info(
            "KFC apply_coupon store_code=%s pack_type=%s item_count=%s",
            store_code,
            pack_type,
            len(items),
        )
        cart = self._create_authenticated_cart(store_code, store, pack_type, items, phone=phone)
        if not response_ok(cart):
            return business_result(cart)

        coupon_key = hashlib.md5(coupon_code.encode()).hexdigest().upper()
        response = self.http.post_use_coupon_code(
            base_url=self.base_url,
            session_id=cart["session_id"],
            ticket=cart["ticket"],
            coupon_key=coupon_key,
            device_id=cart["context"]["device_id"],
        )
        if not response_ok(response):
            return business_result(response, fallback_msg="肯德基未接受卡券")

        use_cart = _obj(_obj(response.get("data")).get("cart"))
        try:
            if int(use_cart.get("itemsCount")) == 0:
                return {"code": -1, "msg": "当前餐品已售罄", "applied": False}
        except (TypeError, ValueError):
            pass
        cart_id = _text(use_cart.get("id")) or cart["cart_id"]
        if not cart_id:
            return {"code": -1, "msg": "肯德基用券后未返回 cartId", "applied": False}

        confirm = self.http.post(
            cart["context"],
            CART_CONFIRM_API,
            {
                "sessionId": cart["session_id"],
                "ticket": cart["ticket"],
                "cartId": cart_id,
                "encodeList": [],
                "isFromCustomerClient": True,
                "secretKey": "kfc",
            },
            base_url=self.base_url,
            user_agent=self.user_agent,
            sign=True,
            user_code=cart["user_code"],
            accept_business_error=True,
        )
        if not response_ok(confirm):
            return business_result(confirm, fallback_msg="肯德基确认订单失败")

        message = response_msg(confirm) or response_msg(response) or "卡券已使用并确认订单"
        return {
            "code": response_code(confirm, 0),
            "msg": message,
            "cartId": cart_id,
            "applied": True,
            "message": message,
        }

    def get_order_status(self, external_order_id: str, *, phone: str) -> dict[str, Any]:
        auth = self._auth_for_phone(phone)
        context = {"config": self.load_config(), "device_id": uuid.uuid4().hex}
        response = self.http.post(
            context,
            ORDER_STATUS_API,
            {
                "sessionId": auth["session_id"],
                "ticket": auth["ticket"],
                "orderId": external_order_id,
            },
            base_url=self.base_url,
            user_agent=self.user_agent,
            sign=True,
            user_code=auth["user_code"],
        )
        data = _obj(response.get("data"))
        raw = data.get("status")
        return {
            "stage": self._map_stage(raw),
            "rawStatus": raw,
            "queueNumber": self._find_field(data, self._list_env("KFC_QUEUE_NUMBER_FIELDS")),
            "pickupCode": self._find_field(data, self._list_env("KFC_PICKUP_CODE_FIELDS")),
        }

    def _create_authenticated_cart(
        self,
        store_code: str,
        store: dict[str, Any],
        pack_type: int,
        items: list[dict[str, Any]],
        *,
        phone: str,
    ) -> dict[str, Any]:
        if not items:
            raise bad_gateway("订单没有餐品")
        if pack_type not in (1, 2):
            raise bad_gateway("取餐方式无效")

        store_entry = self._resolve_store(store_code, store)
        auth = self._auth_for_phone(phone)
        context = {
            "config": self.load_config(),
            "device_id": _text(auth.get("device_id")).strip(),
            "cookie": _text(auth.get("cookie")).strip(),
            "phone": _text(auth.get("phone")).strip(),
        }
        initialised = self._initialise_preorder(context, store_entry, auth)
        if not response_ok(initialised):
            return initialised

        session_id = _text(initialised.get("session_id")).strip()
        signed_store = _obj(initialised.get("signed_store"))
        created = self.http.post(
            context,
            CART_CREATE_API,
            {
                "sessionId": session_id,
                "ticket": auth["ticket"],
                "store": signed_store,
                "changeCheck": False,
                "scene": 1,
                "encodeList": CART_CREATE_ENCODE_LIST,
                "isFromCustomerClient": True,
                "secretKey": "kfc",
            },
            base_url=self.base_url,
            user_agent=self.user_agent,
            sign=True,
            user_code=auth["user_code"],
            accept_business_error=True,
        )
        if not response_ok(created):
            return business_result(created, fallback_msg="肯德基创建购物车失败")

        data = _obj(created.get("data"))
        cart_id = _text(_obj(data.get("cart")).get("id")) or _text(data.get("cartId"))
        if not cart_id:
            return {"code": -1, "msg": "肯德基创建购物车未返回 cartId", "applied": False}
        return {
            "code": 0,
            "msg": response_msg(created),
            "context": context,
            "user_code": auth["user_code"],
            "session_id": session_id,
            "ticket": auth["ticket"],
            "phone": auth["phone"],
            "open_id": auth.get("open_id"),
            "cart_id": cart_id,
        }

    def _initialise_preorder(
        self,
        context: dict[str, Any],
        store_entry: dict[str, Any],
        auth: dict[str, Any],
    ) -> dict[str, Any]:
        store_code = _text(store_entry["view"].get("storeCode")).strip()
        booking_date = self._next_booking_date(store_entry["raw"])
        response = self.http.post(
            context,
            INIT_PREORDER_API,
            {
                "sessionId": auth["session_id"],
                "ticket": auth["ticket"],
                "body": {"geoLocation": {}},
                "storeCodeParent": store_code,
                "storeCodeCurrent": store_code,
                "bookingDate": booking_date,
            },
            base_url=self.base_url,
            user_agent=self.user_agent,
            sign=True,
            user_code=auth["user_code"],
            accept_business_error=True,
        )
        if not response_ok(response):
            return business_result(response, fallback_msg="肯德基预点会话初始化失败")

        data = _obj(response.get("data"))
        session_id = _text(data.get("sessionId")).strip()
        if not session_id:
            return {"code": -1, "msg": "肯德基预点会话初始化失败", "applied": False}
        return {
            "code": 0,
            "msg": response_msg(response),
            "session_id": session_id,
            "signed_store": self._signed_store_from_init(data, store_entry["raw"], store_code),
            "booking_date": booking_date,
        }

    def _resolve_store(self, store_code: str, supplied: dict[str, Any] | None) -> dict[str, Any]:
        if supplied:
            city_code = _text(supplied.get("gbCityCode") or supplied.get("citycode") or supplied.get("cityCode"))
            entry = self._normalise_store(supplied, city_code)
            if not entry or entry["view"]["storeCode"] != store_code:
                raise bad_gateway("前端餐厅数据与所选餐厅不一致")
            return entry
        cached = self.stores.get(store_code)
        if not cached:
            raise bad_gateway("请重新搜索并选择餐厅")
        return cached

    @staticmethod
    def _store_code_of(store: dict[str, Any]) -> str:
        return _text(store.get("storecode") or store.get("storeCode")).strip()

    @staticmethod
    def _normalise_store(value: Any, city_code: str) -> dict[str, Any] | None:
        raw = _obj(value)
        store_code = _text(raw.get("storecode") or raw.get("storeCode"))
        if not store_code:
            return None
        status = "OPEN" if _text(raw.get("status")).lower() in {"1", "open", "营业中", "true"} else "CLOSED"
        store_pic = _obj(raw.get("storePic"))
        images: list[Any] = []
        for key in ("storeSelfPic", "storeEnvirPic", "storeBuzAreaPic"):
            image_values = store_pic.get(key)
            images.extend(image_values if isinstance(image_values, list) else [])
        image_url = next((_text(value) for value in images if _text(value)), "")
        view = {
            "storeCode": store_code,
            "name": _text(raw.get("storename") or raw.get("storeName")),
            "address": _text(raw.get("address")),
            "cityCode": city_code,
            "status": status,
            "businessHours": "–".join(
                value for value in [_text(raw.get("starttime")), _text(raw.get("endtime"))] if value
            ),
            "storePayload": raw,
        }
        if image_url:
            view["imageUrl"] = image_url
        return {"raw": raw, "view": view}

    @classmethod
    def _signed_store_from_init(
        cls,
        data: dict[str, Any],
        fallback: dict[str, Any],
        store_code: str,
    ) -> dict[str, Any]:
        recommend = _obj(data.get("dataRecommend"))
        recommended_store = _obj(recommend.get("storeRecommend"))
        if cls._store_code_of(recommended_store) == store_code:
            return recommended_store
        stores = recommend.get("stores")
        if isinstance(stores, list):
            for value in stores:
                store = _obj(value)
                if cls._store_code_of(store) == store_code:
                    return store
        return fallback

    @staticmethod
    def _next_booking_date(store: dict[str, Any]) -> int:
        interval_minutes = max(1, _int(store.get("bookingInterval"), 15))
        interval_ms = interval_minutes * 60_000
        now = int(time.time() * 1000)
        return ((now + interval_ms + interval_ms - 1) // interval_ms) * interval_ms

    @staticmethod
    def _map_stage(raw_status: Any) -> str:
        default = {"504": "FAILED"}
        try:
            configured = json.loads(_env("KFC_STATUS_MAP_JSON") or "{}")
        except json.JSONDecodeError as exc:
            raise unavailable("KFC_STATUS_MAP_JSON 不是合法 JSON") from exc
        value = _text(configured.get(_text(raw_status)) or default.get(_text(raw_status)))
        allowed = {"SUBMITTED", "PREPARING", "READY", "COMPLETED", "FAILED", "UNKNOWN"}
        return value if value in allowed else "UNKNOWN"

    @classmethod
    def _find_field(cls, value: Any, fields: list[str]) -> str:
        if not fields:
            return ""
        if isinstance(value, list):
            for child in value:
                found = cls._find_field(child, fields)
                if found:
                    return found
            return ""
        if not isinstance(value, dict):
            return ""
        for field in fields:
            if _text(value.get(field)):
                return _text(value.get(field))
        for child in value.values():
            found = cls._find_field(child, fields)
            if found:
                return found
        return ""

    @staticmethod
    def _list_env(name: str) -> list[str]:
        return [value.strip() for value in _env(name).split(",") if value.strip()]

    @staticmethod
    def _find_auth_value(value: Any, fields: list[str]) -> str:
        wanted = {field.lower() for field in fields}

        def walk(current: Any) -> str:
            if isinstance(current, str):
                text = current.strip()
                if text and text[:1] in "{[":
                    try:
                        return walk(json.loads(text))
                    except Exception:
                        return ""
                return ""
            if isinstance(current, list):
                for child in current:
                    found = walk(child)
                    if found:
                        return found
                return ""
            if not isinstance(current, dict):
                return ""
            for key, child in current.items():
                if str(key).lower() in wanted and child not in (None, ""):
                    text = str(child).strip()
                    if text and text != "[object Object]":
                        return text
            for child in current.values():
                found = walk(child)
                if found:
                    return found
            return ""

        return walk(value)
