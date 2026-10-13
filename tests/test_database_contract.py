from __future__ import annotations

from app.database import db


def test_required_tables_exist_in_real_postgresql() -> None:
    required_tables = [
        "users",
        "products",
        "product_options",
        "coupons",
        "access_links",
        "coupon_assignment_logs",
        "kfc_tickets",
        "orders",
    ]
    rows = db.fetchall(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema='public' AND table_name = ANY(%s)""",
        (required_tables,),
    )
    names = {row["table_name"] for row in rows}
    assert names == set(required_tables)


def test_products_has_no_enabled_column() -> None:
    row = db.fetchone(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='products' AND column_name='enabled'"""
    )
    assert row is None


def test_product_options_is_current_option_config() -> None:
    columns = ["id", "product_id", "group_no", "group_name", "option_name", "image_url", "sort"]
    rows = db.fetchall(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name='product_options'
             AND column_name = ANY(%s)""",
        (columns,),
    )
    assert {row["column_name"] for row in rows} == set(columns)

    old_groups = db.fetchone("SELECT to_regclass('public.product_groups') AS table_name")
    assert old_groups["table_name"] is None

    old_items = db.fetchone("SELECT to_regclass('public.product_group_items') AS table_name")
    assert old_items["table_name"] is None

    old_link_column = db.fetchone(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='access_links' AND column_name='product_group_id'"""
    )
    assert old_link_column is None


def test_kfc_tickets_has_device_id_column() -> None:
    row = db.fetchone(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='kfc_tickets' AND column_name='device_id'"""
    )
    assert row is not None


def test_access_links_public_id_is_unique() -> None:
    row = db.fetchone(
        """SELECT 1
           FROM pg_index i
           JOIN pg_class t ON t.oid=i.indrelid
           JOIN pg_namespace n ON n.oid=t.relnamespace
           JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=i.indkey[0]
           WHERE n.nspname='public'
             AND t.relname='access_links'
             AND i.indisunique
             AND i.indpred IS NULL
             AND i.indnkeyatts=1
             AND a.attname='public_id'
           LIMIT 1"""
    )
    assert row is not None


def test_redemption_job_fields_live_on_access_links() -> None:
    fields = [
        "id",
        "public_id",
        "status",
        "store_code",
        "redemption_status",
        "redemption_payload",
        "redemption_error",
        "redemption_started_at",
        "redemption_finished_at",
        "createdAt",
        "updatedAt",
    ]
    rows = db.fetchall(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name='access_links'
             AND column_name = ANY(%s)""",
        (fields,),
    )
    assert {row["column_name"] for row in rows} == set(fields)

    old_table = db.fetchone("SELECT to_regclass('public.coupon_application_attempts') AS table_name")
    assert old_table["table_name"] is None

    order_column = db.fetchone(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='access_links' AND column_name='order_id'"""
    )
    assert order_column is None


def test_orders_uses_access_link_public_id() -> None:
    fields = [
        "id",
        "access_link_public_id",
        "external_order_id",
        "stage",
        "status_code",
        "status_name",
        "createdAt",
        "updatedAt",
    ]
    rows = db.fetchall(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name='orders'
             AND column_name = ANY(%s)""",
        (fields,),
    )
    assert {row["column_name"] for row in rows} == set(fields)

    old_column = db.fetchone(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='orders' AND column_name='access_link_id'"""
    )
    assert old_column is None

    unique_constraint = db.fetchone(
        """SELECT 1
           FROM information_schema.table_constraints tc
           JOIN information_schema.constraint_column_usage ccu
             ON ccu.constraint_schema=tc.constraint_schema
            AND ccu.constraint_name=tc.constraint_name
           WHERE tc.table_schema='public'
             AND tc.table_name='orders'
             AND tc.constraint_type='UNIQUE'
             AND ccu.column_name='access_link_public_id'"""
    )
    assert unique_constraint is None

    legacy_columns = db.fetchall(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema='public' AND table_name='orders'
             AND column_name = ANY(%s)""",
        (["status", "created_at", "updated_at"],),
    )
    assert legacy_columns == []


def test_database_diagnostic_queries_match_runtime_schema() -> None:
    access_links = db.fetchall(
        """SELECT l.id, l.public_id, l.status, l.redemption_status, l.redemption_error,
                  l.store_code, o.id AS order_id,
                  l."createdAt" AS created_at, l."updatedAt" AS updated_at
           FROM access_links l
           LEFT JOIN LATERAL (
               SELECT id
               FROM orders
               WHERE access_link_public_id = l.public_id
               ORDER BY "createdAt" DESC
               LIMIT 1
           ) o ON true
           WHERE false"""
    )
    assert access_links == []

    orders = db.fetchall(
        """SELECT id, stage AS status,
                  "createdAt" AS created_at, "updatedAt" AS updated_at
           FROM orders
           WHERE false
           ORDER BY id DESC"""
    )
    assert orders == []

    coupons = db.fetchall(
        """SELECT c.id, c.account_id, c.product_id, c.kfc_status, c.local_status,
                  p.name AS product_name,
                  EXISTS(
                      SELECT 1 FROM product_options o WHERE o.product_id = c.product_id
                  ) AS has_options
           FROM coupons c
           LEFT JOIN products p ON p.id = c.product_id
           WHERE false"""
    )
    assert coupons == []
