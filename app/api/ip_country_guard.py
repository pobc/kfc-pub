from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from ip_to_country import IpToCountry

from app.network import client_ip_from_request, is_local_or_private, is_localhost_request, normalize_client_ip

logger = logging.getLogger("kfc.ip-country-guard")

_ALLOWED_COUNTRY_CODE = "CN"
_LOOKUP = IpToCountry()


def country_code_for_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    result = _LOOKUP.ip_to_country(str(address))
    if not isinstance(result, dict):
        return None
    code = result.get("country_code")
    return str(code).upper() if code else None


def is_client_ip_allowed(value: str) -> tuple[bool, str | None]:
    address = normalize_client_ip(value)
    if address is None:
        return False, None

    if is_local_or_private(address):
        return True, "LOCAL"

    country_code = country_code_for_ip(address)
    return country_code == _ALLOWED_COUNTRY_CODE, country_code


async def enforce_china_ip_only(request: Request, call_next):
    if is_localhost_request(request):
        return await call_next(request)

    client_ip = client_ip_from_request(request)
    allowed, country_code = is_client_ip_allowed(client_ip)
    if not allowed:
        logger.warning(
            "blocked non-China request ip=%s country=%s path=%s",
            client_ip or "unknown",
            country_code or "unknown",
            request.url.path,
        )
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": {
                    "code": "FOREIGN_IP_FORBIDDEN",
                    "message": "当前服务仅允许中国 IP 访问",
                },
            },
        )
    return await call_next(request)
