from starlette.requests import Request

from app.api.ip_country_guard import client_ip_from_request, is_client_ip_allowed, normalize_client_ip


def _request(
    peer_ip: str,
    headers: dict[str, str] | None = None,
    *,
    server_host: str = "testserver",
) -> Request:
    raw_headers = [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": raw_headers,
            "client": (peer_ip, 12345),
            "server": (server_host, 80),
        }
    )


def test_local_ip_is_allowed() -> None:
    assert is_client_ip_allowed("127.0.0.1") == (True, "LOCAL")
    assert is_client_ip_allowed("192.168.1.20") == (True, "LOCAL")


def test_china_public_ip_is_allowed_and_foreign_ip_is_blocked() -> None:
    china_allowed, china_code = is_client_ip_allowed("114.114.114.114")
    foreign_allowed, foreign_code = is_client_ip_allowed("8.8.8.8")

    assert china_allowed is True
    assert china_code == "CN"
    assert foreign_allowed is False
    assert foreign_code != "CN"


def test_trusted_local_proxy_uses_forwarded_public_ip() -> None:
    request = _request("127.0.0.1", {"x-forwarded-for": "8.8.8.8, 127.0.0.1"})
    assert client_ip_from_request(request) == "8.8.8.8"


def test_localhost_request_ignores_forwarded_ip() -> None:
    request = _request(
        "127.0.0.1",
        {"host": "localhost:3000", "x-forwarded-for": "8.8.8.8, 127.0.0.1"},
        server_host="localhost",
    )
    assert client_ip_from_request(request) == "127.0.0.1"


def test_public_peer_cannot_spoof_forwarded_ip() -> None:
    request = _request("8.8.8.8", {"x-forwarded-for": "114.114.114.114"})
    assert client_ip_from_request(request) == "8.8.8.8"


def test_ipv4_mapped_ipv6_is_normalized() -> None:
    assert str(normalize_client_ip("::ffff:114.114.114.114")) == "114.114.114.114"
