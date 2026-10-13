from __future__ import annotations

from typing import Protocol


class DatabaseReader(Protocol):
    def fetchone(self, statement: str, params: object = None) -> dict | None: ...

    def fetchall(self, statement: str, params: object = None) -> list[dict]: ...


REQUIRED_TABLES = (
    "users",
    "admin_sessions",
    "kfc_tickets",
    "kfc_sessions",
    "products",
    "product_options",
    "taobao_product_prices",
    "taobao_search_keywords",
    "taobao_browser_sessions",
    "coupons",
    "access_links",
    "coupon_assignment_logs",
    "orders",
    "order_confirmation_logs",
    "audit_logs",
)

REQUIRED_COLUMNS = {
    "taobao_search_keywords": ("id", "keyword", "url", "created_at", "updated_at"),
    "kfc_tickets": ("kbck",),
    "product_options": (
        "product_id",
        "group_no",
        "group_name",
        "select_count",
        "option_name",
        "image_url",
        "sort",
    ),
    "taobao_product_prices": (
        "search_keyword",
        "shop_name",
        "product_url",
        "item_name",
        "price",
        "scraped_at",
    ),
    "taobao_browser_sessions": (
        "profile_name",
        "cookies",
        "origins",
        "session_storage",
        "captured_at",
    ),
    "access_links": (
        "store_code",
        "pickup_type",
        "store_image_url",
        "cart_id",
        "redemption_status",
        "redemption_payload",
        "redemption_error",
        "redemption_started_at",
        "redemption_finished_at",
    ),
    "order_confirmation_logs": (
        "result",
        "production_info",
    ),
    "orders": (
        "submit_response",
        "access_link_public_id",
        "status_code",
        "status_name",
    ),
}


def check_runtime_schema(db: DatabaseReader) -> None:
    """Validate the deployed schema without mutating it at application startup."""
    missing_tables: list[str] = []
    for table in REQUIRED_TABLES:
        row = db.fetchone("SELECT to_regclass(%s) AS table_name", (f"public.{table}",))
        if not row or not row["table_name"]:
            missing_tables.append(table)
    if missing_tables:
        joined = ", ".join(missing_tables)
        raise RuntimeError(
            f"数据库结构未升级，缺少表: {joined}。请先执行 python tools/migrate.py 或使用最新 database.sql 初始化。"
        )

    missing_columns: list[str] = []
    for table, columns in REQUIRED_COLUMNS.items():
        rows = db.fetchall(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s AND column_name = ANY(%s)""",
            (table, list(columns)),
        )
        existing = {row["column_name"] for row in rows}
        missing_columns.extend(f"{table}.{column}" for column in columns if column not in existing)
    if missing_columns:
        joined = ", ".join(missing_columns)
        raise RuntimeError(f"数据库结构未升级，缺少字段: {joined}。请先执行 python tools/migrate.py。")
