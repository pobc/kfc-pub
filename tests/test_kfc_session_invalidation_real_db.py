from __future__ import annotations

import secrets

from app.database import db
from app.database import kfc_sessions, kfc_tickets


def test_ticket_or_device_change_invalidates_cached_kfc_session() -> None:
    marker = secrets.token_hex(4)
    phone = f"139{int(marker, 16) % 100000000:08d}"
    ticket = db.fetchone(
        """INSERT INTO kfc_tickets
           (id,name,ticket,user_code,phone,open_id,device_id,cookie,auth_state,enabled,remark,"createdAt","updatedAt")
           VALUES (gen_random_uuid(),%s,%s,%s,%s,'',%s,'','{}'::jsonb,true,'pytest',now(),now())
           RETURNING id""",
        (phone, f"ticket-{marker}", f"user-{marker}", phone, f"device-{marker}"),
    )

    kfc_sessions.save_kfc_session(phone=phone, session_id=f"session-{marker}")
    assert kfc_sessions.get_valid_kfc_session(phone=phone) is not None

    kfc_tickets.update_kfc_session(str(ticket["id"]), {"ticket": f"ticket-new-{marker}"})
    assert kfc_sessions.get_valid_kfc_session(phone=phone) is None

    kfc_sessions.save_kfc_session(phone=phone, session_id=f"session-new-{marker}")
    kfc_tickets.update_kfc_session(str(ticket["id"]), {"deviceId": f"device-new-{marker}"})
    assert kfc_sessions.get_valid_kfc_session(phone=phone) is None
