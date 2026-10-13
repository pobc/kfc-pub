from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from app.database import db
from app.database.security import security_db
from app.service.security_service import login_retry_after_seconds


def test_admin_login_failure_lock_tiers() -> None:
    username = f"security-{uuid.uuid4().hex}"
    login_key = username.casefold()[:128]
    try:
        for _ in range(3):
            security_db.record_login_failure(username, "127.0.0.1")
        state = security_db.login_failure_state(username)
        assert state["failure_count"] == 3
        assert 55 <= login_retry_after_seconds(state) <= 60

        for _ in range(3):
            security_db.record_login_failure(username, "127.0.0.1")
        state = security_db.login_failure_state(username)
        assert state["failure_count"] == 6
        assert 295 <= login_retry_after_seconds(state) <= 300

        for _ in range(4):
            security_db.record_login_failure(username, "127.0.0.1")
        state = security_db.login_failure_state(username)
        assert state["failure_count"] == 10
        assert 3595 <= login_retry_after_seconds(state) <= 3600

        security_db.record_login_success(username, "127.0.0.1")
        state = security_db.login_failure_state(username)
        assert state["failure_count"] == 0
        assert login_retry_after_seconds(state) == 0
    finally:
        db.execute(
            "DELETE FROM audit_logs WHERE entity_type='admin_login' AND entity_id=%s",
            (login_key,),
        )


def test_invalid_access_link_threshold_is_strictly_exceeded() -> None:
    before = security_db.invalid_access_link_count()
    access_ids = [f"bad-{uuid.uuid4().hex}" for _ in range(3)]
    try:
        assert not security_db.invalid_access_link_threshold_exceeded(before + 3)
        for access_id in access_ids:
            security_db.record_invalid_access_link(access_id, "127.0.0.1")
        assert security_db.invalid_access_link_count() == before + 3
        assert not security_db.invalid_access_link_threshold_exceeded(before + 3)
        assert security_db.invalid_access_link_threshold_exceeded(before + 2)
    finally:
        db.execute(
            """
            DELETE FROM audit_logs
            WHERE entity_type='public_access_link'
              AND action='invalid_access_link'
              AND entity_id = ANY(%s)
            """,
            ([access_id[:128] for access_id in access_ids],),
        )


def test_invalid_access_link_count_ignores_previous_china_day() -> None:
    before = security_db.invalid_access_link_count()
    access_id = f"old-{uuid.uuid4().hex}"
    try:
        db.execute(
            """
            INSERT INTO audit_logs
                (actor_id, action, entity_type, entity_id, details, "createdAt", "updatedAt")
            VALUES (
                %s,
                'invalid_access_link',
                'public_access_link',
                %s,
                %s,
                (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') - interval '1 second') AT TIME ZONE 'Asia/Shanghai',
                now()
            )
            """,
            ("127.0.0.1", access_id, Jsonb({"clientIp": "127.0.0.1"})),
        )
        assert security_db.invalid_access_link_count() == before
    finally:
        db.execute(
            """
            DELETE FROM audit_logs
            WHERE entity_type='public_access_link'
              AND action='invalid_access_link'
              AND entity_id=%s
            """,
            (access_id,),
        )
