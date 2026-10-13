from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import QueuePool

from app.config import settings
from app.database.schema import check_runtime_schema


def _sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def _row_dict(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


class Database:
    def __init__(self) -> None:
        self._engine = create_engine(
            _sqlalchemy_database_url(settings().database_url),
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=0,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        self._transaction_connection: ContextVar[Connection | None] = ContextVar(
            "database_transaction_connection",
            default=None,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def open(self) -> None:
        self.fetchone("SELECT 1 AS n")
        check_runtime_schema(self)

    def close(self) -> None:
        self._engine.dispose()

    def fetchone(self, statement: str, params: Any = None) -> dict[str, Any] | None:
        connection = self._transaction_connection.get()
        if connection is not None:
            return _row_dict(self._execute(connection, statement, params).fetchone())
        with self._engine.begin() as connection:
            return _row_dict(self._execute(connection, statement, params).fetchone())

    def fetchall(self, statement: str, params: Any = None) -> list[dict[str, Any]]:
        connection = self._transaction_connection.get()
        if connection is not None:
            rows = self._execute(connection, statement, params).fetchall()
        else:
            with self._engine.begin() as connection:
                rows = self._execute(connection, statement, params).fetchall()
        return [dict(row._mapping) for row in rows]

    def fetchmany(self, statement: str, params: Any = None, *, size: int | None = None) -> list[dict[str, Any]]:
        connection = self._transaction_connection.get()
        if connection is not None:
            result = self._execute(connection, statement, params)
            rows = result.fetchmany(size) if size is not None else result.fetchmany()
        else:
            with self._engine.begin() as connection:
                result = self._execute(connection, statement, params)
                rows = result.fetchmany(size) if size is not None else result.fetchmany()
        return [dict(row._mapping) for row in rows]

    def execute(self, statement: str, params: Any = None) -> int:
        connection = self._transaction_connection.get()
        if connection is not None:
            return int(self._execute(connection, statement, params).rowcount)
        with self._engine.begin() as connection:
            return int(self._execute(connection, statement, params).rowcount)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        current = self._transaction_connection.get()
        if current is not None:
            with current.begin_nested():
                yield
            return

        with self._engine.begin() as connection:
            token = self._transaction_connection.set(connection)
            try:
                yield
            finally:
                self._transaction_connection.reset(token)

    @staticmethod
    def _execute(connection: Connection, statement: str, params: Any = None):
        return connection.exec_driver_sql(statement, params if params is not None else ())


db = Database()
