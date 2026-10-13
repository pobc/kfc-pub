from __future__ import annotations

from typing import Any

from app.database import db


def get_enabled_kfc_account_identifier(account_id: str) -> str | None:
    row = db.fetchone(
        """SELECT pa.identifier
           FROM platform_accounts pa
           JOIN platforms p ON p.id=pa.platform_id
           WHERE pa.id=%s AND pa.enabled=true AND p.enabled=true AND p.code='KFC'
           LIMIT 1""",
        (account_id,),
    )
    return str(row["identifier"] or "").strip() if row else None


def ensure_kfc_source(ticket: dict[str, Any] | None = None) -> tuple[str, str]:
    platform = db.fetchone("SELECT * FROM platforms WHERE code = 'KFC' LIMIT 1")
    if not platform:
        platform = db.fetchone(
            """INSERT INTO platforms (id, name, code, enabled, remark, "createdAt", "updatedAt")
               VALUES (gen_random_uuid(), 'KFC', 'KFC', true, '系统内部来源，不在后台提供平台管理', now(), now()) RETURNING *"""
        )
    elif not platform["enabled"]:
        platform = db.fetchone(
            'UPDATE platforms SET enabled = true, "updatedAt" = now() WHERE id = %s RETURNING *',
            (platform["id"],),
        )

    phone = str(ticket.get("phone") or "").strip() if ticket else ""
    if ticket and phone:
        identifier = f"kfc-phone:{phone}"
        name = phone
    elif ticket:
        identifier = f"kfc-session:{ticket['id']}"
        name = ticket["name"]
    else:
        identifier = "kfc-manual"
        name = "KFC 手动导入"

    account = db.fetchone(
        "SELECT * FROM platform_accounts WHERE platform_id = %s AND identifier = %s LIMIT 1",
        (platform["id"], identifier),
    )

    if not account and ticket and phone:
        legacy = db.fetchone(
            """SELECT pa.*
               FROM platform_accounts pa
               JOIN kfc_tickets kt ON pa.identifier = 'kfc-session:' || kt.id::text
               WHERE pa.platform_id=%s AND kt.phone=%s
               ORDER BY pa."updatedAt" DESC, pa."createdAt" DESC
               LIMIT 1""",
            (platform["id"], phone),
        )
        if legacy:
            account = db.fetchone(
                """UPDATE platform_accounts
                   SET identifier=%s, name=%s, enabled=true, "updatedAt"=now()
                   WHERE id=%s RETURNING *""",
                (identifier, name, legacy["id"]),
            )

    if not account:
        account = db.fetchone(
            """INSERT INTO platform_accounts (id, platform_id, name, identifier, enabled, remark, "createdAt", "updatedAt")
               VALUES (gen_random_uuid(), %s, %s, %s, true, '系统内部卡券来源', now(), now()) RETURNING *""",
            (platform["id"], name, identifier),
        )
    elif not account["enabled"] or account["name"] != name:
        account = db.fetchone(
            'UPDATE platform_accounts SET enabled = true, name = %s, "updatedAt" = now() WHERE id = %s RETURNING *',
            (name, account["id"]),
        )
    return str(platform["id"]), str(account["id"])
