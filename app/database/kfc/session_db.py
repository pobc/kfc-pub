from __future__ import annotations

from typing import Any

from app.database import db

SESSION_VALID_MINUTES = 10


def get_valid_kfc_session(*, phone: str) -> dict[str, Any] | None:
    phone = phone.strip()
    if not phone:
        return None
    with db.transaction():
        db.execute("DELETE FROM kfc_sessions WHERE expires_at <= now()")
        return db.fetchone(
            """SELECT * FROM kfc_sessions
               WHERE phone = %s
                 AND expires_at > now()
               LIMIT 1""",
            (phone,),
        )


def save_kfc_session(*, phone: str, session_id: str) -> dict[str, Any]:
    phone = phone.strip()
    session_id = session_id.strip()
    if not phone:
        raise ValueError("phone 不能为空")
    if not session_id:
        raise ValueError("session_id 不能为空")

    return db.fetchone(
        """INSERT INTO kfc_sessions
           (phone, session_id, expires_at, "createdAt", "updatedAt")
           VALUES (%s, %s, now() + interval '10 minutes', now(), now())
           ON CONFLICT (phone) DO UPDATE SET
             session_id = EXCLUDED.session_id,
             expires_at = EXCLUDED.expires_at,
             "updatedAt" = now()
           RETURNING *""",
        (phone, session_id),
    )


def delete_kfc_session(*, phone: str) -> None:
    phone = phone.strip()
    if not phone:
        return
    db.execute("DELETE FROM kfc_sessions WHERE phone=%s", (phone,))
