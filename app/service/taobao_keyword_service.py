"""管理淘宝搜索配置；删除配置不影响已采集的价格。"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.database.price import taobao_keyword_db
from app.errors import bad_request, not_found
from app.service.taobao_price_scraper import _normalize_search_url
from app.utils import iso

logger = logging.getLogger("kfc.taobao_keyword")


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "keyword": row["keyword"],
        "url": row["url"],
        "createdAt": iso(row["created_at"]),
        "updatedAt": iso(row["updated_at"]),
    }


def _require_row(row: dict[str, Any] | None, keyword_id: int) -> dict[str, Any]:
    if row is None:
        logger.warning("淘宝搜索配置不存在 operation=lookup result=not_found keywordId=%s", keyword_id)
        raise not_found("淘宝搜索关键词不存在或已删除")
    return row


def list_keywords(search: str = "") -> dict[str, Any]:
    started = time.perf_counter()
    items = [_view(row) for row in taobao_keyword_db.list_keywords(search)]
    logger.info(
        "淘宝搜索配置查询完成 operation=list result=success count=%s duration=%.1fms",
        len(items),
        (time.perf_counter() - started) * 1000,
    )
    return {"items": items}


def get_keyword(keyword_id: int) -> dict[str, Any]:
    return _view(_require_row(taobao_keyword_db.get_keyword(keyword_id), keyword_id))


def save_keyword(keyword: str, url: str, keyword_id: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    keyword, url = keyword.strip(), url.strip()
    try:
        if not keyword or len(keyword) > 200:
            raise ValueError("搜索关键词须为 1 至 200 个字符")
        if not url or len(url) > 2000:
            raise ValueError("淘宝搜索链接须为 1 至 2000 个字符")
        # 保存原始链接，实际抓取时仍由采集服务统一固定为第一页。
        _normalize_search_url(url)
    except ValueError as exc:
        logger.warning("淘宝搜索配置校验失败 operation=save result=rejected keywordId=%s reason=%s", keyword_id, exc)
        raise bad_request(str(exc)) from exc
    if keyword_id is None:
        row = taobao_keyword_db.create_keyword(keyword, url)
    else:
        row = _require_row(taobao_keyword_db.update_keyword(keyword_id, keyword, url), keyword_id)
    logger.info(
        "淘宝搜索配置保存完成 operation=%s result=success keywordId=%s duration=%.1fms",
        "create" if keyword_id is None else "update",
        row["id"],
        (time.perf_counter() - started) * 1000,
    )
    return _view(row)


def delete_keyword(keyword_id: int) -> dict[str, Any]:
    started = time.perf_counter()
    if not taobao_keyword_db.delete_keyword(keyword_id):
        _require_row(None, keyword_id)
    logger.info(
        "淘宝搜索配置删除完成 operation=delete result=success keywordId=%s duration=%.1fms",
        keyword_id,
        (time.perf_counter() - started) * 1000,
    )
    return {"deleted": True, "id": keyword_id}
