"""淘宝搜索配置的持久化，时间戳由数据库统一生成。"""

from __future__ import annotations

from typing import Any

from app.database import db


def list_keywords(search: str = "") -> list[dict[str, Any]]:
    # 按字面包含查询，避免关键词中的百分号、下划线被当成通配符。
    return db.fetchall(
        """SELECT * FROM taobao_search_keywords
           WHERE strpos(lower(keyword), lower(%s)) > 0 OR strpos(lower(url), lower(%s)) > 0
           ORDER BY id DESC""",
        (search.strip(), search.strip()),
    )


def get_keyword(keyword_id: int) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM taobao_search_keywords WHERE id=%s", (keyword_id,))


def create_keyword(keyword: str, url: str) -> dict[str, Any]:
    row = db.fetchone(
        "INSERT INTO taobao_search_keywords (keyword, url) VALUES (%s, %s) RETURNING *",
        (keyword, url),
    )
    if row is None:
        raise RuntimeError("淘宝搜索关键词未成功保存")
    return row


def update_keyword(keyword_id: int, keyword: str, url: str) -> dict[str, Any] | None:
    return db.fetchone(
        """UPDATE taobao_search_keywords SET keyword=%s, url=%s, updated_at=clock_timestamp()
           WHERE id=%s RETURNING *""",
        (keyword, url, keyword_id),
    )


def delete_keyword(keyword_id: int) -> int:
    return db.execute("DELETE FROM taobao_search_keywords WHERE id=%s", (keyword_id,))
