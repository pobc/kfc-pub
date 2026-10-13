from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.utils import iso

DEFAULT_PROFILE_NAME = "default"


def _status_view(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "saved": False,
            "capturedAt": None,
            "cookieCount": 0,
            "originCount": 0,
            "sessionStorageCount": 0,
        }
    cookies = row.get("cookies") if isinstance(row.get("cookies"), list) else []
    origins = row.get("origins") if isinstance(row.get("origins"), list) else []
    session_storage = row.get("session_storage") if isinstance(row.get("session_storage"), dict) else {}
    return {
        "saved": True,
        "capturedAt": iso(row.get("captured_at")),
        "cookieCount": len(cookies),
        "originCount": len(origins),
        "sessionStorageCount": sum(len(values) for values in session_storage.values() if isinstance(values, list)),
        "capturedUrl": row.get("captured_url") or "",
        "capturedTitle": row.get("captured_title") or "",
    }


def get_current_session() -> dict[str, Any] | None:
    return db.fetchone(
        """SELECT *
           FROM taobao_browser_sessions
           WHERE profile_name=%s
           LIMIT 1""",
        (DEFAULT_PROFILE_NAME,),
    )


def session_status() -> dict[str, Any]:
    return _status_view(get_current_session())


def save_session(snapshot: dict[str, Any], captured_at: datetime) -> dict[str, Any]:
    cookies = snapshot.get("cookies") if isinstance(snapshot.get("cookies"), list) else []
    origins = snapshot.get("origins") if isinstance(snapshot.get("origins"), list) else []
    session_storage = snapshot.get("sessionStorage") if isinstance(snapshot.get("sessionStorage"), dict) else {}
    row = db.fetchone(
        """INSERT INTO taobao_browser_sessions
             (profile_name, cookies, origins, session_storage, captured_url, captured_title,
              captured_at, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
           ON CONFLICT (profile_name) DO UPDATE SET
             cookies=EXCLUDED.cookies,
             origins=EXCLUDED.origins,
             session_storage=EXCLUDED.session_storage,
             captured_url=EXCLUDED.captured_url,
             captured_title=EXCLUDED.captured_title,
             captured_at=EXCLUDED.captured_at,
             updated_at=now()
           RETURNING *""",
        (
            DEFAULT_PROFILE_NAME,
            Jsonb(cookies),
            Jsonb(origins),
            Jsonb(session_storage),
            str(snapshot.get("url") or "")[:2000],
            str(snapshot.get("title") or "")[:500],
            captured_at,
        ),
    )
    if not row:
        raise RuntimeError("淘宝登录信息保存失败")
    return _status_view(row)
