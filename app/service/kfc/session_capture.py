from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import websocket

from app.config import settings
from app.errors import not_found, unavailable

BASE_URL = "https://order.kfc.com.cn"
HOME_URL = f"{BASE_URL}/preorder-taro/home"
logger = logging.getLogger("uvicorn.error")

_INVALID_AUTH_VALUES = {"", "[]", "{}", "null", "none", "undefined", "[object object]"}


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


def _clean_auth_value(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _INVALID_AUTH_VALUES else text


def _find_kbck(value: Any, depth: int = 0) -> str:
    if depth > 10:
        return ""
    current = _decode_storage_value(value)
    if isinstance(current, dict):
        latest = _clean_auth_value(current.get("kbck"))
        for child in current.values():
            found = _find_kbck(child, depth + 1)
            if found:
                latest = found
        return latest
    if isinstance(current, list):
        latest = ""
        for child in current:
            found = _find_kbck(child, depth + 1)
            if found:
                latest = found
        return latest
    return ""


def extract_logger_plain_kbck(value: Any) -> str:
    decoded = _decode_storage_value(value)
    if not isinstance(decoded, str):
        return _find_kbck(decoded)

    latest = ""
    for raw_line in decoded.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        found = _find_kbck(entry)
        if found:
            latest = found
    return latest


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


@dataclass
class CaptureTask:
    id: str
    port: int
    pid: int
    profile_dir: str
    login_url: str
    started_at: float
    expires_at: float
    kbck: str = ""
    kbck_updated_at: float | None = None
    kbck_stop: threading.Event = field(default_factory=threading.Event, repr=False)


class CdpClient:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=10)
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        call_id = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        while True:
            payload = json.loads(self.ws.recv())
            if payload.get("id") != call_id:
                continue
            if payload.get("error"):
                raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
            return payload.get("result") or {}

    def eval_json(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        self.ws.close()


class CaptureService:
    def __init__(self) -> None:
        self.tasks: dict[str, CaptureTask] = {}
        self.http = requests.Session()
        self._timeout = 5

    def start(self) -> dict[str, Any]:
        self._cleanup_expired()
        capture_id = str(uuid.uuid4())
        chrome = self._resolve_chrome()
        port = self._find_free_port()
        root = Path(os.getenv("KFC_CHROME_PROFILE_DIR", ".kfc/browser-capture")).resolve()
        profile = root / capture_id
        profile.mkdir(parents=True, exist_ok=True)
        login_url = os.getenv("KFC_LOGIN_URL", "").strip() or HOME_URL
        args = [
            chrome,
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--deny-permission-prompts",
            "--new-window",
            "--window-size=430,920",
            login_url,
        ]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(args, **kwargs)
        try:
            self._wait_for_cdp(port)
        except Exception:
            self._terminate_pid(process.pid)
            shutil.rmtree(profile, ignore_errors=True)
            raise
        started_at = time.time()
        task = CaptureTask(
            capture_id,
            port,
            process.pid,
            str(profile),
            login_url,
            started_at,
            started_at + settings().capture_task_ttl_seconds,
        )
        self.tasks[capture_id] = task
        logger.info(
            "KFC login capture started captureId=%s pid=%s port=%s",
            capture_id,
            process.pid,
            port,
        )
        threading.Thread(
            target=self._poll_kbck,
            args=(task,),
            name=f"kfc-kbck-{capture_id[:8]}",
            daemon=True,
        ).start()
        return {
            "captureId": capture_id,
            "loginUrl": login_url,
            "startedAt": self._iso(task.started_at),
            "expiresAt": self._iso(task.expires_at),
        }

    def capture(self, capture_id: str) -> dict[str, Any]:
        self._cleanup_expired()
        task = self.tasks.get(capture_id)
        if not task:
            raise not_found("KFC 登录窗口不存在、已过期或已经结束")
        if not self._cdp_available(task.port):
            self.close(capture_id)
            raise not_found("KFC 登录窗口已经关闭，请重新打开")
        logger.info("KFC login state capture started captureId=%s", capture_id)
        ws_url = self._page_ws_url(task.port)
        client = CdpClient(ws_url)
        try:
            page_snapshot = (
                client.eval_json(r"""(() => {
  const dump = (store) => {
    const out = {};
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      out[key] = store.getItem(key);
    }
    return out;
  };
  return {
    href: location.href,
    title: document.title,
    bodyText: document.body ? document.body.innerText : "",
    documentHtml: document.documentElement ? document.documentElement.outerHTML : "",
    documentCookie: document.cookie,
    localStorage: dump(localStorage),
    sessionStorage: dump(sessionStorage),
    stateRaw: sessionStorage.getItem("__STATE") || localStorage.getItem("__STATE") || ""
  };
})()""")
                or {}
            )
            client.call("Network.enable")
            try:
                cookies = client.call("Network.getAllCookies").get("cookies") or []
            except Exception:
                cookies = client.call("Storage.getCookies").get("cookies") or []

            raw_capture = {
                "page": page_snapshot,
                "cookies": cookies,
            }
            state_json = self._parse_json_maybe(page_snapshot.get("stateRaw") or "")
            candidates = self._find_candidates(
                {
                    "localStorage": page_snapshot.get("localStorage") or {},
                    "sessionStorage": page_snapshot.get("sessionStorage") or {},
                    "stateRaw": page_snapshot.get("stateRaw") or "",
                    "cookies": {str(item.get("name", "")): item.get("value") for item in cookies},
                }
            )
            summary = {
                "ticket": self._first(candidates, "ticket", "token"),
                "userCode": self._first(candidates, "userCode", "user_code"),
                "payUserId": self._first(candidates, "payUserId"),
                "phone": self._first(candidates, "phone"),
                "openid": self._first(candidates, "openid", "openId"),
            }
            logger.info(
                "KFC login state capture completed captureId=%s localStorageKeys=%s cookieCount=%s",
                capture_id,
                len(page_snapshot.get("localStorage") or {}),
                len(cookies),
            )
            return {
                "createdAt": self._iso(time.time()),
                "baseUrl": BASE_URL,
                "profileDir": task.profile_dir,
                "rawCapture": raw_capture,
                "currentPage": {
                    "href": page_snapshot.get("href", ""),
                    "title": page_snapshot.get("title", ""),
                    "bodyText": page_snapshot.get("bodyText", ""),
                    "documentHtml": page_snapshot.get("documentHtml", ""),
                },
                "documentCookie": page_snapshot.get("documentCookie", ""),
                "cookies": cookies,
                "localStorage": page_snapshot.get("localStorage") or {},
                "sessionStorage": page_snapshot.get("sessionStorage") or {},
                "stateRaw": page_snapshot.get("stateRaw") or "",
                "stateJson": state_json,
                "candidates": candidates,
                "summary": summary,
                "paymentPwd": None,
            }
        finally:
            client.close()

    def get_kbck(self, capture_id: str) -> str:
        task = self.tasks.get(capture_id)
        if not task:
            return ""
        return task.kbck

    def close(self, capture_id: str) -> dict[str, bool]:
        task = self.tasks.pop(capture_id, None)
        if not task:
            return {"closed": True}
        task.kbck_stop.set()
        try:
            version = self._json(task.port, "/json/version")
            ws_url = str(version.get("webSocketDebuggerUrl") or "")
            if ws_url:
                client = CdpClient(ws_url)
                try:
                    client.call("Browser.close")
                finally:
                    client.close()
        except Exception:
            pass
        self._terminate_pid(task.pid)
        shutil.rmtree(task.profile_dir, ignore_errors=True)
        logger.info(
            "KFC login capture closed captureId=%s kbckCaptured=%s",
            capture_id,
            bool(task.kbck),
        )
        return {"closed": True}

    def close_all(self) -> None:
        for capture_id in list(self.tasks):
            self.close(capture_id)
        self.http.close()

    def _poll_kbck(self, task: CaptureTask) -> None:
        attempts = 0
        logger.info("KFC KBCK polling started captureId=%s interval=1s", task.id)
        while not task.kbck_stop.is_set() and time.time() < task.expires_at:
            attempts += 1
            try:
                logger_plain = self._read_logger_plain(task.port)
                kbck = extract_logger_plain_kbck(logger_plain)
                if kbck:
                    if kbck != task.kbck:
                        task.kbck = kbck
                        task.kbck_updated_at = time.time()
                        logger.info(
                            "KFC KBCK captured captureId=%s kbck=%s",
                            task.id,
                            _mask_secret(kbck),
                        )
                elif attempts == 1 or attempts % 10 == 0:
                    logger.info(
                        "KFC KBCK not found yet captureId=%s attempts=%s",
                        task.id,
                        attempts,
                    )
            except Exception as exc:
                if attempts == 1 or attempts % 10 == 0:
                    logger.warning(
                        "KFC KBCK read failed captureId=%s attempts=%s error=%s",
                        task.id,
                        attempts,
                        exc,
                    )
            if task.kbck_stop.wait(1.0):
                break
        logger.info(
            "KFC KBCK polling stopped captureId=%s captured=%s attempts=%s",
            task.id,
            bool(task.kbck),
            attempts,
        )

    def _read_logger_plain(self, port: int) -> Any:
        response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=self._timeout)
        response.raise_for_status()
        pages = response.json()
        ws_url = ""
        for page in pages if isinstance(pages, list) else []:
            if (
                page.get("type") == "page"
                and "order.kfc.com.cn" in str(page.get("url", ""))
                and page.get("webSocketDebuggerUrl")
            ):
                ws_url = str(page["webSocketDebuggerUrl"])
                break
        if not ws_url:
            raise RuntimeError("没有找到 KFC 页面")
        client = CdpClient(ws_url)
        try:
            return client.eval_json('localStorage.getItem("logger-plain") || ""')
        finally:
            client.close()

    def _cleanup_expired(self) -> None:
        now = time.time()
        for capture_id, task in list(self.tasks.items()):
            if task.expires_at <= now:
                self.close(capture_id)

    @staticmethod
    def _terminate_pid(pid: int) -> None:
        if pid <= 0:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass

    def _resolve_chrome(self) -> str:
        configured = os.getenv("KFC_CHROME_PATH", "").strip()
        if configured and Path(configured).exists():
            return configured
        system = platform.system().lower()
        candidates: list[str]
        if system == "windows":
            candidates = [
                str(Path(os.getenv("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
                str(Path(os.getenv("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe"),
                str(Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
            ]
        elif system == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
        else:
            candidates = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
            ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        path = shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")
        if path:
            return path
        raise unavailable("没有找到 Chrome，请安装 Chrome 或通过 KFC_CHROME_PATH 指定可执行文件")

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _json(self, port: int, path: str) -> dict[str, Any]:
        response = self.http.get(f"http://127.0.0.1:{port}{path}", timeout=self._timeout)
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}

    def _cdp_available(self, port: int) -> bool:
        try:
            self._json(port, "/json/version")
            return True
        except Exception:
            return False

    def _wait_for_cdp(self, port: int) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._cdp_available(port):
                return
            time.sleep(0.3)
        raise unavailable("Chrome 启动超时，请确认浏览器可以正常启动")

    def _page_ws_url(self, port: int) -> str:
        deadline = time.time() + 10
        while time.time() < deadline:
            response = self.http.get(f"http://127.0.0.1:{port}/json/list", timeout=self._timeout)
            pages = response.json()
            for page in pages if isinstance(pages, list) else []:
                if (
                    page.get("type") == "page"
                    and "order.kfc.com.cn" in str(page.get("url", ""))
                    and page.get("webSocketDebuggerUrl")
                ):
                    return str(page["webSocketDebuggerUrl"])
            time.sleep(0.3)
        raise not_found("没有找到 KFC 登录页面，请重新打开登录窗口")

    @staticmethod
    def _parse_json_maybe(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text or text[0] not in "{[":
            return value
        try:
            return json.loads(text)
        except Exception:
            return value

    @staticmethod
    def _candidate_text(value: Any) -> str:
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return ""
        text = str(value).strip()
        if text.lower() in _INVALID_AUTH_VALUES:
            return ""
        return text

    def _find_candidates(self, value: Any) -> dict[str, list[dict[str, str]]]:
        names = ["ticket", "token", "userCode", "user_code", "payUserId", "phone", "openid", "openId"]
        found = {name: [] for name in names}

        def walk(current: Any, path: str = "") -> None:
            current = self._parse_json_maybe(current)
            if isinstance(current, list):
                for index, child in enumerate(current):
                    walk(child, f"{path}[{index}]")
                return
            if not isinstance(current, dict):
                return
            for key, child in current.items():
                child_path = f"{path}.{key}" if path else key
                candidate_text = self._candidate_text(child)
                for target in names:
                    if key.lower() == target.lower() and candidate_text:
                        found[target].append({"path": child_path, "value": candidate_text})
                walk(child, child_path)

        walk(value)
        return found

    @staticmethod
    def _first(candidates: dict[str, list[dict[str, str]]], *names: str) -> str:
        for name in names:
            values = candidates.get(name) or []
            for item in values:
                text = CaptureService._candidate_text(item.get("value")) if isinstance(item, dict) else ""
                if text:
                    return text
        return ""

    @staticmethod
    def _iso(timestamp: float) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


capture_service = CaptureService()
