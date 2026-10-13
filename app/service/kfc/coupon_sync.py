from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import requests

from app.config import settings
from app.errors import bad_gateway, unavailable
from app.service.kfc.contracts import HOME_PATH
from app.service.kfc.h5_client import KfcH5Client
from app.service.kfc.h5_config import H5Config

logger = logging.getLogger(__name__)

DEFAULT_AVAILABLE_COUPON_API = "/crm/queryAvailableCoupon"
DEFAULT_HISTORY_COUPON_API = "/crm/queryCouponByUserCode"
ALLOWED_COUPON_HOSTS = {"appcoupon.kfc.com.cn", "tappcoupon.kfc.com.cn"}
STORE_CHANNELS = {"CPOS", "DT", "PreOrder"}
DELIVERY_CHANNEL = "Delivery"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _require_success_code(data: dict[str, Any]) -> None:
    raw_code = data.get("errCode", data.get("code"))
    try:
        code = int(raw_code)
    except (TypeError, ValueError) as exc:
        logger.error("KFC coupon API invalid response code data=%s", data)
        raise bad_gateway("肯德基卡券接口缺少合法返回码") from exc
    if code != 0:
        logger.warning("KFC coupon API business failure code=%s msg=%s", code, data.get("errMsg") or data.get("msg"))
        raise bad_gateway(_text(data.get("errMsg") or data.get("msg")) or "肯德基卡券接口返回失败")


class KfcCouponSyncClient:
    def __init__(self, h5_client: KfcH5Client) -> None:
        self.h5 = h5_client
        self._timeout = settings().kfc_request_timeout_ms / 1000

    def list_coupons(self, *, ticket: str, user_code: str, cookie: str = "") -> list[dict[str, Any]]:
        available_api = _env("KFC_COUPON_AVAILABLE_API") or DEFAULT_AVAILABLE_COUPON_API
        history_api = _env("KFC_COUPON_HISTORY_API") or DEFAULT_HISTORY_COUPON_API
        context = {"config": self.h5.load_config(), "device_id": uuid.uuid4().hex}
        available_query = self._json_env("KFC_COUPON_AVAILABLE_QUERY_JSON")
        history_query = self._json_env("KFC_COUPON_HISTORY_QUERY_JSON")
        logger.info(
            "KFC coupon sync started ticket=%s user_code=%s cookie=%s available_api=%s history_api=%s",
            ticket,
            user_code,
            cookie,
            available_api,
            history_api,
        )

        def available(tab: int) -> dict[str, Any]:
            return {
                "couponUseTabs": tab,
                "isBeforeStart": 1,
                "isCardbag": True,
                "isFromCustomerClient": True,
                "needTabCount": "Y",
                "pageNum": 1,
                "pageSize": 30000,
                **available_query,
                "token": ticket,
            }

        def history(status: int) -> dict[str, Any]:
            return {
                "couponStatus": status,
                "isFromCustomerClient": True,
                "pageNum": 1,
                "pageSize": 30000,
                "secretKey": "kfc",
                **history_query,
                "token": ticket,
            }

        with requests.Session() as client:
            responses = [
                (self._get(client, context, available_api, available(0), user_code, cookie), "AVAILABLE", None),
                (self._get(client, context, available_api, available(1), user_code, cookie), "AVAILABLE", None),
                (self._get(client, context, history_api, history(3), user_code, cookie), "USED", 1),
                (self._get(client, context, history_api, history(4), user_code, cookie), "EXPIRED", None),
                (self._get(client, context, history_api, history(8), user_code, cookie), "REFUNDED", 2),
            ]

        found: dict[str, dict[str, Any]] = {}
        priority = {"AVAILABLE": 1, "USED": 2, "EXPIRED": 3, "REFUNDED": 4}
        for response, fallback, required_use_status in responses:
            extracted = self._extract(response, fallback, required_use_status)
            logger.info("KFC coupon response extracted fallback=%s count=%s", fallback, len(extracted))
            for item in extracted:
                existing = found.get(item["couponCode"])
                if not existing:
                    found[item["couponCode"]] = item
                    continue
                if item.get("createdAt") and (
                    not existing.get("createdAt") or item["createdAt"] < existing["createdAt"]
                ):
                    existing["createdAt"] = item["createdAt"]
                if priority[item["couponStatus"]] > priority[existing["couponStatus"]]:
                    existing["couponStatus"] = item["couponStatus"]
                    existing["productName"] = item["productName"] or existing["productName"]
        result = list(found.values())
        logger.info("KFC coupon sync completed total=%s", len(result))
        return result

    def _get(
        self,
        client: requests.Session,
        context: dict[str, Any],
        api: str,
        params: dict[str, Any],
        user_code: str,
        auth_cookie: str,
    ) -> dict[str, Any]:
        if not api.startswith("/"):
            raise unavailable("KFC 卡券 API 必须是绝对路径")
        base = settings().kfc_coupon_base_url
        parsed = urlparse(base)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_COUPON_HOSTS:
            raise unavailable("KFC_COUPON_BASE_URL 必须是肯德基官方 appcoupon 域名")
        config: H5Config = context["config"]
        canonical = "&".join(
            f"{quote(str(k), safe='')}={'' if v is None else _text(v)}"
            for k, v in sorted(params.items())
            if v is not None
        )
        query = urlencode(sorted((k, "" if v is None else _text(v)) for k, v in params.items() if v is not None))
        timestamp = str(int(time.time() * 1000))
        source = f"{config.kbs_key}\t{config.kbs_secret}\t{timestamp}\t{api}\t{canonical}"
        headers = {
            "accept": "application/json",
            "origin": self.h5.base_url,
            "referer": f"{self.h5.base_url}{HOME_PATH}",
            "user-agent": self.h5.user_agent,
            "x-yumc-client-deviceid": context["device_id"],
            "x-yumc-route-cell": config.route_cell,
            "x-yumc-route-channel": "Mobile Web",
            "x-yumc-route-usercode": user_code,
            "sysbrand": "kfc",
            "syschannel": "preorder",
            "kbck": config.kbs_key,
            "kbcts": timestamp,
            "kbsv": hashlib.md5(source.encode()).hexdigest(),
        }
        cookie = self.h5.http.cookie_header(config.route_cell, auth_cookie)
        if cookie:
            headers["cookie"] = cookie
        logger.info("KFC coupon GET request api=%s params=%s headers=%s", api, params, headers)
        started = time.perf_counter()
        try:
            response = client.get(f"{base}{api}?{query}", headers=headers, timeout=self._timeout)
            data = response.json()
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception("KFC coupon GET exception api=%s duration=%.1fms", api, duration)
            raise bad_gateway("肯德基接口暂时不可用") from exc
        duration = (time.perf_counter() - started) * 1000
        logger.info(
            "KFC coupon GET response api=%s status=%s duration=%.1fms code=%s msg=%s",
            api,
            response.status_code,
            duration,
            data.get("errCode", data.get("code")) if isinstance(data, dict) else None,
            data.get("errMsg", data.get("msg")) if isinstance(data, dict) else None,
        )
        if response.status_code >= 400 or not isinstance(data, dict):
            raise bad_gateway("肯德基接口暂时不可用")
        _require_success_code(data)
        return data

    def _extract(
        self,
        root: Any,
        fallback_status: str,
        required_use_status: int | None,
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    walk(child)
                return
            if not isinstance(value, dict):
                return
            if self._syncable(value):
                try:
                    if required_use_status is not None and int(value.get("useStatus")) != required_use_status:
                        raise ValueError
                    coupon_code = _text(value.get("couponCode")).strip()
                    if coupon_code and coupon_code not in found:
                        use_status = value.get("useStatus")
                        valid_status = value.get("validStatus")
                        status = fallback_status
                        try:
                            if int(use_status) == 2:
                                status = "REFUNDED"
                            elif int(use_status) == 1:
                                status = "USED"
                            elif int(valid_status) == 0:
                                status = "EXPIRED"
                        except (TypeError, ValueError):
                            pass
                        created_at = None
                        try:
                            millis = int(value.get("placeTime"))
                            if millis > 0:
                                from datetime import datetime, timezone

                                created_at = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
                        except (TypeError, ValueError):
                            pass
                        found[coupon_code] = {
                            "couponCode": coupon_code,
                            "productName": _text(value.get("useProductName") or value.get("couponName")).strip(),
                            "createdAt": created_at,
                            "couponStatus": status,
                        }
                except ValueError:
                    pass
            for child in value.values():
                walk(child)

        walk(root)
        return list(found.values())

    @staticmethod
    def _channels(coupon: dict[str, Any]) -> set[str]:
        return {v.strip() for v in _text(coupon.get("couponUsedChannels")).split(",") if v.strip()}

    def _syncable(self, coupon: dict[str, Any]) -> bool:
        try:
            if float(coupon.get("salePrice")) != 0:
                return False
        except (TypeError, ValueError):
            return False
        channels = self._channels(coupon)
        return DELIVERY_CHANNEL in channels or bool(STORE_CHANNELS & channels)

    @staticmethod
    def _json_env(name: str) -> dict[str, Any]:
        try:
            value = json.loads(_env(name) or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError as exc:
            logger.error("KFC coupon env JSON invalid name=%s value=%s", name, _env(name))
            raise unavailable(f"{name} 不是合法 JSON") from exc


kfc_h5 = KfcH5Client()
kfc_coupon_sync = KfcCouponSyncClient(kfc_h5)
