from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page, Response, Route, async_playwright

from app.service.kfc.browser_auth import _cookies, _cookie_params, _restore_script, _restore_url, _storage
from app.service.kfc.contracts import CART_UPDATE_API
from app.service.kfc.redemption_logging import log_redemption_failure
from app.service.kfc.redemption_response import matches_endpoint, require_available_coupon, coupon_cart_id
from app.service.kfc.session_capture import HOME_URL, CaptureService

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
BUNDLE_NAME = "1816.054ffbb3.js"
COUPON_TEXT = re.compile(r"""(?P<prefix>["']txt["']\s*:\s*Ze\s*\|\|\s*)["']立即使用["']""")
VISION_SCRIPT = Path(__file__).with_name("slider_vision.js").read_text(encoding="utf-8")
SLIDER_REQUIRED_CODE = "5910060"
CART_UPDATE_ENDPOINT = CART_UPDATE_API.rsplit("/", 1)[-1]


def patch_coupon_bundle(source: str) -> str:
    """Expose each card's existing digest, never substitute one digest on every card."""
    modified, count = COUPON_TEXT.subn(r'\g<prefix>"立即使用#"+d', source)
    if count != 1:
        raise RuntimeError("KFC 卡券脚本结构已变化，无法唯一替换立即使用按钮")
    return modified


def _option_name_variants(value: Any) -> set[str]:
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()
    without_quantity = re.sub(r"^\d+(?:份|杯|个|盒|桶|客|组|套)?", "", normalized)
    without_packaging = without_quantity.replace("块装)", "块)")
    stem = re.sub(r"\([^)]*\)$", "", without_packaging)
    return {name for name in (normalized, without_quantity, without_packaging, stem) if name}


T = TypeVar("T")


def run_browser_task(task: Coroutine[Any, Any, T]) -> T:
    # Uvicorn reload installs WindowsSelectorEventLoopPolicy. Playwright needs
    # subprocess transports, which only Proactor supports on Windows. Select a
    # loop for this worker thread without changing the server's global policy.
    factory = asyncio.ProactorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(task)


class RedemptionBrowser:
    """Drive one isolated Chrome through checkout, then release its resources."""

    def __init__(
        self, auth: dict[str, Any], *, stop: threading.Event | None = None, job_id: str = "", browser_pid: Any = None
    ) -> None:
        self.auth = auth
        self.job_id = job_id or "manual"
        self.started_at = time.monotonic()
        self.log_secrets = {
            str(auth.get(key) or "")
            for key in ("ticket", "cookie", "open_id", "device_id", "phone", "user_code", "kbck")
        }
        self.stop = stop or threading.Event()
        self.profile = ROOT / ".kfc" / "redemption-browser" / uuid.uuid4().hex
        self.stage = "starting"
        self.bundle_patched = False
        self.patch_failed = False
        self.playwright: Any = None
        self.browser: Any = None
        self.page: Page | None = None
        self.process: subprocess.Popen | None = None
        self.browser_pid = browser_pid
        self._use_response: Any = None

    async def __aenter__(self) -> RedemptionBrowser:
        # Isolate the Chrome process so the worker supervisor can stop it at
        # the task deadline, including during Playwright startup.
        service = CaptureService()
        try:
            self._step(
                "playwright-starting", "启动 Playwright driver loop=%s", type(asyncio.get_running_loop()).__name__
            )
            self._check_stop()
            self.playwright = await async_playwright().start()
            self._step("chrome-resolving", "查找 Chrome 可执行文件")
            chrome = service._resolve_chrome()
            port = service._find_free_port()
            self.profile.mkdir(parents=True, exist_ok=False)
            profile_settings = self.profile / "Default"
            profile_settings.mkdir()
            (profile_settings / "Preferences").write_text(
                json.dumps({"profile": {"default_content_setting_values": {"geolocation": 2}}}),
                encoding="utf-8",
            )
            self._step(
                "chrome-launching", "启动无头 Chrome executable=%s profile=%s cdp_port=%s", chrome, self.profile, port
            )
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                kwargs["start_new_session"] = True
            self.process = subprocess.Popen(
                [
                    chrome,
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    f"--user-data-dir={self.profile}",
                    "--headless",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    "--window-size=430,920",
                    "about:blank",
                ],
                **kwargs,
            )
            if self.browser_pid is not None:
                self.browser_pid.value = self.process.pid
            self._step("chrome-cdp-waiting", "Chrome 进程已创建 pid=%s，等待调试端口", self.process.pid)
            await asyncio.to_thread(service._wait_for_cdp, port)
            self._step("playwright-connecting", "调试端口就绪，连接 Playwright")
            self.browser = await self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
            self.context = self.browser.contexts[0]
            logger.info("[核销] job=%s stage=geolocation_denied 已关闭定位请求，使用指定城市选餐厅", self.job_id)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await self.page.set_viewport_size({"width": 430, "height": 820})
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(45000)
            # Fresh profile has no service-worker cache; intercept before any KFC navigation.
            await self.context.route(re.compile(r"/1816\.054ffbb3\.js(?:\?.*)?$"), self._patch_route)
            self._step("browser-open", "Chrome 已打开且 Playwright 已连接 pid=%s", self.process.pid)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        finally:
            service.http.close()

    async def __aexit__(self, *_: Any) -> None:
        try:
            if self.browser:
                async with asyncio.timeout(5):
                    await self.browser.close()
        finally:
            if self.playwright:
                async with asyncio.timeout(5):
                    await self.playwright.stop()
                self.playwright = None
            logger.info("[核销] job=%s stage=browser_closed 浏览器控制已关闭", self.job_id)

    def _step(self, stage: str, message: str, *args: Any) -> None:
        self.stage = stage
        logger.info(
            "[核销] job=%s stage=%s elapsed=%.1fs " + message,
            self.job_id,
            stage,
            time.monotonic() - self.started_at,
            *args,
        )

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise InterruptedError("Worker 停止，终止当前任务")

    async def _wait(self, milliseconds: float) -> None:
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            self._check_stop()
            await self.page.wait_for_timeout(min(100, max(1, (deadline - time.monotonic()) * 1000)))

    async def _patch_route(self, route: Route) -> None:
        if urlsplit(route.request.url).hostname != "order.kfc.com.cn":
            await route.continue_()
            return
        try:
            logger.info("[核销] job=%s stage=bundle_fetch 下载并改写资源 %s", self.job_id, BUNDLE_NAME)
            response = await route.fetch(timeout=30000)
            if response.status != 200:
                raise RuntimeError("KFC 卡券脚本下载失败")
            modified = patch_coupon_bundle(await response.text())
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-encoding", "etag"}
            }
            headers["content-type"] = "application/javascript; charset=utf-8"
            await route.fulfill(status=200, headers=headers, body=modified)
            self.bundle_patched = True
            logger.info("[核销] job=%s stage=bundle_patched 立即使用按钮 MD5 标记已注入", self.job_id)
        except Exception as exc:
            self.patch_failed = True
            log_redemption_failure(logger, self.job_id, "bundle_patch", exc, secrets=self.log_secrets)
            await route.abort()

    @asynccontextmanager
    async def _preload_script(self, source: str) -> AsyncIterator[None]:
        cdp = await self.context.new_cdp_session(self.page)
        try:
            # A separate CDP session must enable Page itself; otherwise Chrome
            # accepts addScriptToEvaluateOnNewDocument but never runs the script.
            await cdp.send("Page.enable")
            preload = await cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": source})
            try:
                yield
            finally:
                await cdp.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": preload["identifier"]})
        finally:
            await cdp.detach()

    async def restore_login(self) -> None:
        self._check_stop()
        auth_state = self.auth.get("auth_state")
        auth_state = auth_state if isinstance(auth_state, dict) else {}
        local = _storage(auth_state, "localStorage")
        session = _storage(auth_state, "sessionStorage")
        restore_url = _restore_url(self.auth, auth_state)
        if parse_qs(urlsplit(restore_url).query).get("forceLogin") == ["1"]:
            # Wait for the token login to create KEY_USER. Restoring the old key
            # makes the login-ready check pass before KFC has accepted the token.
            local.pop("KEY_USER", None)
        cookies = [params for item in _cookies(self.auth, auth_state) if (params := _cookie_params(item))]
        self._step(
            "restoring-login",
            "恢复登录状态 cookies=%s local_keys=%s session_keys=%s",
            len(cookies),
            len(local),
            len(session),
        )
        if not local and not session and not cookies:
            raise RuntimeError("卡券所属账号没有可恢复的浏览器登录信息")
        if cookies:
            await self.context.add_cookies(cookies)
        async with self._preload_script(_restore_script(local, session)):
            logger.info("[核销] job=%s stage=login_navigation 打开登录恢复页（凭据地址已隐藏）", self.job_id)
            await self.page.goto(restore_url, wait_until="domcontentloaded")
            await self.page.wait_for_function(
                """() => Boolean(localStorage.getItem('KEY_USER')) &&
                !/欢迎您，请登录|手机号登录|验证码登录|登录即同意|请先登录/.test(document.body?.innerText || '')""",
                timeout=30000,
            )
            await self.page.evaluate("history.replaceState(null, '', location.pathname + location.hash)")
            self._step("login-ready", "页面登录状态已恢复")

    async def open_menu(
        self,
        *,
        store_code: str,
        city_name: str,
        store_name: str,
        image_url: str,
        pickup_type: int,
    ) -> None:
        if not all(value.strip() for value in (store_code, city_name, store_name, image_url)):
            raise ValueError("餐厅城市、名称或图片缺失，请重新选择餐厅")
        await self.restore_login()
        await self.select_store_from_home(
            city_name=city_name,
            store_name=store_name,
            image_url=image_url,
        )
        self._step("pickup-waiting", "按任务取餐方式进入菜单 pickup_type=%s store=%s", pickup_type, store_code)
        # 门店确认按钮的处理函数返回后，取餐方式弹窗仍可能延迟挂载。
        await (
            self.page.locator('img[src*="icon-pickup.png"], img[src*="icon-store-dining.png"]')
            .or_(self.page.locator(".address-store-name-text"))
            .filter(visible=True)
            .first.wait_for(state="visible", timeout=15000)
        )
        await self.select_pickup_type(pickup_type, ensure_selected=True)
        await self.page.get_by_text("优惠助手", exact=True).filter(visible=True).first.wait_for(state="visible")
        self._step("menu-open", "门店选择完成，菜单已加载 store=%s", store_code)

    async def _fill_search(self, control: Locator, value: str) -> None:
        tag = await control.evaluate("e => e.tagName.toLowerCase()")
        field = (
            control if tag in {"input", "textarea"} else control.locator("input, textarea").filter(visible=True).first
        )
        await field.fill(value)
        await field.press("Enter")

    async def select_store_from_home(
        self,
        *,
        city_name: str,
        store_name: str,
        image_url: str,
    ) -> None:
        self._check_stop()
        self._step("home-opening", "从首页选择城市和餐厅 city=%s restaurant=%s", city_name, store_name)
        # Navigate to home instead of forging restaurant cache state or opening
        # the menu directly. KFC's own selection flow confirms the restaurant.
        await self.page.goto(HOME_URL, wait_until="domcontentloaded")
        await self._click_frontmost(self.page.locator(".storeInfoBlockTitleArrow").filter(visible=True))
        city = self.page.locator(".store_searchbar_city_txt").filter(visible=True).first
        await city.wait_for(state="visible")
        editable = await city.evaluate("e => e.matches('input,textarea') || !!e.querySelector('input,textarea')")
        if editable:
            await self._fill_search(city, city_name)
        else:
            await self._click_frontmost(city)
        sections = self.page.locator(".store_citysection").filter(visible=True)
        await sections.first.wait_for(state="visible")
        target = sections.get_by_text(city_name, exact=True).or_(
            sections.filter(has_text=re.compile(r"^\s*" + re.escape(city_name) + r"\s*$"))
        )
        await self._click_frontmost(target, scroll=True)
        await sections.first.wait_for(state="hidden")
        self._step("city-selected", "城市已选择 city=%s", city_name)
        # The map has a text search entrance; the real input lives on the
        # next page. Wait for the city page to leave before opening it.
        await self._click_frontmost(self.page.locator(".store_searchbar_text").filter(visible=True))
        search = self.page.locator(".store_searchbar_input").filter(visible=True).first
        await search.wait_for(state="visible")
        await self._fill_search(search, store_name)
        self._step("store-searching", "按餐厅名称搜索，并匹配已保存图片 restaurant=%s", store_name)
        await self._click_store_image(image_url)
        self._step("store-matched", "已按图片唯一匹配餐厅 restaurant=%s", store_name)
        await self._click_frontmost(self.page.locator(".store-btn.store-btn_confirm").filter(visible=True))
        await self._click_frontmost(self.page.locator(".buttonBlockInner").filter(visible=True))

    async def _click_store_image(self, image_url: str) -> None:
        parsed = urlsplit(image_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("保存的餐厅图片链接无效")
        expected = (parsed.netloc.lower(), parsed.path, parsed.query)
        deadline = time.monotonic() + 15
        images = self.page.locator("img").filter(visible=True)
        while time.monotonic() < deadline:
            self._check_stop()
            sources = await images.evaluate_all("es => es.map(e => e.src)")
            matches = [
                i
                for i, src in enumerate(sources)
                if (urlsplit(src).netloc.lower(), urlsplit(src).path, urlsplit(src).query) == expected
            ]
            if len(matches) > 1:
                raise RuntimeError("餐厅图片匹配到多个结果，无法唯一确认门店")
            if matches:
                # The photo is covered by the favourite/details action layer.
                # Select its own restaurant row through the name, where KFC's
                # itemPressed handler receives the click without opening details.
                row = images.nth(matches[0]).locator(
                    'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " store_item ")][1]'
                )
                await self._click_frontmost(row.locator(".store_item_right_row--names"), scroll=True)
                return
            await self._wait(200)
        raise RuntimeError("搜索结果中没有匹配已保存图片的餐厅")

    async def click_coupon(self, coupon_code: str) -> None:
        if not coupon_code:
            raise ValueError("分配卡券缺少 couponCode")
        self.log_secrets.add(coupon_code)
        self._check_stop()
        digest = hashlib.md5(coupon_code.encode("utf-8")).hexdigest()
        await self.open_coupon_assistant(coupon_code)
        self._step("coupon-assistant-open", "优惠助手已打开，查找已分配卡券 coupon_md5=%s", digest)
        target = self.page.locator(".coupon-item-button").filter(has_text=re.compile(r"#" + digest + r"\s*$", re.I))
        for _ in range(40):
            self._check_stop()
            if self.patch_failed:
                raise RuntimeError("KFC 卡券脚本替换失败")
            if await target.count():
                if not self.bundle_patched:
                    raise RuntimeError("未确认卡券脚本替换成功")
                self._step("coupon-click-started", "已找到目标卡券，准备点击匹配按钮 count=%s", await target.count())
                await self._click_frontmost(target, scroll=True)
                self._step("coupon-clicked", "目标卡券已点击，等待滑动验证")
                return
            # Allow lazy/virtual coupon lists to mount subsequent cards.
            await self.page.evaluate("""() => {
              const scrollers = new Set();
              for (const button of document.querySelectorAll('.coupon-item-button')) {
                for (let node = button.parentElement; node; node = node.parentElement) {
                  if (node.scrollHeight > node.clientHeight + 4 &&
                      /auto|scroll/.test(getComputedStyle(node).overflowY)) {
                    scrollers.add(node); break;
                  }
                }
              }
              for (const node of scrollers) node.scrollBy(0, node.clientHeight * 0.75);
            }""")
            await self._wait(500)
        logger.error(
            "[核销] job=%s stage=coupon_not_found bundle_patched=%s visible_buttons=%s",
            self.job_id,
            self.bundle_patched,
            await self.page.locator(".coupon-item-button:visible").count(),
        )
        raise RuntimeError("优惠助手中未找到分配卡券对应的 MD5 按钮")

    async def open_coupon_assistant(self, coupon_code: str) -> None:
        self._step("coupon-assistant-opening", "定位并点击当前页面的优惠助手")
        async with self.page.expect_response(
            lambda response: matches_endpoint(response.url, {"getCoupon"}), timeout=20000
        ) as observed:
            await self._click_frontmost(self.page.get_by_text("优惠助手", exact=True).filter(visible=True))
        response = await observed.value
        if not response.ok:
            raise RuntimeError("KFC getCoupon HTTP 请求失败")
        require_available_coupon(await response.json(), coupon_code)
        self._step("coupon-available", "目标卡券 availableCount 大于零，允许继续")

    async def select_pickup_type(self, pickup_type: int, *, ensure_selected: bool = False) -> None:
        if pickup_type not in (1, 2):
            raise ValueError("取餐方式无效")
        icons = self.page.locator('img[src*="icon-pickup.png"], img[src*="icon-store-dining.png"]').filter(visible=True)
        if not await icons.count():
            if not ensure_selected:
                return
            # The coupon helper can appear before the pickup popup. Confirm the
            # actual menu choice, opening its selector if it is unset/different.
            choice = self.page.locator(".address-store-d-wrap").filter(visible=True)
            if not await choice.count():
                self._step(
                    "pickup-deferred", "当前餐厅菜单没有取餐选择器，出现选项时再选择 pickup_type=%s", pickup_type
                )
                return
            expected = "外带" if pickup_type == 1 else "堂食"
            if (await choice.inner_text()).strip(" |\n") == expected:
                self._step("pickup-selected", "菜单取餐方式已匹配 pickup_type=%s", pickup_type)
                return
            await self._click_frontmost(choice)
            await icons.first.wait_for(state="visible")
        filename = "icon-pickup.png" if pickup_type == 1 else "icon-store-dining.png"
        self._step("pickup-selecting", "选择取餐方式 pickup_type=%s", pickup_type)
        await self._click_frontmost(self.page.locator(f'img[src*="{filename}"]').filter(visible=True))
        await (
            self.page.locator(f'.pt-item__selected img[src*="{filename}"]')
            .filter(visible=True)
            .first.wait_for(state="visible")
        )
        await self._click_frontmost(self.page.locator(".pt-btn.pt-btn__selected").filter(visible=True))
        await icons.first.wait_for(state="hidden")
        if ensure_selected:
            await (
                self.page.locator(".address-store-d-info-store")
                .filter(has_text=re.compile("^" + ("外带" if pickup_type == 1 else "堂食") + "$"), visible=True)
                .first.wait_for(state="visible")
            )
        self._step("pickup-selected", "取餐方式已确认 pickup_type=%s", pickup_type)

    async def use_coupon(
        self,
        coupon_code: str,
        pickup_type: int,
        *,
        has_product_options: bool,
        production_info: list[dict[str, Any]] | None = None,
    ) -> str:
        await self.select_pickup_type(pickup_type)
        coupon_responses: asyncio.Queue[Response] = asyncio.Queue()
        cart_updates: asyncio.Queue[Response] = asyncio.Queue()

        def observe(response: Response) -> None:
            if matches_endpoint(response.url, {"useCoupon", "useCouponCode"}):
                coupon_responses.put_nowait(response)
            elif matches_endpoint(response.url, {CART_UPDATE_ENDPOINT}):
                cart_updates.put_nowait(response)

        self.page.on("response", observe)
        try:
            await self.click_coupon(coupon_code)
            self._step("coupon-response-waiting", "等待用券结果")
            payload = await self._wait_for_coupon_result(coupon_responses)
            cart_id = coupon_cart_id(payload)
            if await self._complete_optional_meal(
                cart_updates,
                production_info or [],
                has_product_options=has_product_options,
            ):
                self._step("coupon-added-to-cart", "可选餐品已加入购物车")
            else:
                self._step("coupon-applied", "无配置项券已生效，等待确认选好了")
            return cart_id
        finally:
            self._use_response = None
            self.page.remove_listener("response", observe)

    async def _wait_for_response(self, responses: asyncio.Queue[Response], operation: str, timeout: float) -> Response:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_stop()
            try:
                return await asyncio.wait_for(responses.get(), timeout=0.2)
            except TimeoutError:
                continue
        raise RuntimeError(f"等待 KFC {operation} 响应超时")

    async def _wait_for_coupon_result(self, responses: asyncio.Queue[Response]) -> dict[str, Any]:
        for attempt in range(1, 5):
            response = await self._wait_for_response(responses, "useCoupon", 45)
            if not response.ok:
                raise RuntimeError("KFC useCoupon HTTP 请求失败")
            payload = await response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("KFC useCoupon 返回格式无效")
            if str(payload.get("code")) != SLIDER_REQUIRED_CODE:
                return payload
            if attempt == 4:
                raise RuntimeError("KFC 连续要求滑动验证，停止重复用券")
            self._step("slider-required", "useCoupon 要求滑动验证 attempt=%s/3", attempt)
            # KFC retries useCoupon after Geetest succeeds; wait for that response.
            self._use_response = None
            if await self.solve_slider() != "verified":
                raise RuntimeError("KFC 要求滑动验证，但验证页面未出现")
        raise RuntimeError("KFC useCoupon 未返回有效结果")

    async def _complete_optional_meal(
        self,
        cart_updates: asyncio.Queue[Response],
        production_info: list[dict[str, Any]],
        *,
        has_product_options: bool,
    ) -> bool:
        if not has_product_options:
            self._step("coupon-options-skipped", "数据库判定当前餐品无配置项")
            return False
        add_to_cart = self.page.get_by_text("加入购物车", exact=True).filter(visible=True)
        await add_to_cart.first.wait_for(state="visible", timeout=5000)
        if not production_info:
            raise RuntimeError("可选餐品券缺少 order_confirmation_logs 餐品选择记录")

        await self._select_meal_options(production_info)
        self._step("coupon-cart-updating", "餐品名称已全部匹配，点击加入购物车")
        await self._click_frontmost(add_to_cart)
        response = await self._wait_for_response(cart_updates, "cart/update", 20)
        if not response.ok:
            raise RuntimeError("KFC cart/update HTTP 请求失败")
        payload = await response.json()
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise RuntimeError("KFC cart/update 返回失败")
        return True

    async def _select_meal_options(self, production_info: list[dict[str, Any]]) -> None:
        """按确认记录调整页面选择，与加入购物车的写入边界分离。"""
        # 取消默认选项后底部按钮会改成选餐提示，不能继续用“加入购物车”文字定位祖先。
        detail_root = self.page.locator(".page_body").filter(has=self.page.locator(".m-m-i")).filter(visible=True)
        if await detail_root.count() != 1:
            raise RuntimeError("无法唯一定位当前餐品选择页面")
        if not production_info or any(not isinstance(item, dict) for item in production_info):
            raise RuntimeError("订单确认记录中的餐品选择缺少中文名称")
        names = [
            str(item.get("name") or "").strip()
            for item in sorted(production_info, key=lambda item: int(item.get("group_no") or 0))
            if isinstance(item, dict)
        ]
        if len(names) != len(production_info) or any(not name for name in names):
            raise RuntimeError("订单确认记录中的餐品选择缺少中文名称")
        self._step("coupon-options-detected", "检测到可选餐品 selections=%s", len(names))

        await self._expand_meal_options(detail_root)
        cards = detail_root.locator(".m-m-i").filter(visible=True)
        page_variants = {
            variant for line in (await detail_root.inner_text()).splitlines() for variant in _option_name_variants(line)
        }
        # 不可重复选分组默认已占满名额，必须先取消勾选，才能选择同组其他餐品。
        cleared = 0
        for card_index in range(await cards.count()):
            self._check_stop()
            card = cards.nth(card_index)
            radio = card.locator(".m-m-i-grey-radio").filter(visible=True)
            if await radio.count() and await self._meal_card_selected(card):
                name = " ".join((await card.locator(".m-m-i-title").inner_text()).split())
                self._step("coupon-option-clearing", "取消默认餐品 name=%s", name)
                await self._click_frontmost(radio, scroll=True, stable=True)
                await self._wait_for_meal_card_selected(card, name, selected=False)
                cleared += 1
        self._step("coupon-options-cleared", "默认餐品取消完成 count=%s", cleared)

        selected_cards: list[tuple[Locator, str]] = []
        for index, name in enumerate(names, start=1):
            matching = [
                cards.nth(card_index)
                for card_index in range(await cards.count())
                if await self._meal_card_matches(cards.nth(card_index), name)
            ]
            if len(matching) > 1:
                raise RuntimeError(f"餐品“{name}”匹配到多个页面选项")
            if not matching:
                if _option_name_variants(name).isdisjoint(page_variants):
                    raise RuntimeError(f"餐品“{name}”在页面中不存在")
                logger.info(
                    "[核销] job=%s stage=coupon-option-fixed name=%s selection=%s/%s",
                    self.job_id,
                    name,
                    index,
                    len(names),
                )
                continue
            card = matching[0]
            selected_cards.append((card, name))
            if await self._meal_card_selected(card):
                logger.info(
                    "[核销] job=%s stage=coupon-option-verified name=%s selection=%s/%s",
                    self.job_id,
                    name,
                    index,
                    len(names),
                )
                continue
            if await card.locator(".m-m-i-cover").filter(has_text="已售罄").count():
                raise RuntimeError(f"餐品“{name}”已售罄")
            radio = card.locator(".m-m-i-grey-radio").filter(visible=True)
            additions = card.locator(".btn-circle .add-sub-wrap").filter(visible=True)
            control = radio if await radio.count() else additions.last if await additions.count() else None
            if control is None:
                raise RuntimeError(f"餐品“{name}”当前不可选择")
            self._step(
                "coupon-option-selecting",
                "按中文名称选择餐品 name=%s selection=%s/%s",
                name,
                index,
                len(names),
            )
            await self._click_frontmost(control, scroll=True, stable=True)
            await self._wait_for_meal_card_selected(card, name)

        # 后续点击可能改变同组已选项，提交前重新核对全部目标仍然选中。
        for card, name in selected_cards:
            if not await self._meal_card_selected(card):
                raise RuntimeError(f"餐品“{name}”在完成选择后未保持选中")
        self._step("coupon-options-verified", "目标餐品选择完成 selections=%s", len(names))

    async def _expand_meal_options(self, detail_root: Locator) -> None:
        """只展开当前套餐的折叠分组，等待文字切换，防止重复点击又收起。"""
        more = detail_root.get_by_text("更多", exact=True).filter(visible=True)
        expanded = 0
        while await more.count():
            self._check_stop()
            if expanded >= 30:
                raise RuntimeError("餐品分组展开次数超限")
            label = await more.first.element_handle()
            if label is None:
                raise RuntimeError("餐品展开按钮已失效")
            self._step("coupon-options-expanding", "展开餐品分组 index=%s", expanded + 1)
            try:
                await self._click_frontmost(more.first, scroll=True)
                deadline = time.monotonic() + 3
                while await label.evaluate("node => node.isConnected && node.textContent.trim() === '更多'"):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("点击更多后餐品分组未展开")
                    await self._wait(100)
            finally:
                await label.dispose()
            expanded += 1
        self._step("coupon-options-expanded", "餐品列表展开完成 groups=%s", expanded)

    @staticmethod
    async def _meal_card_matches(card: Locator, target_name: str) -> bool:
        target = _option_name_variants(target_name)
        displayed = {
            variant for line in (await card.inner_text()).splitlines() for variant in _option_name_variants(line)
        }
        return not target.isdisjoint(displayed)

    @staticmethod
    async def _meal_card_selected(card: Locator) -> bool:
        return await card.evaluate("""node => {
          // 单选标记优先于图片和边框，避免相同图片或白色高亮造成误判。
          const radio = node.querySelector('.m-m-i-grey-radio');
          if (radio) return Boolean(radio.querySelector('.nz-rbtn_false, [aria-checked="true"], input:checked'));
          const style = node.getAttribute('style') || '';
          if (/border(?:-color)?\\s*:/i.test(style) && /background-color\\s*:/i.test(style)) return true;
          const checked = node.querySelector('[aria-checked="true"], input:checked');
          if (checked) return true;
          const image = node.querySelector('.m-m-i-img img, img.m-m-i-img, .m-m-i-img');
          const src = image?.src || image?.getAttribute?.('src') || '';
          return Boolean(src) && [...document.querySelectorAll('.s-c-n img')].some(
            selected => selected.src === src || selected.getAttribute('src') === src
          );
        }""")

    async def _wait_for_meal_card_selected(self, card: Locator, name: str, *, selected: bool = True) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self._check_stop()
            if await self._meal_card_selected(card) == selected:
                # KFC 按钮默认有 300ms 节流，DOM 先更新不代表下一次点击已可受理。
                await self._wait(350)
                return
            await self._wait(100)
        state = "选中" if selected else "取消选中"
        raise RuntimeError(f"餐品“{name}”点击后未确认{state}")

    async def submit_order(self, pickup_type: int) -> dict[str, Any]:
        close = self.page.locator(".ko-oc-cch_b_x").filter(visible=True)
        if await close.count():
            await self._click_frontmost(close)
        await self.select_pickup_type(pickup_type)
        self._step("order-confirming", "点击第一个选好了按钮")
        await self.page.locator(".spcb-fixed-text").first.click()
        await self._select_confirmation_pickup_type(pickup_type)
        self._step("order-submitting", "点击提交订单并等待 submit 返回")
        async with self.page.expect_response(
            lambda response: matches_endpoint(response.url, {"submit"}), timeout=30000
        ) as observed:
            await self._click_frontmost(self.page.locator(".ko-st-c-sc_r_t").filter(visible=True))
        response = await observed.value
        payload = await response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("KFC submit 返回格式无效")
        self._step("submit-received", "submit 响应已接收 http_status=%s", response.status)
        return {"httpStatus": response.status, "body": payload}

    async def _select_confirmation_pickup_type(self, pickup_type: int) -> None:
        if pickup_type not in (1, 2):
            raise ValueError("取餐方式无效")
        name = "外带" if pickup_type == 1 else "堂食"
        option = self.page.get_by_text(name, exact=True).filter(visible=True)
        await option.first.wait_for(state="visible", timeout=15000)
        self._step("confirmation-pickup-selecting", "确认订单页选择取餐方式 pickup_type=%s name=%s", pickup_type, name)
        await self._click_frontmost(option)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for index in reversed(range(await option.count())):
                if await option.nth(index).evaluate(r"""node => {
                  for (let current = node; current && current !== document.body; current = current.parentElement) {
                    const style = getComputedStyle(current);
                    const colors = [style.borderTopColor, style.borderRightColor,
                                    style.borderBottomColor, style.borderLeftColor];
                    if (colors.some(color => {
                      const rgb = color.match(/\d+(?:\.\d+)?/g)?.map(Number);
                      return rgb && rgb.length >= 3 && rgb[0] >= 180 && rgb[1] <= 100 && rgb[2] <= 100;
                    })) return true;
                  }
                  return false;
                }"""):
                    self._step(
                        "confirmation-pickup-selected",
                        "确认订单页取餐方式已确认 pickup_type=%s name=%s",
                        pickup_type,
                        name,
                    )
                    return
            await self._wait(100)
        raise RuntimeError(f"确认订单页取餐方式“{name}”点击后未确认选中")

    async def _click_frontmost(self, locator: Locator, *, scroll: bool = False, stable: bool = False) -> None:
        # Taro can keep multiple menu pages mounted at identical coordinates.
        # Only the element receiving the actual pointer event belongs to the active page.
        deadline = time.monotonic() + 15
        click_attempts = 0
        last_click_error: PlaywrightError | None = None
        while time.monotonic() < deadline:
            self._check_stop()
            for index in reversed(range(await locator.count())):
                candidate = locator.nth(index)
                if scroll:
                    # The fixed KFC cart footer can cover an element that is
                    # technically visible, so leave its center unobstructed.
                    await candidate.evaluate("node => node.scrollIntoView({block: 'center', inline: 'nearest'})")
                    await self._wait(100)
                exposed = await candidate.evaluate("""node => {
                  const box = node.getBoundingClientRect();
                  if (box.width <= 0 || box.height <= 0) return false;
                  const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                  return hit === node || node.contains(hit);
                }""")
                if exposed:
                    if "disable" in (await candidate.get_attribute("class") or ""):
                        raise RuntimeError("目标卡券按钮当前不可使用")
                    click_attempts += 1
                    try:
                        # 业务调用者会显式等待下一页状态；不在通用点击层等待 Taro 伪导航收敛。
                        # 餐品勾选需要等待布局动画稳定，避免在展开或取消定制项时点到移动中的控件。
                        await candidate.click(force=not stable, no_wait_after=True, timeout=1000)
                    except PlaywrightError as exc:
                        # Taro 可能在命中检测后替换节点，重新解析 Locator 后继续尝试活动页面。
                        last_click_error = exc
                        continue
                    if click_attempts > 1:
                        logger.debug(
                            "[核销] job=%s stage=frontmost-click result=success attempts=%s",
                            self.job_id,
                            click_attempts,
                        )
                    return
            await self._wait(200)
        if last_click_error is not None:
            raise RuntimeError("当前页面目标按钮在命中检测后持续失效，无法点击") from last_click_error
        raise RuntimeError("当前页面没有可点击的目标按钮")

    async def _challenge_frame(self, timeout: float = 15) -> Frame | None:
        deadline = time.monotonic() + timeout
        activated = False
        while time.monotonic() < deadline:
            self._check_stop()
            if self._use_response is not None and self._use_response.is_done():
                return None
            for frame in self.page.frames:
                if await frame.locator(".geetest_canvas_bg:visible").count():
                    return frame
                radar = frame.locator(".geetest_radar_tip:visible")
                if not activated and await radar.count() == 1:
                    await radar.click()
                    activated = True
            await self._wait(200)
        if activated:
            raise RuntimeError("极验验证已打开，但滑块画布未出现")
        # Do not silently accept an unsupported challenge implementation.
        if any(
            [
                await frame.locator('[class*="geetest"][class*="panel"]:visible, .geetest_holder:visible').count()
                for frame in self.page.frames
            ]
        ):
            raise RuntimeError("出现尚未识别的极验验证页面")
        return None

    async def _follow_points(self, points: list[dict[str, float]]) -> None:
        for point in points:
            self._check_stop()
            await self.page.mouse.move(point["x"], point["y"])
            await self._wait(point["delayMs"])

    async def solve_slider(self) -> str:
        self._step("slider-waiting", "等待滑动验证，最长 15 秒")
        frame = await self._challenge_frame()
        if frame is None:
            self._step("slider-not-requested", "未出现滑动验证，继续等待用券结果")
            return "not-requested"
        self._step("slider-visible", "滑动验证已出现，开始分析画布")
        await frame.evaluate(VISION_SCRIPT)
        for attempt in range(3):
            self._check_stop()
            logger.info("[核销] job=%s stage=slider_attempt attempt=%s/3 等待画布就绪", self.job_id, attempt + 1)
            await frame.wait_for_function(
                """() => {
              try { return kfcSliderVision.analyze().distance > 0; } catch { return false; }
            }""",
                timeout=15000,
            )
            await frame.locator(".geetest_slider_button:visible").wait_for(state="visible")
            await self._wait(450)
            geometry = await frame.evaluate("kfcSliderVision.analyze()")
            image = await frame.locator(".geetest_canvas_bg:visible").bounding_box()
            button = await frame.locator(".geetest_slider_button:visible").bounding_box()
            if not image or not button:
                raise RuntimeError("无法读取极验滑块位置")
            distance = geometry["distance"] * image["width"] / geometry["width"]
            logger.info(
                "[核销] job=%s stage=slider_drag attempt=%s distance=%.1fpx source=%s confidence=%.3f",
                self.job_id,
                attempt + 1,
                distance,
                geometry["distanceSource"],
                geometry["confidence"],
            )
            x, y = button["x"] + button["width"] / 2, button["y"] + button["height"] / 2
            viewport = self.page.viewport_size or await self.page.evaluate("({width: innerWidth, height: innerHeight})")
            approach = await frame.evaluate(
                """p => kfcSliderVision.buildPreClickRoutine(p.width, p.height,
                {endX: p.x, endY: p.y, behaviorLevel: p.attempt, segments: 5})""",
                {**viewport, "x": x, "y": y, "attempt": attempt},
            )
            await self._follow_points(approach)
            points = await frame.evaluate(
                """p => kfcSliderVision.buildTrajectory(p.x, p.y, p.distance,
                {behaviorLevel: p.attempt})""",
                {"x": x, "y": y, "distance": distance, "attempt": attempt},
            )
            await self.page.mouse.move(x, y)
            result_panel = await frame.locator(".geetest_canvas_bg:visible").evaluate_handle(
                "canvas => canvas.closest('.geetest_panel.geetest_wind') || canvas.closest('.geetest_holder') || document.body"
            )
            await self.page.mouse.down()
            try:
                await self._wait(150)
                await self._follow_points(points)
            finally:
                await self.page.mouse.up()
            try:
                result = await self._verification_result(frame, result_panel)
            finally:
                await result_panel.dispose()
            logger.info(
                "[核销] job=%s stage=slider_result attempt=%s result=%s", self.job_id, attempt + 1, result["kind"]
            )
            if result.get("verificationPassed") is True:
                self._step("slider-verified", "滑动验证通过，继续等待用券结果")
                return "verified"
            if not result.get("retryable") or attempt == 2:
                raise RuntimeError("滑动验证未确认成功，请检查保留的浏览器")
            previous = geometry["signature"]
            retry = frame.get_by_text("请点击此处重试", exact=True).filter(visible=True)
            if await retry.count() != 1:
                retry = frame.locator(".geetest_refresh_1:visible")
            if await retry.count() != 1:
                raise RuntimeError("无法唯一定位滑动重试按钮")
            await retry.click()
            await frame.wait_for_function(
                """old => {
              try { return kfcSliderVision.signature() !== old; } catch { return false; }
            }""",
                arg=previous,
                timeout=10000,
            )
        raise RuntimeError("滑动验证未完成")

    async def _verification_result(self, frame: Frame, panel: Any) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        result: dict[str, Any] = {"kind": "unknown", "retryable": False}
        while time.monotonic() < deadline:
            self._check_stop()
            if frame.is_detached():
                return result
            result = await panel.evaluate("""panel => {
              const messages = [...panel.querySelectorAll('.geetest_result, .geetest_success_radar_tip, .geetest_radar_tip, .geetest_panel_error_tips')]
                .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
                .map(e => e.textContent || '');
              const result = kfcSliderVision.classifyVerificationResult(messages);
              if (result.kind !== 'unknown') return result;
              // The KFC popup reports success with a CSS class and speed percentile,
              // then hides the panel before the coupon button changes.
              if (panel.querySelector('.geetest_result.geetest_success')) {
                return {kind: 'success', verificationPassed: true, retryable: false};
              }
              return result;
            }""")
            if result["kind"] != "unknown":
                # Allow a late server rejection to supersede the puzzle success message.
                await self._wait(1000)
                if not frame.is_detached():
                    rejected = await panel.evaluate("""panel => [...panel.querySelectorAll('.geetest_result, .geetest_panel_error_tips')]
                        .filter(e => e.getClientRects().length).map(e => e.textContent || '')""")
                    if re.search(r"forbidden|请重试|请正确拼合图像", " ".join(rejected), re.I):
                        return {"kind": "server-rejected", "verificationPassed": False, "retryable": True}
                return result
            await self._wait(100)
        return result

    def checkpoint(self, verification: str, error_type: str = "") -> dict[str, Any]:
        return {
            "stage": self.stage,
            "verification": verification,
            "errorType": error_type,
            "profileDir": str(self.profile),
            "browserPid": self.process.pid if self.process else None,
            "bundlePatched": self.bundle_patched,
        }
