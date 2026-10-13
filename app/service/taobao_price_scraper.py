"""使用 Playwright 浏览淘宝搜索页，并通过 Chrome 网络层请求商品详情链接。"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from base64 import b64decode
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, unquote_plus, urljoin, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, CDPSession, Error as PlaywrightError, Page, Request, Response

from app.config import settings
from app.service.taobao_browser import (
    playwright_runtime,
    taobao_browser_context,
)

logger = logging.getLogger("kfc.taobao_price")

_TAOBAO_ITEM_HOSTS = {"item.taobao.com", "detail.tmall.com", "chaoshi.detail.tmall.com"}
_ITEM_ID_KEYS = {"itemid", "item_id", "auctionid", "auction_id", "nid"}
_URL_KEYS = {"url", "itemurl", "item_url", "auctionurl", "auction_url", "detailurl", "detail_url"}
_TITLE_KEYS = ("title", "itemTitle", "item_title", "shortTitle", "name")
_SHOP_KEYS = ("shopName", "shop_name", "sellerNick", "seller_nick", "nick")
_PRICE_TEXT_KEYS = ("priceText", "price_text", "promotionPrice", "salePrice", "price")
_PRICE_PATTERN = re.compile(r"\d+(?:\.\d{1,2})?")
_ITEM_ID_PATTERN = re.compile(r"(?:[?&]|&amp;)id=(\d{5,})", re.IGNORECASE)
_SCRIPT_PATTERN = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_TITLE_PATTERN = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_DETAIL_REQUEST_INTERVAL_SECONDS = 1.0


class TaobaoVerificationRequired(RuntimeError):
    """淘宝要求人工验证时终止本次任务，避免继续请求放大风控。"""


class TaobaoPricePersistenceError(RuntimeError):
    """单条价格提交失败，必须中止任务，不能按商品解析失败跳过。"""


def _contains_detail_model(value: Any) -> bool:
    """判断 JSON 值中是否包含淘宝规格价格模型。"""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            sku_base = current.get("skuBase") or current.get("sku_base")
            sku_core = current.get("skuCore") or current.get("sku_core")
            if isinstance(sku_base, dict) and isinstance(sku_core, dict):
                return True
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _parse_json_text(text: str) -> Any | None:
    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        return None
    candidates = [stripped]
    left = stripped.find("(")
    right = stripped.rfind(")")
    if 0 <= left < right:
        candidates.append(stripped[left + 1 : right])
    first_brace = min((index for index in (stripped.find("{"), stripped.find("[")) if index >= 0), default=-1)
    if first_brace >= 0:
        last_brace = max(stripped.rfind("}"), stripped.rfind("]"))
        if last_brace > first_brace:
            candidates.append(stripped[first_brace : last_brace + 1])
    decoded_values: list[Any] = []
    for candidate in candidates:
        try:
            decoded_values.append(json.loads(candidate))
        except (TypeError, ValueError):
            try:
                # 淘宝初始化脚本会在 JSON 对象后继续拼接 JavaScript，允许读取首个完整 JSON 值。
                decoded_values.append(json.JSONDecoder().raw_decode(candidate.lstrip())[0])
            except (TypeError, ValueError):
                continue
    if "skuBase" not in stripped and "skuCore" not in stripped:
        return decoded_values[0] if decoded_values else None
    for value in decoded_values:
        if _contains_detail_model(value):
            return value

    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"[{[]", stripped))):
        try:
            value, end = decoder.raw_decode(stripped[match.start() :])
        except (TypeError, ValueError):
            continue
        if end <= 0 or not _contains_detail_model(value):
            continue
        return value
    return decoded_values[0] if decoded_values else None


def _response_payload(response: Response) -> Any | None:
    if response.status >= 400:
        return None
    try:
        return _parse_json_text(response.text())
    except Exception:
        logger.debug("淘宝浏览器响应无法读取 url=%s", response.url, exc_info=True)
        return None


def _iter_nodes(value: Any) -> Iterator[Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str) and current.lstrip().startswith(("{", "[")):
            decoded = _parse_json_text(current)
            if decoded is not None and decoded is not current:
                stack.append(decoded)
                continue
        yield current
        if isinstance(current, dict):
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def _diagnostic_key_paths(payloads: list[Any]) -> list[str]:
    """只记录字段路径和类型，避免把接口响应中的用户数据写入日志。"""
    samples: list[str] = []
    stack: list[tuple[str, Any]] = [(f"payload[{index}]", payload) for index, payload in enumerate(payloads)]
    while stack and len(samples) < 30:
        path, current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                child_path = f"{path}.{key}"
                normalized = str(key).casefold()
                if any(marker in normalized for marker in ("sku", "price", "prop")):
                    samples.append(f"{child_path}:{type(child).__name__}")
                    if len(samples) >= 30:
                        break
                if isinstance(child, (dict, list)):
                    stack.append((child_path, child))
                elif isinstance(child, str) and child.lstrip().startswith(("{", "[")):
                    decoded = _parse_json_text(child)
                    if decoded is not None:
                        stack.append((child_path, decoded))
        elif isinstance(current, list):
            stack.extend((f"{path}[{index}]", child) for index, child in enumerate(current[:100]))
    return samples


def _canonical_product_url(raw_url: str) -> str | None:
    value = html.unescape(unquote(str(raw_url or "").strip().replace("\\/", "/")))
    if not value:
        return None
    if value.startswith("//"):
        value = f"https:{value}"
    value = urljoin("https://s.taobao.com/", value)
    parsed = urlsplit(value)

    query = parse_qs(parsed.query)
    for redirect_key in ("url", "target", "redirect", "redirect_url"):
        redirected = (query.get(redirect_key) or [""])[0]
        if redirected and redirected != value:
            canonical = _canonical_product_url(redirected)
            if canonical:
                return canonical

    item_id = (query.get("id") or query.get("itemId") or query.get("item_id") or [""])[0]
    if not item_id:
        match = _ITEM_ID_PATTERN.search(value)
        item_id = match.group(1) if match else ""
    if not str(item_id).isdigit():
        return None

    host = (parsed.hostname or "").lower()
    if host not in _TAOBAO_ITEM_HOSTS:
        host = "item.taobao.com"
    return f"https://{host}/item.htm?id={item_id}"


def _first_search_page_url(raw_url: str) -> str:
    """只替换页码，保留淘宝搜索词及其他参数的原始编码。"""
    parsed = urlsplit(raw_url)
    parts = []
    keywords = []
    encodings = set()
    for part in parsed.query.split("&"):
        name, _, value = part.partition("=")
        key = unquote_plus(name)
        if key == "page":
            continue
        if key == "q":
            keywords.append(value)
        elif key == "ie":
            encodings.add(unquote_plus(value).strip().lower().replace("-", "") or "utf8")
        parts.append(part)

    if len(encodings) > 1 or not encodings <= {"utf8", "gbk", "gb2312", "gb18030"}:
        raise ValueError("淘宝搜索链接编码不支持或不一致，请从淘宝搜索结果页重新复制完整链接")
    encoding = next(iter(encodings), "utf8")
    for value in keywords:
        try:
            # 默认的容错解码会把截断字节替换成“�”，此时必须报错，不能猜测原始搜索词。
            keyword = unquote_plus(value, encoding=encoding, errors="strict")
        except UnicodeError as exc:
            raise ValueError("淘宝搜索词编码已损坏，请从淘宝搜索结果页重新复制完整链接") from exc
        if "\ufffd" in keyword or re.search(r"%(?![0-9a-fA-F]{2})", value):
            raise ValueError("淘宝搜索词编码已损坏，请从淘宝搜索结果页重新复制完整链接")

    parts.append("page=1")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(parts), parsed.fragment))


def _normalize_search_url(raw_url: str) -> str:
    """校验页面提交的淘宝搜索链接，并固定为第一页。"""
    value = str(raw_url or "").strip()
    if not value:
        raise ValueError("请输入淘宝搜索链接")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("抓取链接必须以 http:// 或 https:// 开头")
    host = (parsed.hostname or "").casefold()
    if host == "s.taobao.com":
        return _first_search_page_url(value)
    raise ValueError("仅支持 s.taobao.com 的淘宝搜索链接")


def _links_from_payload(payload: Any) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            normalized_key = str(key).replace("-", "").lower()
            if normalized_key in _URL_KEYS and isinstance(value, str):
                canonical = _canonical_product_url(value)
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    links.append(canonical)
        has_title = any(str(node.get(key) or "").strip() for key in _TITLE_KEYS)
        if not has_title:
            continue
        for key, value in node.items():
            if str(key).replace("-", "").lower() not in _ITEM_ID_KEYS:
                continue
            item_id = str(value or "").strip()
            if not item_id.isdigit() or len(item_id) < 5:
                continue
            canonical = f"https://item.taobao.com/item.htm?id={item_id}"
            if canonical not in seen:
                seen.add(canonical)
                links.append(canonical)
    return links


def _first_string_by_key(value: Any, keys: tuple[str, ...]) -> str:
    normalized_keys = {key.casefold() for key in keys}
    for node in _iter_nodes(value):
        if not isinstance(node, dict):
            continue
        for key, child in node.items():
            if str(key).casefold() not in normalized_keys or isinstance(child, (dict, list)):
                continue
            text = str(child or "").strip()
            if text:
                return text
    return ""


def _detail_model(payload: Any) -> dict[str, Any] | None:
    for node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue
        sku_base = node.get("skuBase") or node.get("sku_base")
        sku_core = node.get("skuCore") or node.get("sku_core")
        if isinstance(sku_base, dict) and isinstance(sku_core, dict):
            return node
    return None


def _decimal_price(value: Any, *, cents: bool = False) -> Decimal | None:
    if isinstance(value, dict):
        for key in _PRICE_TEXT_KEYS:
            if key in value:
                parsed = _decimal_price(value[key], cents=False)
                if parsed is not None:
                    return parsed
        if "priceMoney" in value:
            return _decimal_price(value["priceMoney"], cents=True)
        return None
    if value is None or isinstance(value, bool):
        return None
    match = _PRICE_PATTERN.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        amount = Decimal(match.group(0))
    except InvalidOperation:
        return None
    if cents and "." not in match.group(0):
        amount /= Decimal("100")
    return amount.quantize(Decimal("0.01"))


def _sku_name_map(sku_base: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    props = sku_base.get("props") or sku_base.get("properties") or []
    if not isinstance(props, list):
        return result
    for prop in props:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or prop.get("propId") or "").strip()
        values = prop.get("values") or prop.get("items") or []
        if not isinstance(values, list):
            continue
        for option in values:
            if not isinstance(option, dict):
                continue
            vid = str(option.get("vid") or option.get("valueId") or option.get("id") or "").strip()
            name = str(option.get("name") or option.get("valueName") or option.get("text") or "").strip()
            if not vid or not name:
                continue
            result[vid] = name
            if pid:
                result[f"{pid}:{vid}"] = name
    return result


def _sku_item_name(sku: dict[str, Any], names: dict[str, str], fallback: str) -> str:
    prop_path = str(sku.get("propPath") or sku.get("prop_path") or sku.get("properties") or "")
    parts: list[str] = []
    for part in re.split(r"[;,]", prop_path):
        normalized = part.strip()
        if not normalized:
            continue
        name = names.get(normalized) or names.get(normalized.rsplit(":", 1)[-1])
        if name and name not in parts:
            parts.append(name)
    explicit_name = str(sku.get("skuName") or sku.get("name") or "").strip()
    if not parts and explicit_name:
        parts.append(explicit_name)
    return " / ".join(parts) or fallback


def _sku_is_unavailable(info: Any) -> bool:
    """只排除淘宝明确标记无货的规格，缺少库存字段不能当成零库存。"""
    if not isinstance(info, dict):
        return False
    if str(info.get("quantityText") or "").strip() in {"无货", "缺货", "售罄", "已售罄"}:
        return True
    quantity = info.get("quantity")
    if quantity is None or isinstance(quantity, bool):
        return False
    try:
        amount = Decimal(str(quantity))
    except InvalidOperation:
        return False
    return amount.is_finite() and amount <= 0


def _prices_from_payload(
    payload: Any,
    fallback_title: str,
    price_callback: Callable[[str, str, Decimal], None] | None = None,
    unavailable_callback: Callable[[str], None] | None = None,
) -> tuple[str, str, list[tuple[str, Decimal]]]:
    model = _detail_model(payload)
    if not model:
        return "", "", []
    sku_base = model.get("skuBase") or model.get("sku_base") or {}
    sku_core = model.get("skuCore") or model.get("sku_core") or {}
    title = _first_string_by_key(model, _TITLE_KEYS) or fallback_title
    shop_name = _first_string_by_key(model, _SHOP_KEYS)
    sku2info = sku_core.get("sku2info") or sku_core.get("sku2Info") or sku_core.get("skuMap") or {}
    if not isinstance(sku2info, dict):
        sku2info = {}

    names = _sku_name_map(sku_base)
    raw_skus = sku_base.get("skus") or sku_base.get("skuList") or []
    skus = raw_skus if isinstance(raw_skus, list) else []
    rows: list[tuple[str, Decimal]] = []
    seen_names: set[str] = set()
    unavailable_names: set[str] = set()

    def emit_price(item_name: str, price: Decimal) -> None:
        # 必须等当前价格提交成功后才继续解析下一条规格，不能先攒完整商品列表。
        if price_callback is not None:
            price_callback(shop_name, item_name, price)
        rows.append((item_name, price))

    for sku in skus:
        if not isinstance(sku, dict):
            continue
        sku_id = str(sku.get("skuId") or sku.get("sku_id") or sku.get("id") or "").strip()
        info = sku2info.get(sku_id)
        if info is None and sku_id.isdigit():
            info = sku2info.get(int(sku_id))
        if _sku_is_unavailable(info):
            unavailable_names.add(_sku_item_name(sku, names, title or "默认规格"))
            continue
        price = _decimal_price(info)
        if price is None:
            price = _decimal_price(sku)
        item_name = _sku_item_name(sku, names, title or "默认规格")
        if price is None or item_name in seen_names:
            continue
        seen_names.add(item_name)
        emit_price(item_name, price)

    # 有规格的商品不能因全部无货或解析失败而退回汇总起价，制造可售记录。
    if not rows and not skus:
        default_info = sku2info.get("0") or sku2info.get(0) or sku_core
        if _sku_is_unavailable(default_info):
            unavailable_names.add(title or "默认规格")
        else:
            price = _decimal_price(default_info)
            if price is not None:
                emit_price(title or "默认规格", price)
    if unavailable_callback is not None:
        # 数据库以规格名称唯一定位；同名规格仍有货时保留刚提交的价格。
        for item_name in sorted(unavailable_names - seen_names):
            unavailable_callback(item_name)
    return shop_name, title, rows


def _title_from_html(document: str) -> str:
    match = _TITLE_PATTERN.search(document)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def _read_network_resource(cdp: CDPSession, stream: str) -> str:
    """读取 Chrome 网络层返回的详情响应，并始终关闭临时数据流。"""
    chunks: list[str] = []
    try:
        while True:
            result = cdp.send("IO.read", {"handle": stream, "size": 1024 * 1024})
            data = str(result.get("data") or "")
            if result.get("base64Encoded"):
                data = b64decode(data).decode("utf-8", errors="replace")
            chunks.append(data)
            if result.get("eof"):
                return "".join(chunks)
    finally:
        try:
            cdp.send("IO.close", {"handle": stream})
        except Exception:
            # 数据流关闭失败不应覆盖已经发生的请求或解析异常。
            logger.warning("关闭淘宝详情链接响应流失败", exc_info=True)


class TaobaoBrowserScraper:
    def __init__(
        self,
        status_callback: Callable[..., None],
        stop_event: threading.Event,
        auth_state: dict[str, Any],
        *,
        job_id: str = "manual",
        price_callback: Callable[[dict[str, Any]], None] | None = None,
        unavailable_callback: Callable[[str, list[str]], None] | None = None,
        target_url: str = "",
    ):
        self._status_callback = status_callback
        self._stop_event = stop_event
        self._auth_state = auth_state
        self._job_id = job_id
        self._price_callback = price_callback
        self._unavailable_callback = unavailable_callback
        self._target_url = target_url
        self._config = settings()
        self._last_detail_request_started = 0.0

    def _wait_for_detail_request_slot(self) -> None:
        """限制商品链接请求频率，任意两次请求启动至少间隔一秒。"""
        deadline = self._last_detail_request_started + _DETAIL_REQUEST_INTERVAL_SECONDS
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                raise RuntimeError("淘宝价格采集已停止")
            time.sleep(min(0.1, deadline - time.monotonic()))
        self._last_detail_request_started = time.monotonic()

    @staticmethod
    def _verification_page_ready(page: Page) -> tuple[bool, str, str]:
        """判断人工验证页是否已经回到可继续请求的商品详情页。"""
        try:
            current_url = page.url
            title = page.title()
            visible_text = page.locator("body").inner_text(timeout=1000)[:5000]
        except Exception:
            return False, "", ""
        challenge_text = f"{current_url}\n{title}\n{visible_text}".casefold()
        challenge_markers = (
            "验证码",
            "滑块",
            "安全验证",
            "请完成验证",
            "sec.taobao.com",
            "punish",
            "_____tmd_____",
        )
        ready = _canonical_product_url(current_url) is not None and not any(
            marker in challenge_text for marker in challenge_markers
        )
        return ready, current_url, title

    def _wait_for_verification_and_retry(
        self,
        context: BrowserContext,
        cdp: CDPSession,
        search_frame_id: str,
        product_url: str,
    ) -> list[dict[str, Any]]:
        """打开可见验证页，等待用户处理后重新请求当前商品。"""
        timeout_seconds = max(60, self._config.taobao_login_timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        verification_page = context.new_page()
        self._status_callback(
            phase="waiting_verification",
            message="检测到淘宝验证码，请在弹出的浏览器中完成验证",
        )
        logger.warning(
            "淘宝验证码窗口打开 jobId=%s url=%s timeoutSeconds=%s",
            self._job_id,
            product_url,
            timeout_seconds,
        )
        try:
            verification_page.bring_to_front()
            try:
                verification_page.goto(
                    product_url,
                    wait_until="domcontentloaded",
                    timeout=self._config.taobao_page_timeout_ms,
                )
            except PlaywrightError:
                # 验证页面可能保持长连接；窗口已经打开时继续等待人工操作。
                logger.warning(
                    "淘宝验证码页面导航未完成，继续等待人工验证 jobId=%s url=%s",
                    self._job_id,
                    product_url,
                    exc_info=True,
                )
            verification_page.bring_to_front()
            next_retry_at = 0.0
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    raise RuntimeError("淘宝价格采集已停止")
                if verification_page.is_closed():
                    raise TaobaoVerificationRequired("淘宝验证窗口已关闭，请重新开始抓取")

                ready, current_url, title = self._verification_page_ready(verification_page)
                now = time.monotonic()
                if ready and now >= next_retry_at:
                    logger.info(
                        "淘宝人工验证页面已返回商品详情，重试链接请求 jobId=%s requestedUrl=%s currentUrl=%s title=%s",
                        self._job_id,
                        product_url,
                        current_url,
                        title[:200],
                    )
                    try:
                        rows = self._scrape_detail(cdp, search_frame_id, product_url)
                    except TaobaoVerificationRequired:
                        next_retry_at = time.monotonic() + 5
                        logger.warning(
                            "淘宝人工验证后链接请求仍被拦截，继续等待 jobId=%s url=%s",
                            self._job_id,
                            product_url,
                        )
                    else:
                        self._status_callback(phase="scraping", message="淘宝验证完成，继续抓取商品")
                        logger.info("淘宝人工验证完成 jobId=%s url=%s", self._job_id, product_url)
                        return rows
                verification_page.wait_for_timeout(1000)
            raise TaobaoVerificationRequired("淘宝验证码等待超时，请重新开始抓取")
        finally:
            try:
                if not verification_page.is_closed():
                    verification_page.close()
            except Exception:
                logger.debug("关闭淘宝验证码窗口失败 jobId=%s url=%s", self._job_id, product_url, exc_info=True)
            logger.info("淘宝验证码窗口已关闭 jobId=%s url=%s", self._job_id, product_url)

    @staticmethod
    def _dom_links(page: Page) -> list[str]:
        hrefs = page.locator("a[href]").evaluate_all("elements => elements.map(element => element.href)")
        links: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            canonical = _canonical_product_url(str(href))
            if canonical and canonical not in seen:
                seen.add(canonical)
                links.append(canonical)
        return links

    def _discover_links(self, page: Page, payloads: list[Any]) -> list[str]:
        deadline = time.monotonic() + min(30, max(5, self._config.taobao_login_timeout_seconds))
        waiting_announced = False
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                raise RuntimeError("淘宝价格采集已停止")
            links = self._dom_links(page)
            for payload in payloads:
                links.extend(_links_from_payload(payload))
            unique_links = list(dict.fromkeys(links))
            if unique_links:
                logger.info(
                    "淘宝搜索结果解析完成 jobId=%s finalUrl=%s domLinkCount=%s payloadCount=%s productCount=%s",
                    self._job_id,
                    page.url,
                    len(self._dom_links(page)),
                    len(payloads),
                    len(unique_links),
                )
                return unique_links[: max(1, self._config.taobao_scrape_max_products)]
            if not waiting_announced:
                waiting_announced = True
                self._status_callback(
                    phase="waiting_results",
                    message="正在等待淘宝搜索结果",
                )
                logger.warning("淘宝搜索页尚未出现商品 jobId=%s url=%s", self._job_id, page.url)
            page.wait_for_timeout(1000)
        raise RuntimeError("淘宝搜索页未加载出商品，已保存的登录信息可能失效，请重新登录并保存后重试")

    def _scrape_detail(
        self,
        cdp: CDPSession,
        search_frame_id: str,
        product_url: str,
    ) -> list[dict[str, Any]]:
        """由 Chrome 网络层请求商品链接，不导航或刷新浏览器详情页。"""
        self._wait_for_detail_request_slot()
        started = time.perf_counter()
        logger.info("淘宝商品详情 Chrome 链接请求开始 jobId=%s url=%s", self._job_id, product_url)
        result = cdp.send(
            "Network.loadNetworkResource",
            {
                "frameId": search_frame_id,
                "url": product_url,
                "options": {"disableCache": False, "includeCredentials": True},
            },
        )
        resource = result.get("resource") if isinstance(result.get("resource"), dict) else {}
        stream = str(resource.get("stream") or "")
        document = _read_network_resource(cdp, stream) if stream else ""
        final_url = product_url
        status = int(resource.get("httpStatusCode") or 0)
        network_error = str(resource.get("netErrorName") or "")
        raw_headers = resource.get("headers") if isinstance(resource.get("headers"), dict) else {}
        response_headers = {str(key).casefold(): str(value) for key, value in raw_headers.items()}
        security_reason = response_headers.get("x-sec-reason", "")[:100]
        punished = response_headers.get("bxpunish", "") not in {"", "0", "false", "False"}

        def persist_price(shop_name: str, item_name: str, price: Decimal) -> None:
            if self._price_callback is None:
                return
            try:
                self._price_callback(
                    {
                        "shop_name": shop_name or "未知店铺",
                        "product_url": product_url,
                        "item_name": item_name,
                        "price": price,
                    }
                )
            except Exception as exc:
                raise TaobaoPricePersistenceError(f"淘宝价格逐条入库失败：{item_name}") from exc

        try:
            title = _title_from_html(document)
            scripts = _SCRIPT_PATTERN.findall(document)
            payloads: list[Any] = []
            matching_script_shapes: list[str] = []
            for script in scripts:
                if "skuBase" not in script and "skuCore" not in script:
                    continue
                marker_index = max(script.find("skuBase"), script.find("skuCore"))
                shape = re.sub(r"\s+", " ", script[max(0, marker_index - 80) : marker_index + 160])
                matching_script_shapes.append(shape[:240])
                payload = _parse_json_text(script)
                if payload is not None:
                    payloads.append(payload)

            logger.info(
                "淘宝商品详情 Chrome 链接请求完成 jobId=%s requestedUrl=%s status=%s "
                "bodyLength=%s networkError=%s duration=%.1fms",
                self._job_id,
                product_url,
                status,
                len(document),
                network_error,
                (time.perf_counter() - started) * 1000,
            )
            for payload in payloads:
                unavailable_names: list[str] = []
                shop_name, _, rows = _prices_from_payload(payload, title, persist_price, unavailable_names.append)
                if unavailable_names and self._unavailable_callback is not None:
                    try:
                        self._unavailable_callback(product_url, unavailable_names)
                    except Exception as exc:
                        raise TaobaoPricePersistenceError("淘宝无货规格旧价格清理失败") from exc
                if not rows and not unavailable_names:
                    continue
                logger.info(
                    "淘宝商品详情链接解析完成 operation=parse_price result=success "
                    "jobId=%s url=%s payloadCount=%s itemCount=%s unavailableCount=%s",
                    self._job_id,
                    product_url,
                    len(payloads),
                    len(rows),
                    len(unavailable_names),
                )
                return [
                    {
                        "shop_name": shop_name or "未知店铺",
                        "product_url": product_url,
                        "item_name": item_name,
                        "price": price,
                    }
                    for item_name, price in rows
                ]

            visible_document = re.sub(
                r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
                " ",
                document,
                flags=re.IGNORECASE | re.DOTALL,
            )
            visible_text = re.sub(r"<[^>]+>", " ", html.unescape(visible_document))
            markers = [
                marker
                for marker in ("登录", "验证码", "滑块", "访问被拒绝", "商品已下架", "商品不存在")
                if marker in visible_text
            ]
            logger.warning(
                "淘宝商品详情链接解析诊断 jobId=%s requestedUrl=%s finalUrl=%s status=%s title=%s "
                "scriptCount=%s matchingScriptCount=%s payloadCount=%s markers=%s securityReason=%s "
                "punished=%s keyPaths=%s scriptShapes=%s",
                self._job_id,
                product_url,
                final_url,
                status,
                title[:200],
                len(scripts),
                len(matching_script_shapes),
                len(payloads),
                markers,
                security_reason,
                punished,
                _diagnostic_key_paths(payloads),
                matching_script_shapes[:3],
            )
            if (
                status in {412, 429}
                or punished
                or "captcha" in security_reason.casefold()
                or "验证码" in title
                or "滑块" in visible_text
                or "punish" in final_url.casefold()
                or "_____tmd_____" in final_url
            ):
                raise TaobaoVerificationRequired("淘宝要求验证码，请稍后重新登录并完成验证后再抓取")
            if not resource.get("success"):
                raise RuntimeError(f"商品详情链接请求失败，网络错误：{network_error or '未知错误'}")
            if status >= 400:
                raise RuntimeError(f"商品详情链接请求失败，HTTP {status}")
            raise RuntimeError("商品详情链接响应未解析到规格价格")
        finally:
            logger.debug("淘宝商品详情 Chrome 链接请求资源已释放 jobId=%s url=%s", self._job_id, product_url)

    def scrape(self) -> tuple[list[dict[str, Any]], int, int]:
        search_url = _normalize_search_url(self._target_url)
        with playwright_runtime() as playwright:
            with taobao_browser_context(
                playwright,
                task_name=f"scrape-{self._job_id}",
                headless=False,
                # 搜索列表页需要展示给操作者确认登录和结果；详情仍由网络链接请求完成。
                show_window=True,
                auth_state=self._auth_state,
            ) as context:
                search_page = context.pages[0] if context.pages else context.new_page()
                search_payloads: list[Any] = []

                def collect_search(request: Request) -> None:
                    lowered = request.url.lower()
                    if request.resource_type not in {"xhr", "fetch"}:
                        return
                    if "relationrecommend" not in lowered and "search" not in lowered:
                        return
                    # 请求完成后才读取接口正文，避免在响应头事件里等待文档或静态资源下载。
                    response = request.response()
                    if response is None:
                        return
                    payload = _response_payload(response)
                    if payload is not None:
                        search_payloads.append(payload)

                search_page.on("requestfinished", collect_search)
                self._status_callback(phase="searching", message="正在打开输入的淘宝搜索链接")
                logger.info(
                    "淘宝价格采集链接已确认 jobId=%s url=%s",
                    self._job_id,
                    search_url,
                )
                search_page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self._config.taobao_page_timeout_ms,
                )
                if not self._config.taobao_browser_headless:
                    search_page.bring_to_front()
                links = self._discover_links(search_page, search_payloads)
                search_page.remove_listener("requestfinished", collect_search)
                logger.info("淘宝搜索页发现商品 jobId=%s count=%s", self._job_id, len(links))

                cdp = context.new_cdp_session(search_page)
                cdp.send("Network.enable")
                frame_tree = cdp.send("Page.getFrameTree")
                search_frame_id = str(frame_tree["frameTree"]["frame"]["id"])
                logger.info(
                    "淘宝商品详情改用 Chrome 网络链接请求 jobId=%s minIntervalSeconds=%.1f",
                    self._job_id,
                    _DETAIL_REQUEST_INTERVAL_SECONDS,
                )
                prices: list[dict[str, Any]] = []
                failed = 0
                for index, product_url in enumerate(links, start=1):
                    if self._stop_event.is_set():
                        raise RuntimeError("淘宝价格采集已停止")
                    self._status_callback(
                        phase="scraping",
                        message=f"正在抓取商品 {index}/{len(links)}",
                        productCount=len(links),
                        processedCount=index - 1,
                        failedCount=failed,
                    )
                    started = time.perf_counter()
                    rows: list[dict[str, Any]] | None = None
                    try:
                        rows = self._scrape_detail(cdp, search_frame_id, product_url)
                    except TaobaoPricePersistenceError:
                        # 已提交的数据保留；写库失败交由任务边界记录并终止，不能继续抓下一商品。
                        raise
                    except TaobaoVerificationRequired:
                        logger.warning("淘宝商品链接请求触发验证码 jobId=%s url=%s", self._job_id, product_url)
                        rows = self._wait_for_verification_and_retry(
                            context,
                            cdp,
                            search_frame_id,
                            product_url,
                        )
                    except Exception:
                        failed += 1
                        logger.exception(
                            "淘宝商品价格解析失败 jobId=%s url=%s duration=%.1fms",
                            self._job_id,
                            product_url,
                            (time.perf_counter() - started) * 1000,
                        )
                    if rows is not None:
                        # 保存已在逐条解析时完成，这里仅汇总结果，不能再次调用写库回调。
                        prices.extend(rows)
                        logger.info(
                            "淘宝商品价格解析成功 jobId=%s url=%s itemCount=%s duration=%.1fms",
                            self._job_id,
                            product_url,
                            len(rows),
                            (time.perf_counter() - started) * 1000,
                        )
                    self._status_callback(processedCount=index, failedCount=failed)
                return prices, len(links), failed
