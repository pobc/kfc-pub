from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.service.kfc.browser_auth import open_authenticated_chrome
from app.service.kfc.coupon_sync import kfc_coupon_sync
from app.service.kfc.session_capture import extract_logger_plain_kbck
from app.database import kfc_sessions, kfc_tickets, platforms
from app.errors import bad_request, not_found
from app.utils import now

logger = logging.getLogger(__name__)

_INVALID_AUTH_VALUES = {"", "[]", "{}", "null", "none", "undefined", "[object object]"}
_KEY_USER_STORAGE_KEY = "KEY_USER"
_DEVICE_ID_STORAGE_KEY = "DEVICE_ID"
_SESSION_STORAGE_KEY = "PERSISTENCE_SESSION_ID_BY_15MIN_preorder_13_0"
_LOGGER_PLAIN_STORAGE_KEY = "logger-plain"

# 保留旧名称，避免已有测试和内部调用失效。
_extract_logger_plain_kbck = extract_logger_plain_kbck


def _clean_auth_value(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _INVALID_AUTH_VALUES else text


def list_kfc_sessions() -> list[dict[str, Any]]:
    result = kfc_tickets.list_kfc_sessions()
    logger.info("KFC sessions listed count=%s", len(result))
    return result


def create_kfc_session(data: dict[str, Any]) -> dict[str, Any]:
    phone = str(data.get("phone") or "").strip()
    payload = dict(data)
    logger.info("KFC session create requested phone=%s data=%s", phone, payload)
    if phone:
        payload["phone"] = phone
        payload["name"] = phone
        existing = next((item for item in kfc_tickets.list_kfc_sessions() if item.get("phone") == phone), None)
        if existing:
            payload["enabled"] = True
            result = kfc_tickets.update_kfc_session(existing["id"], payload)
            logger.info("KFC session reused phone=%s session_id=%s", phone, existing["id"])
            return result
    result = kfc_tickets.create_kfc_session(payload)
    logger.info("KFC session created phone=%s session_id=%s", phone, result.get("id"))
    return result


def update_kfc_session(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    if "phone" in payload and payload["phone"] is not None:
        phone = str(payload["phone"]).strip()
        payload["phone"] = phone
        if phone:
            payload["name"] = phone
    logger.info("KFC session update requested session_id=%s data=%s", session_id, payload)
    result = kfc_tickets.update_kfc_session(session_id, payload)
    logger.info("KFC session updated session_id=%s", session_id)
    return result


def delete_kfc_session(session_id: str) -> dict[str, bool]:
    logger.info("KFC session delete requested session_id=%s", session_id)
    result = kfc_tickets.delete_kfc_session(session_id)
    logger.info("KFC session deleted session_id=%s deleted=%s", session_id, result.get("deleted"))
    return result


def _decode_storage_value(value: Any) -> Any:
    current = value
    for _ in range(8):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text or text[:1] not in '{["':
            return current
        try:
            decoded = json.loads(text)
        except Exception:
            return current
        if decoded == current:
            return current
        current = decoded
    return current


def _storage_object(value: Any) -> dict[str, Any]:
    decoded = _decode_storage_value(value)
    return decoded if isinstance(decoded, dict) else {}


def _unwrap_data_object(value: Any) -> dict[str, Any]:
    current = _decode_storage_value(value)
    for _ in range(8):
        if not isinstance(current, dict):
            return {}
        if "sessionId" in current or "session_id" in current:
            return current
        if "data" not in current:
            return current
        current = _decode_storage_value(current.get("data"))
    return current if isinstance(current, dict) else {}


def _extract_login_storage(local_storage: dict[str, Any]) -> dict[str, str]:
    key_user = _storage_object(local_storage.get(_KEY_USER_STORAGE_KEY))
    user_content = _storage_object(key_user.get("content"))

    phone = _clean_auth_value(user_content.get("phone")) or _clean_auth_value(user_content.get("bPhone"))
    ticket = _clean_auth_value(user_content.get("token"))
    open_id = _clean_auth_value(user_content.get("openId")) or _clean_auth_value(user_content.get("openid"))

    device_wrapper = _storage_object(local_storage.get(_DEVICE_ID_STORAGE_KEY))
    device_id = _clean_auth_value(_decode_storage_value(device_wrapper.get("data")))

    session_info = _unwrap_data_object(local_storage.get(_SESSION_STORAGE_KEY))
    session_id = _clean_auth_value(session_info.get("sessionId")) or _clean_auth_value(session_info.get("session_id"))
    user_code = _clean_auth_value(session_info.get("userCode")) or _clean_auth_value(session_info.get("user_code"))
    kbck = extract_logger_plain_kbck(local_storage.get(_LOGGER_PLAIN_STORAGE_KEY))

    return {
        "phone": phone,
        "ticket": ticket,
        "open_id": open_id,
        "device_id": device_id,
        "session_id": session_id,
        "user_code": user_code,
        "kbck": kbck,
    }


def import_kfc_auth_state(
    name: str,
    auth_state: dict[str, Any],
    remark: str = "",
    captured_kbck: str = "",
) -> dict[str, Any]:
    local_storage = auth_state.get("localStorage") if isinstance(auth_state.get("localStorage"), dict) else {}
    login_storage = _extract_login_storage(local_storage)

    phone = login_storage["phone"]
    ticket = login_storage["ticket"]
    device_id = login_storage["device_id"]
    session_id = login_storage["session_id"]
    user_code = login_storage["user_code"]
    kbck = login_storage["kbck"] or _clean_auth_value(captured_kbck)
    summary = auth_state.get("summary") if isinstance(auth_state.get("summary"), dict) else {}
    open_id = (
        login_storage["open_id"] or _clean_auth_value(summary.get("openid")) or _clean_auth_value(summary.get("openId"))
    )

    logger.info(
        "KFC auth import parsed name=%s phone=%s ticket=%s device_id=%s session_id=%s user_code=%s kbck=%s open_id=%s",
        name,
        phone,
        ticket,
        device_id,
        session_id,
        user_code,
        kbck,
        open_id,
    )

    if not phone:
        logger.warning("KFC auth import failed reason=missing_phone")
        raise bad_request(f"localStorage.{_KEY_USER_STORAGE_KEY} 中没有捕获到 phone/bPhone")
    if not ticket:
        logger.warning("KFC auth import failed phone=%s reason=missing_ticket", phone)
        raise bad_request(f"localStorage.{_KEY_USER_STORAGE_KEY} 中没有捕获到 token")
    if not device_id:
        logger.warning("KFC auth import failed phone=%s reason=missing_device_id", phone)
        raise bad_request(f"localStorage.{_DEVICE_ID_STORAGE_KEY} 中没有捕获到 device_id")
    if not session_id or not user_code:
        logger.warning("KFC auth import failed phone=%s reason=missing_session_or_user_code", phone)
        raise bad_request(f"localStorage.{_SESSION_STORAGE_KEY} 中没有捕获到 sessionId/userCode")

    cookies = auth_state.get("cookies") if isinstance(auth_state.get("cookies"), list) else []
    by_name: dict[str, str] = {}
    for raw in cookies:
        if not isinstance(raw, dict):
            continue
        cookie_name = str(raw.get("name", "")).strip()
        domain = str(raw.get("domain", "")).lower()
        if not cookie_name:
            continue
        if domain and "kfc.com.cn" not in domain and "yumchina.com" not in domain:
            continue
        by_name[cookie_name] = str(raw.get("value", ""))
    cookie = "; ".join(f"{key}={value}" for key, value in by_name.items())

    try:
        captured_at = datetime.fromisoformat(str(auth_state.get("createdAt", "")).replace("Z", "+00:00"))
    except Exception:
        captured_at = now()

    sessions = kfc_tickets.list_kfc_sessions()
    existing = next((item for item in sessions if item.get("phone") == phone), None)
    if not existing:
        malformed = [
            item
            for item in sessions
            if not _clean_auth_value(item.get("phone")) and not _clean_auth_value(item.get("userCode"))
        ]
        if len(malformed) == 1:
            existing = malformed[0]
    if existing:
        kfc_tickets.update_kfc_session(
            existing["id"],
            {"name": phone, "phone": phone, "userCode": user_code, "enabled": True},
        )
        logger.info("KFC session metadata repaired/reused phone=%s session_id=%s", phone, existing["id"])

    saved = kfc_tickets.upsert_auth_state(
        name=phone,
        ticket=ticket,
        user_code=user_code,
        phone=phone,
        open_id=open_id,
        device_id=device_id,
        cookie=cookie,
        auth_state=auth_state,
        captured_at=captured_at,
        remark=remark,
    )
    if kbck:
        kfc_tickets.save_ticket_kbck(saved["id"], kbck)
    kfc_sessions.save_kfc_session(phone=phone, session_id=session_id)
    logger.info("KFC auth imported phone=%s ticket_id=%s cookie=%s", phone, saved.get("id"), cookie)
    return saved


def open_kfc_ticket_browser(ticket_id: str) -> dict[str, str]:
    auth = kfc_tickets.get_enabled_kfc_session(ticket_id)
    if not auth:
        raise not_found("KFC Ticket 不存在或已停用")
    if not isinstance(auth.get("auth_state"), dict) or not auth.get("auth_state"):
        raise bad_request("当前 KFC Ticket 没有完整登录状态，请先重新登录采集")
    try:
        result = open_authenticated_chrome(dict(auth))
    except RuntimeError as exc:
        raise bad_request(str(exc)) from exc
    logger.info(
        "KFC authenticated Chrome opened ticket_id=%s phone=%s profile=%s",
        ticket_id,
        auth.get("phone"),
        result.get("profileDir"),
    )
    return result


def get_enabled_auth_for_account(account_id: str) -> dict[str, Any]:
    identifier = platforms.get_enabled_kfc_account_identifier(account_id)
    if not identifier:
        raise not_found("卡券所属 KFC 账号不存在或已停用")

    if identifier.startswith("kfc-phone:"):
        phone = identifier.removeprefix("kfc-phone:").strip()
    elif identifier.startswith("kfc-session:"):
        ticket_id = identifier.removeprefix("kfc-session:").strip()
        phone = kfc_tickets.get_phone_for_ticket_id(ticket_id)
    else:
        raise not_found("卡券所属账号没有绑定 KFC 手机号")

    if not phone:
        raise not_found("卡券所属账号没有绑定 KFC 手机号")
    auth = kfc_tickets.get_latest_enabled_auth_for_phone(phone)
    if not auth:
        raise not_found(f"手机号 {phone} 没有可用的 KFC 登录信息")
    return auth


def sync_kfc_coupons(ticket_id: str, batch_name: str | None = None) -> dict[str, Any]:
    logger.info("KFC coupon sync requested ticket_id=%s batch_name=%s", ticket_id, batch_name)
    selected = kfc_tickets.get_enabled_kfc_session(ticket_id)
    if not selected:
        logger.warning("KFC coupon sync failed ticket_id=%s reason=session_not_found_or_disabled", ticket_id)
        raise not_found("KFC Session 不存在或已停用")

    phone = _clean_auth_value(selected.get("phone"))
    if not phone:
        logger.warning("KFC coupon sync failed ticket_id=%s reason=missing_phone", ticket_id)
        raise bad_request("KFC Session 缺少手机号，无法关联登录信息")
    ticket = kfc_tickets.get_latest_enabled_auth_for_phone(phone)
    if not ticket:
        logger.warning("KFC coupon sync failed ticket_id=%s phone=%s reason=no_auth", ticket_id, phone)
        raise not_found(f"手机号 {phone} 没有可用的 KFC 登录信息")

    coupons = kfc_coupon_sync.list_coupons(
        ticket=ticket["ticket"],
        user_code=ticket["user_code"],
        cookie=ticket.get("cookie") or "",
    )
    result = kfc_tickets.save_coupon_sync(
        session_id=str(ticket["id"]),
        ticket=ticket,
        coupons=coupons,
        batch_name=batch_name,
        synced_at=now(),
    )
    logger.info("KFC coupon sync completed ticket_id=%s phone=%s coupon_count=%s result=%s", ticket_id, phone, len(coupons), result)
    return result
