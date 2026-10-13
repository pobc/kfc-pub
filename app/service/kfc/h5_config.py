from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.config import settings
from app.errors import unavailable

logger = logging.getLogger(__name__)
FetchText = Callable[[str], tuple[str, str]]


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class H5Config:
    build_version: str
    api_fversion: str
    kbs_key: str
    kbs_secret: str
    route_cell: str
    expires_at: float


class H5ConfigLoader:
    def __init__(self) -> None:
        self.cached: H5Config | None = None

    def get(self, fetch_text: FetchText, home_path: str) -> H5Config:
        if self.cached and self.cached.expires_at > time.time() * 1000:
            logger.debug(
                "KFC H5 config cache hit build_version=%s api_fversion=%s route_cell=%s expires_at=%s",
                self.cached.build_version,
                self.cached.api_fversion,
                self.cached.route_cell,
                self.cached.expires_at,
            )
            return self.cached
        logger.info("KFC H5 config refresh started home_path=%s", home_path)
        home_body, set_cookie = fetch_text(home_path)
        app_path = self.extract_app_path(home_body)
        logger.info("KFC H5 app script resolved app_path=%s", app_path)
        app_body, _ = fetch_text(app_path)
        kbs_info = self.decode_obfuscated_config(app_body, "kbsInfo")
        api_versions = self.decode_obfuscated_config(app_body, "apiFVersion")
        kbs_web = self.find_pair_object(kbs_info, "kfc.WEB")
        dynamic_versions = (
            dict(pair for pair in api_versions if isinstance(pair, list) and len(pair) >= 2)
            if isinstance(api_versions, list)
            else {}
        )
        build_version = _env("KFC_CLIENT_VERSION") or self.extract_build_version(home_body)
        api_fversion = _env("KFC_FVERSION") or _text(dynamic_versions.get("kfc"))
        kbs_key = _env("KFC_KBS_KEY") or _text(kbs_web.get("client_key"))
        kbs_secret = _env("KFC_KBS_SECRET") or _text(kbs_web.get("client_sec"))
        if not all([build_version, api_fversion, kbs_key, kbs_secret]):
            logger.error("KFC H5 dynamic configuration incomplete")
            raise unavailable("肯德基 H5 动态签名配置提取失败")
        route_cell = self.extract_cookie(set_cookie, "route-cell") or _env("KFC_ROUTE_CELL") or "yumc4"
        self.cached = H5Config(
            build_version,
            api_fversion,
            kbs_key,
            kbs_secret,
            route_cell,
            time.time() * 1000 + settings().kfc_config_cache_ms,
        )
        logger.info("KFC H5 configuration refreshed")
        return self.cached

    @staticmethod
    def extract_app_path(html: str) -> str:
        matches = re.findall(r"""src=["']([^"']*/preorder-taro/js/app\.[^"']+\.js)["']""", html)
        fallback = re.findall(r"""src=["']([^"']*js/app\.[^"']+\.js)["']""", html)
        path = (matches or fallback or [""])[-1]
        if not path:
            raise unavailable("肯德基 App JS 地址提取失败")
        if path.startswith("http"):
            return path
        return path if path.startswith("/") else f"/preorder-taro/{path}"

    @staticmethod
    def extract_build_version(html: str) -> str:
        match = re.search(r"""["']?buildVersion["']?\s*:\s*["']([^"']+)["']""", html)
        return match.group(1) if match else ""

    @staticmethod
    def decode_obfuscated_config(text: str, key: str) -> Any:
        marker = f'"{key}":['
        marker_index = text.find(marker)
        if marker_index < 0:
            return None
        start = marker_index + len(marker) - 1
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                        if isinstance(value, list) and value and isinstance(value[0], str):
                            encoded = "".join(value).replace("$", "=")
                            return json.loads(base64.b64decode(encoded).decode())
                        return value
                    except Exception:
                        return None
        return None

    @staticmethod
    def find_pair_object(value: Any, key: str) -> dict[str, Any]:
        if not isinstance(value, list):
            return {}
        for pair in value:
            if isinstance(pair, list) and len(pair) >= 2 and pair[0] == key:
                return _obj(pair[1])
        return {}

    @staticmethod
    def extract_cookie(header: str, name: str) -> str:
        match = re.search(rf"(?:^|[,;]\s*){re.escape(name)}=([^;,]+)", header)
        return match.group(1) if match else ""
