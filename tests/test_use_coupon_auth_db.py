from __future__ import annotations

import hashlib
import secrets

from psycopg.types.json import Jsonb

from app.database import db, kfc_tickets
from app.service.kfc.contracts import CART_USE_COUPON_CODE_API
from app.service.kfc.h5_client import KfcH5Client
from app.service.kfc.h5_config import H5Config


def _cookie_pairs(value: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for part in value.split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator and name:
            pairs[name] = cookie_value
    return pairs


def test_use_coupon_request_uses_phone_scoped_database_auth() -> None:
    marker = secrets.token_hex(4)
    ticket = f"ticket-{marker}"
    user_code = f"user-{marker}"
    phone = f"138{int(marker, 16) % 100000000:08d}"
    device_id = f"device-{marker}"
    cookie = f"SID=sid-{marker}; auth=auth-{marker}"
    session_id = f"session-{marker}"
    coupon_code = f"COUPON-{marker}"

    db.execute(
        """INSERT INTO kfc_tickets
           (id,name,ticket,user_code,phone,open_id,device_id,cookie,auth_state,captured_at,enabled,last_synced_at,remark,"createdAt","updatedAt")
           VALUES (gen_random_uuid(),%s,%s,%s,%s,'',%s,%s,%s,now(),true,NULL,'pytest',now(),now() + interval '100 years')""",
        (
            phone,
            ticket,
            user_code,
            phone,
            device_id,
            cookie,
            Jsonb({"sessionId": session_id}),
        ),
    )

    client = KfcH5Client()
    client.config_loader.cached = H5Config(
        build_version="v6.999(pytest)",
        api_fversion="pytest-fversion",
        kbs_key="pytest-key",
        kbs_secret="pytest-secret",
        route_cell="pytest-route",
        expires_at=10**15,
    )
    try:
        auth = kfc_tickets.get_latest_enabled_auth_for_phone(phone)
        assert auth is not None
        assert auth["ticket"] == ticket
        assert auth["user_code"] == user_code
        assert auth["phone"] == phone
        assert auth["cookie"] == cookie
        assert auth["device_id"] == device_id

        context = {
            "config": client.load_config(),
            "device_id": auth["device_id"].strip(),
            "cookie": auth["cookie"].strip(),
            "phone": auth["phone"].strip(),
        }
        payload, headers = client.http.prepare_request(
            context,
            CART_USE_COUPON_CODE_API,
            {
                "sessionId": client._find_auth_value(auth.get("auth_state"), ["sessionId", "session_id"]),
                "ticket": auth["ticket"],
                "couponKey": hashlib.md5(coupon_code.encode()).hexdigest().upper(),
            },
            base_url=client.base_url,
            user_agent=client.user_agent,
            sign=True,
            user_code=auth["user_code"],
        )

        assert payload["sessionId"] == session_id
        assert payload["ticket"] == ticket
        assert payload["deviceId"] == device_id
        assert context["phone"] == phone
        assert headers["x-yumc-route-usercode"] == user_code
        stored_cookies = _cookie_pairs(cookie)
        request_cookies = _cookie_pairs(headers["cookie"])
        for name, value in stored_cookies.items():
            assert request_cookies[name] == value
        assert headers["x-yumc-client-deviceid"] == device_id
    finally:
        client.close()
