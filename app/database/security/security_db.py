from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.database import db

_LOGIN_ENTITY_TYPE = "admin_login"
_LOGIN_FAILED = "login_failed"
_LOGIN_SUCCEEDED = "login_succeeded"
_ACCESS_ENTITY_TYPE = "public_access_link"
_INVALID_ACCESS = "invalid_access_link"


def _login_key(username: str) -> str:
    return username.strip().casefold()[:128]


def login_failure_state(username: str) -> dict[str, Any]:
    key = _login_key(username)
    row = db.fetchone(
        """
        WITH last_success AS (
            SELECT MAX("createdAt") AS succeeded_at
            FROM audit_logs
            WHERE entity_type=%s AND entity_id=%s AND action=%s
        )
        SELECT COUNT(*)::int AS failure_count,
               MAX(a."createdAt") AS last_failed_at
        FROM audit_logs a
        CROSS JOIN last_success s
        WHERE a.entity_type=%s
          AND a.entity_id=%s
          AND a.action=%s
          AND (s.succeeded_at IS NULL OR a."createdAt" > s.succeeded_at)
        """,
        (
            _LOGIN_ENTITY_TYPE,
            key,
            _LOGIN_SUCCEEDED,
            _LOGIN_ENTITY_TYPE,
            key,
            _LOGIN_FAILED,
        ),
    )
    return {
        "failure_count": int(row["failure_count"] or 0),
        "last_failed_at": row.get("last_failed_at"),
    }


def record_login_failure(username: str, client_ip: str) -> dict[str, Any]:
    key = _login_key(username)
    db.execute(
        """
        INSERT INTO audit_logs
            (actor_id, action, entity_type, entity_id, details, "createdAt", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, now(), now())
        """,
        (
            (client_ip or "unknown")[:120],
            _LOGIN_FAILED,
            _LOGIN_ENTITY_TYPE,
            key,
            Jsonb({"clientIp": client_ip or "unknown"}),
        ),
    )
    return login_failure_state(username)


def record_login_success(username: str, client_ip: str) -> None:
    key = _login_key(username)
    db.execute(
        """
        INSERT INTO audit_logs
            (actor_id, action, entity_type, entity_id, details, "createdAt", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, now(), now())
        """,
        (
            (client_ip or "unknown")[:120],
            _LOGIN_SUCCEEDED,
            _LOGIN_ENTITY_TYPE,
            key,
            Jsonb({"clientIp": client_ip or "unknown"}),
        ),
    )


def access_link_exists(access_id: str) -> bool:
    return bool(db.fetchone("SELECT 1 FROM access_links WHERE public_id=%s LIMIT 1", (access_id,)))


def record_invalid_access_link(access_id: str, client_ip: str) -> int:
    db.execute(
        """
        INSERT INTO audit_logs
            (actor_id, action, entity_type, entity_id, details, "createdAt", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, now(), now())
        """,
        (
            (client_ip or "unknown")[:120],
            _INVALID_ACCESS,
            _ACCESS_ENTITY_TYPE,
            (access_id or "")[:128],
            Jsonb({"clientIp": client_ip or "unknown"}),
        ),
    )
    return invalid_access_link_count()


def invalid_access_link_count() -> int:
    row = db.fetchone(
        """
        SELECT COUNT(*)::int AS n
        FROM audit_logs
        WHERE entity_type=%s
          AND action=%s
          AND "createdAt" >= date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'
          AND "createdAt" < (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '1 day') AT TIME ZONE 'Asia/Shanghai'
        """,
        (_ACCESS_ENTITY_TYPE, _INVALID_ACCESS),
    )
    return int(row["n"] or 0)


def invalid_access_link_threshold_exceeded(threshold: int) -> bool:
    if threshold < 0:
        return True
    row = db.fetchone(
        """
        SELECT EXISTS(
            SELECT 1
            FROM audit_logs
            WHERE entity_type=%s
              AND action=%s
              AND "createdAt" >= date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'
              AND "createdAt" < (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') + interval '1 day') AT TIME ZONE 'Asia/Shanghai'
            OFFSET %s
            LIMIT 1
        ) AS exceeded
        """,
        (_ACCESS_ENTITY_TYPE, _INVALID_ACCESS, threshold),
    )
    return bool(row["exceeded"])
