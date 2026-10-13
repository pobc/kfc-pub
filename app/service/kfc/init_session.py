from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

from app.config import settings
from app.database import kfc_sessions
from app.errors import bad_gateway
from app.logging_utils import safe_json

logger = logging.getLogger(__name__)

INIT_SESSION_API = "/preorder-portal/api/v2/init/initSession"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _require_success_code(data: dict[str, Any]) -> None:
    raw_code = data.get("code")
    try:
        code = int(raw_code)
    except (TypeError, ValueError) as exc:
        logger.error("KFC initSession invalid response code data=%s", safe_json(data))
        raise bad_gateway("肯德基初始化 Session 接口缺少合法 code") from exc
    if code != 0:
        message = _text(data.get("msg") or data.get("message")).strip() or "肯德基初始化 Session 失败"
        logger.warning("KFC initSession business failure code=%s msg=%s data=%s", code, message, safe_json(data))
        raise bad_gateway(message)


class KfcInitSessionClient:
    def __init__(self) -> None:
        self._timeout = settings().kfc_request_timeout_ms / 1000
        self._client = requests.Session()

    def close(self) -> None:
        self._client.close()

    @property
    def base_url(self) -> str:
        return settings().kfc_h5_base_url

    @property
    def user_agent(self) -> str:
        return _env("KFC_USER_AGENT") or (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )

    @staticmethod
    def build_payload(
        *,
        device_id: str,
        ticket: str,
        portal_type: str = "WAP",
        channel_name: str = "Mobile Web",
        channel_id: str = "13",
        brand: str = "KFC_PRE",
        business: str = "preorder",
        client_version: str = "v6.305(8b4fd15d)",
        fversion: str = "251029",
        version_num: str = "5",
        encode_list: list[Any] | None = None,
        is_from_customer_client: bool = True,
        secret_key: str = "kfc",
    ) -> dict[str, Any]:
        return {
            "portalType": portal_type,
            "channelName": channel_name,
            "channelId": channel_id,
            "brand": brand,
            "business": business,
            "deviceId": device_id,
            "clientVersion": client_version,
            "fversion": fversion,
            "versionNum": version_num,
            "ticket": ticket,
            "encodeList": [] if encode_list is None else encode_list,
            "isFromCustomerClient": is_from_customer_client,
            "secretKey": secret_key,
        }

    @staticmethod
    def _session_id_from_response(data: dict[str, Any]) -> str:
        return _text(_obj(data.get("data")).get("sessionId")).strip()

    @staticmethod
    def _phone_from_response(data: dict[str, Any]) -> str:
        login = _obj(_obj(data.get("data")).get("dataLogin"))
        user = _obj(login.get("user"))
        return _text(user.get("phone")).strip()

    def get_or_create_session_id(
        self,
        *,
        phone: str,
        device_id: str,
        ticket: str,
        client_version: str = "v6.305(8b4fd15d)",
        fversion: str = "251029",
    ) -> str:
        phone = phone.strip()
        device_id = device_id.strip()
        ticket = ticket.strip()
        if not phone:
            raise ValueError("phone 不能为空")
        if not device_id:
            raise ValueError("device_id 不能为空")
        if not ticket:
            raise ValueError("ticket 不能为空")

        cached = kfc_sessions.get_valid_kfc_session(phone=phone)
        if cached:
            session_id = _text(cached.get("session_id")).strip()
            if session_id:
                logger.info("KFC initSession cache hit phone=%s session_id=%s", phone, session_id)
                return session_id

        logger.info(
            "KFC initSession cache miss phone=%s device_id=%s ticket=%s client_version=%s fversion=%s",
            phone,
            device_id,
            ticket,
            client_version,
            fversion,
        )
        data = self.init_session(
            phone=phone,
            device_id=device_id,
            ticket=ticket,
            client_version=client_version,
            fversion=fversion,
        )
        session_id = self._session_id_from_response(data)
        if not session_id:
            logger.error("KFC initSession missing sessionId phone=%s response=%s", phone, safe_json(data))
            raise bad_gateway("肯德基初始化 Session 接口未返回 sessionId")
        return session_id

    def init_session(
        self,
        *,
        device_id: str,
        ticket: str,
        phone: str = "",
        portal_type: str = "WAP",
        channel_name: str = "Mobile Web",
        channel_id: str = "13",
        brand: str = "KFC_PRE",
        business: str = "preorder",
        client_version: str = "v6.305(8b4fd15d)",
        fversion: str = "251029",
        version_num: str = "5",
        encode_list: list[Any] | None = None,
        is_from_customer_client: bool = True,
        secret_key: str = "kfc",
    ) -> dict[str, Any]:
        device_id = device_id.strip()
        ticket = ticket.strip()
        phone = phone.strip()
        if not device_id:
            raise ValueError("device_id 不能为空")
        if not ticket:
            raise ValueError("ticket 不能为空")

        url = f"{self.base_url}{INIT_SESSION_API}"
        payload = self.build_payload(
            device_id=device_id,
            ticket=ticket,
            portal_type=portal_type,
            channel_name=channel_name,
            channel_id=channel_id,
            brand=brand,
            business=business,
            client_version=client_version,
            fversion=fversion,
            version_num=version_num,
            encode_list=encode_list,
            is_from_customer_client=is_from_customer_client,
            secret_key=secret_key,
        )
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": self.base_url,
            "referer": f"{self.base_url}/preorder-taro/home",
            "user-agent": self.user_agent,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        logger.info(
            "KFC initSession request url=%s headers=%s body=%s",
            url,
            safe_json(headers),
            safe_json(payload),
        )

        started = time.perf_counter()
        try:
            response = self._client.post(
                url,
                headers=headers,
                data=raw.encode(),
                timeout=self._timeout,
            )
        except Exception as exc:
            duration = (time.perf_counter() - started) * 1000
            logger.exception("KFC initSession exception api=%s duration=%.1fms", INIT_SESSION_API, duration)
            raise bad_gateway("肯德基初始化 Session 接口暂时不可用") from exc

        duration = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            logger.error(
                "KFC initSession HTTP failure status=%s duration=%.1fms body=%s",
                response.status_code,
                duration,
                response.text,
            )
            raise bad_gateway(f"肯德基初始化 Session 接口 HTTP {response.status_code}")
        try:
            data = response.json()
        except Exception as exc:
            logger.exception(
                "KFC initSession JSON decode failed status=%s duration=%.1fms body=%s",
                response.status_code,
                duration,
                response.text,
            )
            raise bad_gateway("肯德基初始化 Session 接口未返回合法 JSON") from exc
        if not isinstance(data, dict):
            logger.error("KFC initSession invalid response type type=%s body=%s", type(data).__name__, safe_json(data))
            raise bad_gateway("肯德基初始化 Session 接口返回格式错误")

        logger.info(
            "KFC initSession response status=%s duration=%.1fms headers=%s body=%s",
            response.status_code,
            duration,
            safe_json(dict(response.headers)),
            safe_json(data),
        )

        _require_success_code(data)
        session_id = self._session_id_from_response(data)
        if session_id:
            association_phone = phone or self._phone_from_response(data)
            if not association_phone:
                logger.error("KFC initSession success but missing phone session_id=%s response=%s", session_id, safe_json(data))
                raise bad_gateway("肯德基初始化 Session 成功但无法关联手机号")
            kfc_sessions.save_kfc_session(
                phone=association_phone,
                session_id=session_id,
            )
            logger.info(
                "KFC initSession saved phone=%s session_id=%s",
                association_phone,
                session_id,
            )
        return data


kfc_init_session = KfcInitSessionClient()
