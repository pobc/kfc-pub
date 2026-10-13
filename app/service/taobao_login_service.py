"""通过可见 Playwright 浏览器采集并持久化淘宝登录态。"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page

from app.config import settings
from app.database.price import taobao_session_db
from app.errors import bad_request, conflict, not_found, unavailable
from app.service.taobao_browser import playwright_runtime, taobao_browser_context
from app.utils import iso

logger = logging.getLogger("kfc.taobao_login")
_LOGIN_COOKIE_NAMES = {"unb", "lgc", "tracknick", "_nk_"}


@dataclass
class _CaptureCommand:
    action: str
    response: queue.Queue[tuple[bool, Any]]


@dataclass
class _CaptureTask:
    capture_id: str
    started_at: datetime
    expires_at: datetime
    commands: queue.Queue[_CaptureCommand] = field(default_factory=queue.Queue, repr=False)
    ready: threading.Event = field(default_factory=threading.Event, repr=False)
    closed: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    error: str = ""
    login_url: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_taobao_hostname(hostname: str) -> bool:
    normalized = hostname.lower().strip(".")
    return (
        normalized == "taobao.com"
        or normalized.endswith(".taobao.com")
        or normalized == "tmall.com"
        or normalized.endswith(".tmall.com")
    )


def _session_storage(context: BrowserContext) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for page in context.pages:
        try:
            origin_parts = urlsplit(page.url)
            if origin_parts.scheme not in {"http", "https"} or not origin_parts.netloc:
                continue
            if not _is_taobao_hostname(origin_parts.hostname or ""):
                continue
            origin = f"{origin_parts.scheme}://{origin_parts.netloc}"
            values = page.evaluate(
                """() => Object.keys(sessionStorage).map(name => ({ name, value: sessionStorage.getItem(name) || '' }))"""
            )
            if isinstance(values, list):
                result[origin] = values
        except Exception:
            logger.debug("读取淘宝页面 sessionStorage 失败 url=%s", page.url, exc_info=True)
    return result


def _taobao_page(context: BrowserContext) -> Page:
    candidates = [page for page in context.pages if _is_taobao_hostname(urlsplit(page.url).hostname or "")]
    if not candidates:
        raise RuntimeError("登录窗口当前没有淘宝页面，请重新打开登录窗口")
    return candidates[-1]


def _capture_snapshot(context: BrowserContext) -> dict[str, Any]:
    page = _taobao_page(context)
    state = context.storage_state()
    all_cookies = state.get("cookies") if isinstance(state.get("cookies"), list) else []
    cookies = [
        cookie
        for cookie in all_cookies
        if isinstance(cookie, dict) and _is_taobao_hostname(str(cookie.get("domain") or ""))
    ]
    taobao_cookie_names = {str(cookie.get("name") or "") for cookie in cookies if isinstance(cookie, dict)}
    if not (_LOGIN_COOKIE_NAMES & taobao_cookie_names):
        raise RuntimeError("尚未检测到有效淘宝登录信息，请在浏览器中完成登录后再保存")
    try:
        title = page.title()
    except Exception:
        title = ""
    return {
        "cookies": cookies,
        "origins": [
            origin
            for origin in (state.get("origins") if isinstance(state.get("origins"), list) else [])
            if isinstance(origin, dict)
            and _is_taobao_hostname(urlsplit(str(origin.get("origin") or "")).hostname or "")
        ],
        "sessionStorage": _session_storage(context),
        "url": page.url,
        "title": title,
    }


class TaobaoLoginService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, _CaptureTask] = {}

    def _cleanup(self) -> None:
        now = _utc_now()
        expired: list[_CaptureTask] = []
        with self._lock:
            for capture_id, task in list(self._tasks.items()):
                if task.closed.is_set():
                    self._tasks.pop(capture_id, None)
                elif task.expires_at <= now:
                    expired.append(task)
                    self._tasks.pop(capture_id, None)
        for task in expired:
            self._send_close(task)
            logger.warning("淘宝登录窗口已过期 captureId=%s", task.capture_id)

    @staticmethod
    def _view(task: _CaptureTask) -> dict[str, Any]:
        return {
            "captureId": task.capture_id,
            "active": not task.closed.is_set(),
            "loginUrl": task.login_url,
            "startedAt": iso(task.started_at),
            "expiresAt": iso(task.expires_at),
        }

    def active_capture(self) -> dict[str, Any] | None:
        self._cleanup()
        with self._lock:
            task = next((item for item in self._tasks.values() if not item.closed.is_set()), None)
            return self._view(task) if task else None

    def status(self) -> dict[str, Any]:
        result = taobao_session_db.session_status()
        active = self.active_capture()
        result.update(
            {
                "active": bool(active),
                "captureId": active.get("captureId") if active else None,
                "startedAt": active.get("startedAt") if active else None,
                "expiresAt": active.get("expiresAt") if active else None,
            }
        )
        return result

    def start(self) -> dict[str, Any]:
        active = self.active_capture()
        if active:
            return active
        now = _utc_now()
        ttl = max(60, settings().taobao_login_capture_ttl_seconds)
        task = _CaptureTask(
            capture_id=uuid.uuid4().hex,
            started_at=now,
            expires_at=datetime.fromtimestamp(now.timestamp() + ttl, timezone.utc),
        )
        task.thread = threading.Thread(
            target=self._browser_worker,
            args=(task,),
            name=f"taobao-login-{task.capture_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._tasks[task.capture_id] = task
        task.thread.start()
        if not task.ready.wait(timeout=30):
            self.close(task.capture_id)
            raise unavailable("淘宝登录浏览器启动超时，请检查浏览器配置")
        if task.error:
            with self._lock:
                self._tasks.pop(task.capture_id, None)
            self._wait_closed(task)
            raise unavailable(task.error)
        logger.info("淘宝登录窗口已打开 captureId=%s", task.capture_id)
        return self._view(task)

    def _browser_worker(self, task: _CaptureTask) -> None:
        try:
            with playwright_runtime() as playwright:
                with taobao_browser_context(
                    playwright,
                    task_name=f"login-{task.capture_id}",
                    headless=False,
                ) as context:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.goto(
                        settings().taobao_search_url,
                        wait_until="domcontentloaded",
                        timeout=settings().taobao_page_timeout_ms,
                    )
                    page.bring_to_front()
                    task.login_url = page.url
                    task.ready.set()
                    self._serve_commands(task, context)
        except Exception as exc:
            if isinstance(exc, NotImplementedError):
                task.error = "淘宝登录浏览器启动失败：当前服务事件循环不支持创建浏览器子进程"
            else:
                task.error = f"淘宝登录浏览器启动失败（{type(exc).__name__}），请查看服务日志中的原始异常"
            logger.exception("淘宝登录浏览器异常 captureId=%s", task.capture_id)
            task.ready.set()
        finally:
            task.closed.set()

    def _serve_commands(self, task: _CaptureTask, context: BrowserContext) -> None:
        while _utc_now() < task.expires_at:
            try:
                command = task.commands.get(timeout=0.5)
            except queue.Empty:
                continue
            if command.action == "cancel":
                command.response.put((True, {"closed": True}))
                return
            if command.action != "save":
                command.response.put((False, "不支持的登录窗口操作"))
                continue
            try:
                snapshot = _capture_snapshot(context)
                command.response.put((True, snapshot))
                return
            except Exception as exc:
                message = str(exc).strip() or "淘宝登录信息读取失败"
                logger.warning("淘宝登录信息暂未保存 captureId=%s reason=%s", task.capture_id, message)
                command.response.put((False, message))
        logger.warning("淘宝登录窗口等待超时 captureId=%s", task.capture_id)

    def save(self, capture_id: str) -> dict[str, Any]:
        task = self._task(capture_id)
        response: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        task.commands.put(_CaptureCommand("save", response))
        try:
            succeeded, payload = response.get(timeout=30)
        except queue.Empty as exc:
            raise unavailable("读取淘宝登录信息超时，请重试") from exc
        if not succeeded:
            raise bad_request(str(payload))
        captured_at = _utc_now()
        result = taobao_session_db.save_session(payload, captured_at)
        self._wait_closed(task)
        with self._lock:
            self._tasks.pop(capture_id, None)
        logger.info(
            "淘宝登录信息已保存 captureId=%s cookieCount=%s originCount=%s sessionStorageCount=%s",
            capture_id,
            result.get("cookieCount"),
            result.get("originCount"),
            result.get("sessionStorageCount"),
        )
        result.update({"active": False, "captureId": None, "startedAt": None, "expiresAt": None})
        return result

    def _task(self, capture_id: str) -> _CaptureTask:
        self._cleanup()
        with self._lock:
            task = self._tasks.get(capture_id)
        if not task or task.closed.is_set():
            raise not_found("淘宝登录窗口不存在、已关闭或已经过期")
        return task

    @staticmethod
    def _send_close(task: _CaptureTask) -> None:
        if task.closed.is_set():
            return
        response: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        task.commands.put(_CaptureCommand("cancel", response))
        try:
            response.get(timeout=5)
        except queue.Empty:
            logger.warning("淘宝登录浏览器未及时响应关闭 captureId=%s", task.capture_id)

    @staticmethod
    def _wait_closed(task: _CaptureTask) -> None:
        if not task.closed.wait(timeout=10):
            logger.warning("淘宝登录浏览器线程未及时退出 captureId=%s", task.capture_id)
            return
        if task.thread and task.thread.is_alive():
            task.thread.join(timeout=1)

    def close(self, capture_id: str) -> dict[str, bool]:
        task = self._task(capture_id)
        self._send_close(task)
        self._wait_closed(task)
        with self._lock:
            self._tasks.pop(capture_id, None)
        logger.info("淘宝登录窗口已关闭 captureId=%s", capture_id)
        return {"closed": True}

    def ensure_no_active_capture(self) -> None:
        if self.active_capture():
            raise conflict("请先保存或取消当前淘宝登录窗口，再开始抓取")

    def close_all(self) -> None:
        with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            self._send_close(task)
            self._wait_closed(task)


taobao_login_service = TaobaoLoginService()
