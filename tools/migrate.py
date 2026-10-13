from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings

MIGRATIONS_DIR = ROOT / "migrations"


def _sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def migrate() -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    engine = create_engine(_sqlalchemy_database_url(settings().database_url), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                     version varchar(120) PRIMARY KEY,
                     applied_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )

        with engine.connect() as conn:
            applied = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations").fetchall()}

        for path in files:
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(sql)
                    conn.exec_driver_sql(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, now())",
                        (path.name,),
                    )
            except Exception:
                print(f"migration failed: {path.name}", flush=True)
                raise
            print(f"migration applied: {path.name}", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate()
