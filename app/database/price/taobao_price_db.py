from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.database import db
from app.serializers import taobao_price_view

_SORT_COLUMNS = {
    "shopName": "shop_name",
    "productUrl": "product_url",
    "itemName": "item_name",
    "price": "price",
    "scrapedAt": "scraped_at",
}


def _price_filter(
    search: str, keywords: list[str] | None, source_keywords: list[str] | None = None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    search_words = list(dict.fromkeys(search.split()))
    if search_words:
        # 左侧搜索框只匹配商品项，空白分词后取并集；空输入不增加筛选条件。
        clauses.append(
            """EXISTS (SELECT 1 FROM unnest(%s::text[]) AS search_terms(word)
               WHERE strpos(lower(item_name), lower(search_terms.word)) > 0)"""
        )
        parameters.append(search_words)
    normalized_keywords = list(dict.fromkeys(value.strip() for value in (keywords or []) if value.strip()))
    if normalized_keywords:
        # 手动输入的关键词只匹配商品项，不能由复选框的来源条件代替命中。
        clauses.append(
            """EXISTS (SELECT 1 FROM unnest(%s::text[]) AS filters(keyword)
               WHERE strpos(lower(item_name), lower(filters.keyword)) > 0)"""
        )
        parameters.append(normalized_keywords)
    sources = list(dict.fromkeys(value.strip() for value in (source_keywords or []) if value.strip()))
    if sources:
        # 复选框表示明确的采集来源，多选取并集，再与商品项文字条件取交集。
        clauses.append("search_keyword = ANY(%s::text[])")
        parameters.append(sources)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def list_prices(
    search: str = "",
    limit: int | None = None,
    offset: int = 0,
    sort_by: str = "scrapedAt",
    sort_order: str = "desc",
    keywords: list[str] | None = None,
    source_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    # 排序列和方向必须来自白名单，不能把接口参数直接拼入 SQL。
    order_column = _SORT_COLUMNS.get(sort_by, "scraped_at")
    order_direction = "ASC" if sort_order == "asc" else "DESC"
    where_clause, parameters = _price_filter(search, keywords, source_keywords)
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT %s OFFSET %s"
        parameters.extend((max(1, min(limit, 2000)), max(0, offset)))
    rows = db.fetchall(
        f"""SELECT *
            FROM taobao_product_prices
            {where_clause}
            ORDER BY {order_column} {order_direction}, id {order_direction}
            {limit_clause}""",
        tuple(parameters),
    )
    return [taobao_price_view(row) for row in rows]


def price_summary(
    search: str = "", keywords: list[str] | None = None, source_keywords: list[str] | None = None
) -> dict[str, Any]:
    where_clause, parameters = _price_filter(search, keywords, source_keywords)
    row = (
        db.fetchone(
            f"""SELECT COUNT(*) AS item_count,
                  COUNT(DISTINCT product_url) AS product_count,
                  MAX(scraped_at) AS latest_scraped_at
           FROM taobao_product_prices {where_clause}""",
            tuple(parameters),
        )
        or {}
    )
    return {
        "itemCount": int(row.get("item_count") or 0),
        "productCount": int(row.get("product_count") or 0),
        "latestScrapedAt": row.get("latest_scraped_at"),
    }


def delete_prices_before_today(search_keyword: str) -> dict[str, Any]:
    """只清理指定来源关键词的历史价格，其他关键词与未知来源的数据必须保留。"""
    keyword = search_keyword.strip()
    if not keyword or len(keyword) > 200:
        raise ValueError("清理价格必须指定 1 至 200 个字符的搜索关键词")
    row = db.fetchone(
        """WITH boundary AS (
             SELECT date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')
                    AT TIME ZONE 'Asia/Shanghai' AS cutoff
           ), deleted AS (
             DELETE FROM taobao_product_prices USING boundary
             WHERE search_keyword = %s AND scraped_at < boundary.cutoff RETURNING id
           )
           SELECT (SELECT cutoff FROM boundary) AS cutoff, COUNT(*) AS deleted_count FROM deleted""",
        (keyword,),
    )
    if row is None:
        raise RuntimeError("淘宝历史价格清理未返回结果")
    return row


def upsert_price(item: dict[str, Any], scraped_at: datetime) -> int:
    """单条写入价格，使抓取中断时已完成的数据仍然保留。"""
    product_url = str(item["product_url"]).strip()
    item_name = str(item["item_name"]).strip()
    shop_name = str(item["shop_name"]).strip()
    # 未提供来源的旧调用仍可入库，但不能把未知来源记录归到任何关键词下。
    search_keyword = str(item.get("search_keyword") or "").strip()
    if len(search_keyword) > 200:
        raise ValueError("搜索关键词不能超过 200 个字符")
    price = Decimal(str(item["price"])).quantize(Decimal("0.01"))
    if not product_url or not item_name or not shop_name:
        raise ValueError("淘宝价格写入字段不能为空")
    if price < 0:
        raise ValueError("淘宝商品价格不能小于零")
    return db.execute(
        """INSERT INTO taobao_product_prices
             (shop_name, product_url, item_name, price, scraped_at, search_keyword, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, now(), now())
           ON CONFLICT (product_url, item_name, search_keyword) DO UPDATE SET
             shop_name = EXCLUDED.shop_name,
             price = EXCLUDED.price,
             scraped_at = EXCLUDED.scraped_at,
             updated_at = now()""",
        (shop_name[:255], product_url[:1000], item_name[:500], price, scraped_at, search_keyword),
    )


def delete_unavailable_prices(product_url: str, item_names: list[str], search_keyword: str) -> int:
    """仅清理本次详情明确无货的规格，不能跨商品或跨采集来源删除。"""
    if not product_url.strip() or not search_keyword.strip() or len(search_keyword) > 200:
        raise ValueError("清理无货价格必须指定商品链接和有效搜索关键词")
    names = list(dict.fromkeys(name.strip()[:500] for name in item_names if name.strip()))
    if not names:
        return 0
    return db.execute(
        """DELETE FROM taobao_product_prices
           WHERE product_url=%s AND search_keyword=%s AND item_name=ANY(%s::text[])""",
        (product_url.strip()[:1000], search_keyword.strip(), names),
    )


def upsert_prices(items: list[dict[str, Any]], scraped_at: datetime) -> int:
    if not items:
        return 0

    affected = 0
    with db.transaction():
        for item in items:
            affected += upsert_price(item, scraped_at)
    return affected
