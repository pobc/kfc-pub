from __future__ import annotations

import ipaddress

from fastapi import Request

_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def normalize_client_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_local_or_private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_loopback or address.is_private or address.is_link_local


def is_localhost_request(request: Request) -> bool:
    hostname = (request.url.hostname or "").lower()
    return hostname in _LOCAL_HOSTNAMES


def client_ip_from_request(request: Request) -> str:
    """Resolve client IP while trusting forwarding headers only from local/private proxies."""
    peer_text = request.client.host if request.client else ""
    peer = normalize_client_ip(peer_text)
    if peer is None:
        return "unknown"

    if is_localhost_request(request):
        return str(peer)

    if is_local_or_private(peer):
        cf_ip = normalize_client_ip(request.headers.get("cf-connecting-ip", ""))
        if cf_ip is not None and not is_local_or_private(cf_ip):
            return str(cf_ip)

        forwarded_for = request.headers.get("x-forwarded-for", "")
        for raw in forwarded_for.split(","):
            candidate = normalize_client_ip(raw)
            if candidate is not None and not is_local_or_private(candidate):
                return str(candidate)

        real_ip = normalize_client_ip(request.headers.get("x-real-ip", ""))
        if real_ip is not None and not is_local_or_private(real_ip):
            return str(real_ip)

    return str(peer)
