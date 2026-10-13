"""淘宝价格目录查询与后台采集任务编排。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from app.database.price import taobao_price_db, taobao_session_db
from app.errors import bad_request
from app.service.taobao_price_scraper import TaobaoBrowserScraper, _normalize_search_url
from app.utils import iso

logger = logging.getLogger("kfc.taobao_price")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _search_keyword_from_url(raw_url: str) -> str:
    """按搜索链接声明的编码还原关键词，用精确来源决定清理边界。"""
    query = urlsplit(_normalize_search_url(raw_url)).query
    parts = [part.partition("=") for part in query.split("&")]
    parameters = [(unquote_plus(name), value) for name, _, value in parts]
    encoding = next((unquote_plus(value).strip() for name, value in parameters if name == "ie"), "") or "utf8"
    # 编码声明只用于解码 q，跟踪参数可能采用其他编码，不应改变关键词归属。
    values = {
        unquote_plus(value, encoding=encoding, errors="strict").strip() for name, value in parameters if name == "q"
    }
    if len(values) != 1 or "" in values:
        raise ValueError("淘宝搜索链接必须包含一个明确的搜索关键词，请从搜索结果页重新复制链接")
    keyword = next(iter(values))
    if len(keyword) > 200:
        raise ValueError("淘宝搜索关键词不能超过 200 个字符")
    return keyword


class TaobaoPriceService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._status: dict[str, Any] = {
            "state": "idle",
            "phase": "idle",
            "message": "尚未开始抓取",
            "jobId": None,
            "startedAt": None,
            "finishedAt": None,
            "productCount": 0,
            "processedCount": 0,
            "failedCount": 0,
            "itemCount": 0,
            "deletedCount": 0,
        }

    def _update_status(self, **changes: Any) -> None:
        with self._lock:
            self._status.update(changes)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def list_prices(
        self,
        search: str = "",
        sort_by: str = "scrapedAt",
        sort_order: str = "desc",
        keywords: list[str] | None = None,
        source_keywords: list[str] | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        page_size = 100
        summary = taobao_price_db.price_summary(search=search, keywords=keywords, source_keywords=source_keywords)
        total = int(summary["itemCount"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        current_page = min(max(1, page), total_pages)
        items = taobao_price_db.list_prices(
            search=search,
            limit=page_size,
            offset=(current_page - 1) * page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            keywords=keywords,
            source_keywords=source_keywords,
        )
        summary["latestScrapedAt"] = iso(summary.get("latestScrapedAt"))
        logger.info(
            "淘宝价格列表查询完成 operation=list result=success sortBy=%s sortOrder=%s "
            "searchLength=%s keywordCount=%s sourceKeywordCount=%s page=%s pageSize=%s itemCount=%s duration=%.1fms",
            sort_by,
            sort_order,
            len(search.strip()),
            len(keywords or []),
            len(source_keywords or []),
            current_page,
            page_size,
            len(items),
            (time.perf_counter() - started) * 1000,
        )
        return {
            "items": items,
            "summary": summary,
            "pagination": {
                "page": current_page,
                "pageSize": page_size,
                "total": total,
                "totalPages": total_pages,
            },
            "scrape": self.status(),
        }

    def start(self, scrape_url: str = "") -> dict[str, Any]:
        try:
            target_url = _normalize_search_url(scrape_url)
            search_keyword = _search_keyword_from_url(target_url)
        except ValueError as exc:
            logger.warning("淘宝抓取链接校验未通过 operation=validate_search_url result=rejected reason=%s", exc)
            raise bad_request(str(exc)) from exc
        auth_state = taobao_session_db.get_current_session()
        if not auth_state:
            raise bad_request("请先点击“登录淘宝”，完成登录并保存登录信息")
        with self._lock:
            if self._worker and self._worker.is_alive():
                return dict(self._status)
            job_id = uuid.uuid4().hex
            self._stop_event.clear()
            self._status = {
                "state": "running",
                "phase": "starting",
                "message": "正在启动淘宝采集浏览器",
                "jobId": job_id,
                "startedAt": iso(_utc_now()),
                "finishedAt": None,
                "productCount": 0,
                "processedCount": 0,
                "failedCount": 0,
                "itemCount": 0,
                "targetUrl": target_url,
                "searchKeyword": search_keyword,
                "deletedCount": 0,
            }
            self._worker = threading.Thread(
                target=self._run,
                args=(job_id, auth_state, target_url),
                name="taobao-price-scraper",
                daemon=True,
            )
            self._worker.start()
            return dict(self._status)

    def _run(self, job_id: str, auth_state: dict[str, Any], target_url: str = "") -> None:
        started = time.perf_counter()
        persisted_count = 0
        unavailable_deleted_count = 0
        search_keyword = ""

        def persist_price(item: dict[str, Any]) -> None:
            nonlocal persisted_count
            item_started = time.perf_counter()
            affected = taobao_price_db.upsert_price({**item, "search_keyword": search_keyword}, _utc_now())
            if affected != 1:
                raise RuntimeError("淘宝单条价格未成功写入数据库")
            persisted_count += affected
            self._update_status(itemCount=persisted_count)
            log = logger.info if persisted_count == 1 else logger.debug
            log(
                "淘宝单条价格已提交 operation=persist_price result=committed jobId=%s "
                "searchKeyword=%s url=%s itemName=%s itemCount=%s duration=%.1fms",
                job_id,
                search_keyword,
                item["product_url"],
                item["item_name"],
                persisted_count,
                (time.perf_counter() - item_started) * 1000,
            )

        def remove_unavailable_prices(product_url: str, item_names: list[str]) -> None:
            nonlocal unavailable_deleted_count
            cleanup_started = time.perf_counter()
            affected = taobao_price_db.delete_unavailable_prices(product_url, item_names, search_keyword)
            unavailable_deleted_count += affected
            logger.info(
                "淘宝无货规格旧价格清理完成 operation=delete_unavailable_prices result=success "
                "jobId=%s searchKeyword=%s url=%s unavailableCount=%s deletedCount=%s duration=%.1fms",
                job_id,
                search_keyword,
                product_url,
                len(item_names),
                affected,
                (time.perf_counter() - cleanup_started) * 1000,
            )

        logger.info("淘宝价格采集任务开始 jobId=%s", job_id)
        try:
            # 从任务自己的链接确定来源，不使用价格列表的筛选条件或商品标题猜测归属。
            search_keyword = _search_keyword_from_url(target_url)
            self._update_status(
                phase="cleaning",
                searchKeyword=search_keyword,
                message=f"正在清理关键词“{search_keyword}”今天之前的价格",
            )
            cleanup_started = time.perf_counter()
            cleanup = taobao_price_db.delete_prices_before_today(search_keyword)
            self._update_status(deletedCount=int(cleanup["deleted_count"]))
            logger.info(
                "淘宝历史价格清理完成 operation=delete_old_prices result=success "
                "jobId=%s searchKeyword=%s cutoff=%s deletedCount=%s duration=%.1fms",
                job_id,
                search_keyword,
                iso(cleanup["cutoff"]),
                cleanup["deleted_count"],
                (time.perf_counter() - cleanup_started) * 1000,
            )
            _, product_count, failed_count = TaobaoBrowserScraper(
                self._update_status,
                self._stop_event,
                auth_state,
                job_id=job_id,
                price_callback=persist_price,
                unavailable_callback=remove_unavailable_prices,
                target_url=target_url,
            ).scrape()
            # 详情全部无货属于有效结果；只有所有商品请求都失败时才按采集失败处理。
            if persisted_count <= 0 and product_count == failed_count:
                raise RuntimeError("淘宝商品详情均未解析到价格，本次未写入数据库")
            scraped_at = _utc_now()
            message = (
                f"抓取完成：{product_count - failed_count}/{product_count} 个商品，{persisted_count} 个价格项已写入"
            )
            if unavailable_deleted_count:
                message += f"，已清理 {unavailable_deleted_count} 条无货旧价格"
            self._update_status(
                state="succeeded",
                phase="completed",
                message=message,
                finishedAt=iso(scraped_at),
                productCount=product_count,
                processedCount=product_count,
                failedCount=failed_count,
                itemCount=persisted_count,
            )
            logger.info(
                "淘宝价格采集任务完成 jobId=%s productCount=%s failedCount=%s itemCount=%s duration=%.1fms",
                job_id,
                product_count,
                failed_count,
                persisted_count,
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            message = str(exc).strip() or "淘宝价格采集失败"
            if persisted_count:
                message = f"已提交的 {persisted_count} 条价格已保留；{message}"
            self._update_status(
                state="failed",
                phase="failed",
                message=message[:500],
                finishedAt=iso(_utc_now()),
            )
            logger.exception(
                "淘宝价格采集任务失败 jobId=%s searchKeyword=%s itemCount=%s duration=%.1fms",
                job_id,
                search_keyword,
                persisted_count,
                (time.perf_counter() - started) * 1000,
            )

    def stop(self) -> None:
        with self._lock:
            worker = self._worker
            if not worker or not worker.is_alive():
                return
            self._stop_event.set()
        worker.join(timeout=10)
        if worker.is_alive():
            logger.warning("淘宝价格采集线程未在应用关闭前退出")


taobao_price_service = TaobaoPriceService()
