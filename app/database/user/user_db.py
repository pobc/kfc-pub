from __future__ import annotations

from typing import Any

from app.database import db


def find_user(username: str) -> dict[str, Any] | None:
    return db.fetchone(
        "SELECT id, username, password_hash, enabled FROM users WHERE username = %s LIMIT 1",
        (username,),
    )


def touch_last_login(user_id: Any) -> None:
    db.execute(
        'UPDATE users SET last_login_at = now(), "updatedAt" = now() WHERE id = %s',
        (user_id,),
    )
