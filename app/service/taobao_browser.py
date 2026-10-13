"""淘宝 Playwright 浏览器的启动与认证状态恢复。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.request import urlopen

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from app.config import settings

logger = logging.getLogger("kfc.taobao_browser")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PLAYWRIGHT_POLICY_LOCK = threading.Lock()


@contextmanager
def playwright_runtime() -> Iterator[Playwright]:
    """确保 Playwright 在 Windows Selector 策略下仍使用可创建子进程的事件循环。"""
    if os.name != "nt":
        with sync_playwright() as playwright:
            yield playwright
        return

    manager = sync_playwright()
    with _PLAYWRIGHT_POLICY_LOCK:
        original_policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            playwright = manager.start()
        finally:
            asyncio.set_event_loop_policy(original_policy)

    try:
        yield playwright
    finally:
        # Playwright 关闭驱动时也会调度子进程，短暂切回 Proactor 后再恢复服务策略。
        with _PLAYWRIGHT_POLICY_LOCK:
            original_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            try:
                playwright.stop()
            finally:
                asyncio.set_event_loop_policy(original_policy)


def taobao_profile_root() -> Path:
    """返回淘宝浏览器临时资料目录的根路径。"""
    root = Path(settings().taobao_browser_profile_dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _taobao_chrome_path() -> str:
    configured = settings().taobao_browser_executable_path.strip()
    if configured:
        executable = Path(configured).expanduser().resolve()
        if not executable.is_file():
            raise RuntimeError(f"淘宝浏览器程序不存在：{executable}")
        return str(executable)

    system = platform.system().lower()
    if system == "windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    executable_path = str(executable) if executable else shutil.which("google-chrome") or shutil.which("chromium")
    if executable_path:
        return executable_path
    raise RuntimeError("没有找到本机 Chrome，请安装 Chrome 或配置 TAOBAO_BROWSER_EXECUTABLE_PATH")


def _find_free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_cdp(port: int, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"本机 Chrome 提前退出，退出码：{process.returncode}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("本机 Chrome 启动超时，请确认浏览器可以正常启动")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    except (OSError, subprocess.SubprocessError):
        logger.warning("终止淘宝本机浏览器失败 pid=%s", process.pid, exc_info=True)


def _remove_task_profile(profile: Path) -> None:
    """等待 Windows 释放 Chrome 文件句柄后清理本次任务资料。"""
    deadline = time.monotonic() + 5
    absent_since: float | None = None
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if profile.exists():
            absent_since = None
            try:
                shutil.rmtree(profile)
                last_error = None
            except OSError as exc:
                last_error = exc
        else:
            absent_since = absent_since or time.monotonic()
            # Chrome 子进程可能在主进程退出后短暂回写资料，确认目录持续消失再返回。
            if time.monotonic() - absent_since >= 0.5:
                return
        time.sleep(0.1)
    if profile.exists():
        logger.warning("清理淘宝浏览器资料目录失败 profile=%s error=%s", profile, last_error)


def _configure_task_network_permissions(profile: Path) -> None:
    """在 Chrome 首次启动前，屏蔽本任务浏览器的本机和局域网访问询问。"""
    # 兼容旧版合并权限与新版拆分权限；值 2 表示屏蔽，不会关闭浏览器安全检查。
    blocked_permissions = {
        "local_network_access": 2,
        "local_network": 2,
        "loopback_network": 2,
    }
    default_profile = profile / "Default"
    default_profile.mkdir()
    preferences = {"profile": {"default_content_setting_values": blocked_permissions}}
    # 这里只写本次任务刚创建的独立配置，不读取或覆盖用户日常 Chrome 的配置。
    (default_profile / "Preferences").write_text(json.dumps(preferences), encoding="utf-8")
    logger.info(
        "淘宝浏览器本机网络访问已屏蔽 operation=configure_local_network result=denied task=%s permissions=%s",
        profile.name,
        ",".join(blocked_permissions),
    )


@contextmanager
def taobao_browser_context(
    playwright: Playwright,
    *,
    task_name: str,
    headless: bool,
    show_window: bool = True,
    auth_state: dict[str, Any] | None = None,
) -> Iterator[BrowserContext]:
    """以独立本机 Chrome 进程运行一次淘宝任务，并通过 CDP 交给 Playwright。"""
    safe_task_name = "".join(character if character.isalnum() or character in "-_" else "-" for character in task_name)
    if not safe_task_name:
        raise ValueError("淘宝浏览器任务名称不能为空")

    profile = taobao_profile_root() / safe_task_name
    profile_created = False
    process: subprocess.Popen[Any] | None = None
    browser = None
    started = time.perf_counter()
    try:
        chrome = _taobao_chrome_path()
        port = _find_free_port()
        profile.mkdir(parents=True, exist_ok=False)
        profile_created = True
        _configure_task_network_permissions(profile)
        arguments = [
            chrome,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--lang=zh-CN",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "--window-size=1440,1000",
            "about:blank",
        ]
        if headless:
            arguments.insert(4, "--headless")
        elif not show_window:
            # 淘宝会拦截 headless；有头窗口保持活动但放到屏幕外，避免打扰用户桌面。
            arguments.insert(4, "--window-position=-32000,-32000")
        process_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            process_options["start_new_session"] = True

        process = subprocess.Popen(arguments, **process_options)
        logger.info(
            "淘宝本机浏览器已启动 task=%s pid=%s cdpPort=%s profile=%s executable=%s headless=%s showWindow=%s",
            safe_task_name,
            process.pid,
            port,
            profile,
            chrome,
            headless,
            show_window,
        )
        _wait_for_cdp(port, process)
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
        if not browser.contexts:
            raise RuntimeError("本机 Chrome 未提供可用的浏览器上下文")
        context = browser.contexts[0]
        context.set_default_timeout(15000)
        context.set_default_navigation_timeout(settings().taobao_page_timeout_ms)
        if auth_state:
            restore_taobao_session(context, auth_state)
        yield context
    except Exception:
        logger.exception("淘宝本机浏览器任务失败 task=%s", safe_task_name)
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                logger.debug("关闭淘宝 CDP 连接失败 task=%s", safe_task_name, exc_info=True)
        if process is not None:
            _terminate_process(process)
        if profile_created:
            _remove_task_profile(profile)
        logger.info(
            "淘宝本机浏览器已清理 task=%s duration=%.1fms",
            safe_task_name,
            (time.perf_counter() - started) * 1000,
        )


def restore_taobao_session(context: BrowserContext, auth_state: dict[str, Any]) -> None:
    cookies = auth_state.get("cookies") if isinstance(auth_state.get("cookies"), list) else []
    origins = auth_state.get("origins") if isinstance(auth_state.get("origins"), list) else []
    session_storage = auth_state.get("session_storage") if isinstance(auth_state.get("session_storage"), dict) else {}
    if cookies:
        context.add_cookies(cookies)

    storage_by_origin: dict[str, dict[str, dict[str, str]]] = {}
    for item in origins:
        if not isinstance(item, dict):
            continue
        origin = str(item.get("origin") or "").strip()
        values = item.get("localStorage")
        if not origin or not isinstance(values, list):
            continue
        storage_by_origin.setdefault(origin, {})["localStorage"] = {
            str(value.get("name")): str(value.get("value") or "")
            for value in values
            if isinstance(value, dict) and value.get("name") is not None
        }
    for origin, values in session_storage.items():
        if not isinstance(values, list):
            continue
        storage_by_origin.setdefault(str(origin), {})["sessionStorage"] = {
            str(value.get("name")): str(value.get("value") or "")
            for value in values
            if isinstance(value, dict) and value.get("name") is not None
        }

    if storage_by_origin:
        payload = json.dumps(storage_by_origin, ensure_ascii=False)
        context.add_init_script(
            script=f"""(() => {{
  const allState = {payload};
  const current = allState[location.origin];
  if (!current) return;
  for (const [name, value] of Object.entries(current.localStorage || {{}})) localStorage.setItem(name, value);
  for (const [name, value] of Object.entries(current.sessionStorage || {{}})) sessionStorage.setItem(name, value);
}})();"""
        )
    logger.info(
        "淘宝登录态已载入 cookieCount=%s originCount=%s sessionOriginCount=%s",
        len(cookies),
        len(origins),
        len(session_storage),
    )
