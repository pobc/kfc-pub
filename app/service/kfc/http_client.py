from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests

from app.service.kfc.contracts import (
    CART_USE_COUPON_CODE_API,
    HOME_PATH,
    USE_COUPON_CLIENT_VERSION,
    USE_COUPON_FVERSION,
)
from app.service.kfc.h5_config import H5Config
from app.config import settings
from app.errors import bad_gateway

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def response_code(response: dict[str, Any], fallback: int = -1) -> Any:
    value = response.get("code")
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return _text(value)


def response_msg(response: dict[str, Any], fallback: str = "") -> str:
    return _text(response.get("msg") or response.get("message")).strip() or fallback


def response_ok(response: dict[str, Any]) -> bool:
    return response_code(response, -1) == 0


def business_result(
    response: dict[str, Any],
    *,
    fallback_msg: str = "肯德基接口返回失败",
    applied: bool = False,
) -> dict[str, Any]:
    return {
        "code": response_code(response),
        "msg": response_msg(response, fallback_msg),
        "applied": applied,
    }


def require_response_code(data: dict[str, Any], *, accept_business_error: bool) -> int:
    raw = data.get("code")
    try:
        code = int(raw)
    except (TypeError, ValueError) as exc:
        raise bad_gateway("肯德基接口缺少合法 code") from exc
    if code != 0 and not accept_business_error:
        raise bad_gateway(response_msg(data, "肯德基接口返回失败"))
    return code


class KfcHttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.timeout = settings().kfc_request_timeout_ms / 1000

    def close(self) -> None:
        self.session.close()

    def cookie_header(self, route_cell: str, auth_cookie: str = "") -> str:
        cookies: dict[str, str] = {}
        for part in auth_cookie.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name:
                cookies[name] = value
        for cookie in self.session.cookies:
            cookies[cookie.name] = cookie.value
        cookies["route-cell"] = route_cell
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def prepare_request(
        self,
        context: dict[str, Any],
        api: str,
        body: dict[str, Any],
        *,
        base_url: str,
        user_agent: str,
        sign: bool = True,
        user_code: str = "",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        config: H5Config = context["config"]
        payload = {
            "portalType": "WAP",
            "channelName": "Mobile Web",
            "channelId": "13",
            "brand": "KFC_PRE",
            "business": "preorder",
            "deviceId": context["device_id"],
            "clientVersion": config.build_version,
            "fversion": config.api_fversion,
            "versionNum": "5",
            "env": "qcpil",
            **body,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": base_url,
            "referer": f"{base_url}{HOME_PATH}",
            "user-agent": user_agent,
            "x-yumc-client-deviceid": context["device_id"],
            "x-yumc-route-cell": config.route_cell,
            "x-yumc-route-channel": "Mobile Web",
            "x-yumc-route-usercode": user_code,
            "sysbrand": "kfc",
            "syschannel": "preorder",
        }
        cookie = self.cookie_header(config.route_cell, _text(context.get("cookie")))
        if cookie:
            headers["cookie"] = cookie
        if sign:
            timestamp = str(int(time.time() * 1000))
            source = f"{config.kbs_key}\t{config.kbs_secret}\t{timestamp}\t{api}\t\t{raw}"
            headers.update(
                {
                    "kbck": config.kbs_key,
                    "kbcts": timestamp,
                    "kbsv": hashlib.md5(source.encode()).hexdigest(),
                    "rcsav": "",
                    "rcsbcid": f"bid_{context['device_id']}",
                    "rcsdcid": context["device_id"],
                }
            )
        return payload, headers

    def post(
        self,
        context: dict[str, Any],
        api: str,
        body: dict[str, Any],
        *,
        base_url: str,
        user_agent: str,
        sign: bool = True,
        user_code: str = "",
        accept_business_error: bool = False,
    ) -> dict[str, Any]:
        payload, headers = self.prepare_request(
            context,
            api,
            body,
            base_url=base_url,
            user_agent=user_agent,
            sign=sign,
            user_code=user_code,
        )
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        url = f"{base_url}{api}"
        started = time.perf_counter()
        try:
            response = self.session.post(url, headers=headers, data=raw.encode(), timeout=self.timeout)
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception("KFC POST exception api=%s duration=%.1fms", api, duration)
            raise bad_gateway("肯德基接口暂时不可用") from exc
        duration = (time.perf_counter() - started) * 1000
        logger.info("KFC POST response api=%s status=%s duration=%.1fms", api, response.status_code, duration)
        try:
            data = response.json()
        except Exception as exc:
            logger.exception("KFC POST JSON decode failed api=%s status=%s", api, response.status_code)
            raise bad_gateway("肯德基接口暂时不可用") from exc
        if response.status_code >= 400 or not isinstance(data, dict):
            raise bad_gateway("肯德基接口暂时不可用")
        code = require_response_code(data, accept_business_error=accept_business_error)
        if code != 0:
            logger.warning("KFC POST business result api=%s code=%s msg=%s", api, code, response_msg(data))
        return data

    def post_use_coupon_code(
        self,
        *,
        base_url: str,
        session_id: str,
        ticket: str,
        coupon_key: str,
        device_id: str,
    ) -> dict[str, Any]:
        rcsbc_id = f"bid_{device_id}"
        payload = {
            "portalType": "WAP",
            "channelName": "WECHATMINI",
            "channelId": "13_5",
            "brand": "KFC_PRE",
            "business": "preorder",
            "sessionId": session_id,
            "deviceId": device_id,
            "clientVersion": USE_COUPON_CLIENT_VERSION,
            "openId": "",
            "ticket": ticket,
            "fversion": USE_COUPON_FVERSION,
            "versionNum": "5",
            "couponKey": coupon_key,
            "scene": 1,
            "env": "wgb",
            "rcsbcId": rcsbc_id,
            "rcsdcId": device_id,
            "riskCtrlParam": {
                "channelName": "WECHATMINI",
                "openId": "",
                "browserId": rcsbc_id,
                "deviceId": device_id,
                "td_id": "",
                "miniSysInfo": {
                    "model": "microsoft",
                    "pixelRatio": 1.5,
                    "language": "zh_CN",
                    "version": "4.1.12.26",
                    "system": "Windows 11 x64",
                    "platform": "windows",
                },
            },
            "encodeList": [],
            "isFromCustomerClient": True,
            "secretKey": "kfc",
        }
        url = f"{base_url}{CART_USE_COUPON_CODE_API}"
        started = time.perf_counter()
        try:
            response = self.session.post(
                url,
                headers={"accept": "application/json", "content-type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception("KFC useCouponCode exception duration=%.1fms", duration)
            raise bad_gateway("肯德基接口暂时不可用") from exc
        duration = (time.perf_counter() - started) * 1000
        logger.info(
            "KFC useCouponCode response status=%s duration=%.1fms",
            response.status_code,
            duration,
        )
        try:
            data = response.json()
        except Exception as exc:
            raise bad_gateway("肯德基接口暂时不可用") from exc
        if response.status_code >= 400 or not isinstance(data, dict):
            raise bad_gateway("肯德基接口暂时不可用")
        require_response_code(data, accept_business_error=True)
        return data

    def fetch_text(self, *, base_url: str, user_agent: str, path_or_url: str) -> tuple[str, str]:
        url = path_or_url if path_or_url.startswith("http") else urljoin(base_url + "/", path_or_url.lstrip("/"))
        started = time.perf_counter()
        try:
            response = self.session.get(url, headers={"user-agent": user_agent}, timeout=self.timeout)
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception("KFC GET exception path=%s duration=%.1fms", path_or_url, duration)
            raise bad_gateway("肯德基 H5 配置获取失败") from exc
        duration = (time.perf_counter() - started) * 1000
        logger.info(
            "KFC GET response path=%s status=%s duration=%.1fms content_length=%s",
            path_or_url,
            response.status_code,
            duration,
            len(response.content),
        )
        if response.status_code >= 400:
            raise bad_gateway("肯德基 H5 配置获取失败")
        return response.text, response.headers.get("set-cookie", "")
