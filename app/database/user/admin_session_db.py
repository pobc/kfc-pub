from __future__ import annotations

from typing import Any

from app.database import db


def create_admin_session(*, user_id: str, token_hash: str, ttl_minutes: int) -> dict[str, Any]:
    with db.transaction():
        db.execute("DELETE FROM admin_sessions WHERE expires_at <= now()")
        return db.fetchone(
            """INSERT INTO admin_sessions (user_id, token_hash, expires_at, created_at, last_seen_at)
               VALUES (%s, %s, now() + (%s * interval '1 minute'), now(), now())
               RETURNING *""",
            (user_id, token_hash, ttl_minutes),
        )


def get_valid_admin_session(token_hash: str) -> dict[str, Any] | None:
    with db.transaction():
        db.execute("DELETE FROM admin_sessions WHERE expires_at <= now()")
        return db.fetchone(
            """UPDATE admin_sessions
               SET last_seen_at=now()
               WHERE token_hash=%s AND expires_at>now()
               RETURNING *""",
            (token_hash,),
        )


def delete_admin_session(token_hash: str) -> None:
    db.execute("DELETE FROM admin_sessions WHERE token_hash=%s", (token_hash,))
