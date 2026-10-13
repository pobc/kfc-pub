"""Browser storage restoration shared with the database Chrome example."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.service.kfc.session_capture import BASE_URL, HOME_URL, CdpClient, CaptureService


def _storage(auth_state: dict[str, Any], key: str) -> dict[str, str]:
    raw = auth_state.get(key)
    if not isinstance(raw, dict):
        raw_capture = auth_state.get("rawCapture")
        page = raw_capture.get("page") if isinstance(raw_capture, dict) else None
        raw = page.get(key) if isinstance(page, dict) else None
    if not isinstance(raw, dict):
        return {}

    result: dict[str, str] = {}
    for name, value in raw.items():
        if value is None:
            continue
        if isinstance(value, str):
            result[str(name)] = value
        else:
            result[str(name)] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return result


def _cookies(auth: dict[str, Any], auth_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = auth_state.get("cookies")
    if not isinstance(raw, list):
        raw_capture = auth_state.get("rawCapture")
        raw = raw_capture.get("cookies") if isinstance(raw_capture, dict) else None

    cookies = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if cookies:
        return cookies

    result: list[dict[str, Any]] = []
    for part in str(auth.get("cookie") or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            result.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".kfc.com.cn",
                    "path": "/",
                    "secure": True,
                }
            )
    return result


def _cookie_params(cookie: dict[str, Any]) -> dict[str, Any] | None:
    name = str(cookie.get("name") or "").strip()
    value = str(cookie.get("value") or "")
    if not name:
        return None

    params: dict[str, Any] = {"name": name, "value": value}
    domain = str(cookie.get("domain") or "").strip()
    if domain:
        params["domain"] = domain
        params["path"] = str(cookie.get("path") or "/")
    else:
        params["url"] = HOME_URL

    for key in ("secure", "httpOnly", "sameParty"):
        if key in cookie:
            params[key] = bool(cookie[key])

    same_site = str(cookie.get("sameSite") or "").strip().capitalize()
    if same_site in {"Strict", "Lax", "None"}:
        params["sameSite"] = same_site

    priority = str(cookie.get("priority") or "").strip().capitalize()
    if priority in {"Low", "Medium", "High"}:
        params["priority"] = priority

    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and expires > time.time():
        params["expires"] = expires
    return params


def _restore_script(local_storage: dict[str, str], session_storage: dict[str, str]) -> str:
    local_json = json.dumps(local_storage, ensure_ascii=False)
    session_json = json.dumps(session_storage, ensure_ascii=False)
    base_json = json.dumps(BASE_URL)
    return f"""(() => {{
  if (location.origin !== {base_json}) return;
  const restore = (store, values) => {{
    for (const [key, value] of Object.entries(values)) store.setItem(key, value);
  }};
  restore(localStorage, {local_json});
  restore(sessionStorage, {session_json});
}})();"""


def _restore_url(auth: dict[str, Any], auth_state: dict[str, Any]) -> str:
    current_page = auth_state.get("currentPage")
    href = str(current_page.get("href") or "") if isinstance(current_page, dict) else ""
    if not href:
        raw_capture = auth_state.get("rawCapture")
        page = raw_capture.get("page") if isinstance(raw_capture, dict) else None
        href = str(page.get("href") or "") if isinstance(page, dict) else ""

    parsed = urlsplit(href)
    query = parse_qs(parsed.query)
    ticket = str(auth.get("ticket") or "")
    if (
        parsed.scheme == "https"
        and parsed.hostname == "order.kfc.com.cn"
        and parsed.path == "/preorder-taro/home"
        and query.get("forceLogin") == ["1"]
        and ticket
        and query.get("token") == [ticket]
        and bool((query.get("userCode") or [""])[0])
    ):
        return href

    user_code = str(auth.get("user_code") or "")
    if ticket and user_code:
        query = urlencode(
            {
                "forceLogin": "1",
                "token": ticket,
                "userCode": f"000{user_code}0000",
            }
        )
        return f"{HOME_URL}?{query}"
    return HOME_URL


def _wait_for_page(client: CdpClient, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ready = client.eval_json("document.readyState")
            href = str(client.eval_json("location.href") or "")
            if ready in {"interactive", "complete"} and "order.kfc.com.cn" in href:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("KFC 页面加载超时")


def _wait_for_authenticated_page(client: CdpClient, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = client.eval_json(
                """(() => ({
  hasUser: Boolean(localStorage.getItem("KEY_USER")),
  loginRequired: /欢迎您，请登录|手机号登录|验证码登录|登录即同意|请先登录/.test(
    document.body ? document.body.innerText : ""
  )
}))()"""
            )
            if isinstance(state, dict) and state.get("hasUser") and not state.get("loginRequired"):
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("数据库登录信息未被 KFC 页面接受，可能已失效，请重新采集")


def _first_page_ws_url(service: CaptureService, port: int) -> str:
    deadline = time.time() + 10
    while time.time() < deadline:
        response = service.http.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
        response.raise_for_status()
        pages = response.json()
        for page in pages if isinstance(pages, list) else []:
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                return str(page["webSocketDebuggerUrl"])
        time.sleep(0.25)
    raise RuntimeError("没有找到 Chrome 页面")


def open_authenticated_chrome(
    auth: dict[str, Any],
    profile_dir: Path | None = None,
) -> dict[str, str]:
    auth_state = auth.get("auth_state")
    if not isinstance(auth_state, dict):
        auth_state = {}

    phone = str(auth.get("phone") or "").strip()
    local_storage = _storage(auth_state, "localStorage")
    session_storage = _storage(auth_state, "sessionStorage")
    cookies = _cookies(auth, auth_state)
    restore_url = _restore_url(auth, auth_state)
    if not local_storage and not session_storage and not cookies:
        raise RuntimeError("当前 KFC Ticket 没有可恢复的浏览器登录状态")

    service = CaptureService()
    chrome = service._resolve_chrome()
    port = service._find_free_port()
    project_root = Path(__file__).resolve().parents[3]
    profile = profile_dir or (
        project_root
        / ".kfc"
        / "browser-injected"
        / f"{phone or 'ticket'}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    profile = profile.resolve()
    profile.mkdir(parents=True, exist_ok=False)
    profile_settings = profile / "Default"
    profile_settings.mkdir()
    (profile_settings / "Preferences").write_text(
        json.dumps({"profile": {"default_content_setting_values": {"geolocation": 2}}}),
        encoding="utf-8",
    )

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--new-window",
        "--window-size=430,920",
        "about:blank",
    ]
    process_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        process_kwargs["start_new_session"] = True

    process = subprocess.Popen(args, **process_kwargs)
    client: CdpClient | None = None
    preload_id = ""
    try:
        service._wait_for_cdp(port)
        client = CdpClient(_first_page_ws_url(service, port))
        client.call("Network.enable")

        restored_cookies = 0
        for cookie in cookies:
            params = _cookie_params(cookie)
            if not params:
                continue
            result = client.call("Network.setCookie", params)
            if result.get("success", True):
                restored_cookies += 1

        script = _restore_script(local_storage, session_storage)
        preload_result = client.call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        preload_id = str(preload_result.get("identifier") or "")
        client.call("Page.navigate", {"url": restore_url})
        _wait_for_page(client)
        _wait_for_authenticated_page(client)
        client.eval_json('history.replaceState(null, "", location.pathname + location.hash)')

        if not local_storage and not session_storage and restored_cookies == 0:
            raise RuntimeError("Chrome 未能恢复任何 KFC 登录信息")
    except Exception:
        service._terminate_pid(process.pid)
        shutil.rmtree(profile, ignore_errors=True)
        raise
    finally:
        if client:
            if preload_id:
                try:
                    client.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": preload_id})
                except Exception:
                    pass
            client.close()
        service.http.close()

    return {
        "phone": phone,
        "profileDir": str(profile),
        "url": restore_url,
    }
