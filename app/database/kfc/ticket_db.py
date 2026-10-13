from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.database import db
from app.database.platform.platform_db import ensure_kfc_source
from app.errors import not_found
from app.serializers import batch_view, coupon_view, ticket_view
from app.utils import iso


_AUTH_COLUMNS = "id,name,ticket,user_code,phone,open_id,device_id,cookie,kbck,auth_state,captured_at,\"createdAt\",\"updatedAt\""


def _auth_context_changed(current: dict[str, Any], *, phone: str, ticket: str, device_id: str) -> bool:
    return (
        str(current.get("phone") or "").strip() != phone.strip()
        or str(current.get("ticket") or "").strip() != ticket.strip()
        or str(current.get("device_id") or "").strip() != device_id.strip()
    )


def _invalidate_cached_sessions(*phones: str) -> None:
    values = sorted({phone.strip() for phone in phones if phone and phone.strip()})
    if values:
        db.execute("DELETE FROM kfc_sessions WHERE phone = ANY(%s)", (values,))


def count_kfc_tickets() -> int:
    return db.fetchone("SELECT COUNT(*) AS n FROM kfc_tickets")["n"]


def list_kfc_sessions() -> list[dict[str, Any]]:
    rows = db.fetchall('SELECT * FROM kfc_tickets ORDER BY "createdAt" DESC')
    return [ticket_view(row) for row in rows]


def create_kfc_session(data: dict[str, Any]) -> dict[str, Any]:
    phone = data.get("phone", "").strip()
    with db.transaction():
        row = db.fetchone(
            """INSERT INTO kfc_tickets
               (id, name, ticket, user_code, phone, open_id, device_id, cookie, auth_state, captured_at, enabled, last_synced_at, remark, "createdAt", "updatedAt")
               VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, NULL, true, NULL, %s, now(), now()) RETURNING *""",
            (
                data["name"].strip(),
                data["ticket"].strip(),
                data["userCode"].strip(),
                phone,
                data.get("openId", "").strip(),
                data.get("deviceId", "").strip(),
                data.get("cookie", "").strip(),
                data.get("remark", "").strip(),
            ),
        )
        _invalidate_cached_sessions(phone)
    return ticket_view(row)


def update_kfc_session(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
    with db.transaction():
        current = db.fetchone("SELECT * FROM kfc_tickets WHERE id = %s FOR UPDATE", (session_id,))
        if not current:
            raise not_found("KFC Session 不存在")
        old_phone = str(current.get("phone") or "")
        mapping = {
            "name": "name",
            "ticket": "ticket",
            "userCode": "user_code",
            "phone": "phone",
            "openId": "open_id",
            "deviceId": "device_id",
            "cookie": "cookie",
            "remark": "remark",
            "enabled": "enabled",
        }
        fields: list[str] = []
        values: list[Any] = []
        final = dict(current)
        for key, column in mapping.items():
            if key in data and data[key] is not None:
                value = data[key].strip() if isinstance(data[key], str) else data[key]
                fields.append(f"{column} = %s")
                values.append(value)
                final[column] = value
        auth_changed = _auth_context_changed(
            current,
            phone=str(final.get("phone") or ""),
            ticket=str(final.get("ticket") or ""),
            device_id=str(final.get("device_id") or ""),
        )
        if fields:
            fields.append('"updatedAt" = now()')
            values.append(session_id)
            current = db.fetchone(
                f"UPDATE kfc_tickets SET {', '.join(fields)} WHERE id = %s RETURNING *",
                tuple(values),
            )
        if auth_changed:
            _invalidate_cached_sessions(old_phone, str(final.get("phone") or ""))
    return ticket_view(current)


def delete_kfc_session(session_id: str) -> dict[str, bool]:
    with db.transaction():
        current = db.fetchone("SELECT phone FROM kfc_tickets WHERE id=%s FOR UPDATE", (session_id,))
        if not current:
            raise not_found("KFC Session 不存在")
        db.execute("DELETE FROM kfc_tickets WHERE id = %s", (session_id,))
        _invalidate_cached_sessions(str(current.get("phone") or ""))
    return {"deleted": True}


def get_enabled_kfc_session(session_id: str) -> dict[str, Any] | None:
    return db.fetchone(
        "SELECT * FROM kfc_tickets WHERE id = %s AND enabled = true",
        (session_id,),
    )


def get_latest_enabled_auth_for_phone(phone: str) -> dict[str, Any] | None:
    value = phone.strip()
    if not value:
        return None
    return db.fetchone(
        f"""SELECT {_AUTH_COLUMNS}
            FROM kfc_tickets
            WHERE enabled=true AND phone=%s
            ORDER BY captured_at DESC NULLS LAST, "updatedAt" DESC, "createdAt" DESC
            LIMIT 1""",
        (value,),
    )


def get_phone_for_ticket_id(ticket_id: str) -> str:
    row = db.fetchone("SELECT phone FROM kfc_tickets WHERE id=%s LIMIT 1", (ticket_id,))
    return str(row.get("phone") or "").strip() if row else ""


def save_ticket_kbck(ticket_id: str, kbck: str) -> None:
    value = kbck.strip()
    if value:
        db.execute(
            'UPDATE kfc_tickets SET kbck = %s, "updatedAt" = now() WHERE id = %s',
            (value, ticket_id),
        )


def upsert_auth_state(
    *,
    name: str,
    ticket: str,
    user_code: str,
    phone: str,
    open_id: str,
    device_id: str,
    cookie: str,
    auth_state: dict[str, Any],
    captured_at: datetime,
    remark: str,
) -> dict[str, Any]:
    with db.transaction():
        existing = db.fetchone(
            "SELECT * FROM kfc_tickets WHERE user_code = %s LIMIT 1 FOR UPDATE",
            (user_code,),
        )
        if existing:
            changed = _auth_context_changed(existing, phone=phone, ticket=ticket, device_id=device_id)
            row = db.fetchone(
                """UPDATE kfc_tickets SET name=%s, ticket=%s, phone=%s, open_id=%s, device_id=%s, cookie=%s,
                   auth_state=%s, captured_at=%s, enabled=true, remark=%s, "updatedAt"=now()
                   WHERE id=%s RETURNING *""",
                (
                    name.strip(),
                    ticket,
                    phone,
                    open_id,
                    device_id,
                    cookie,
                    Jsonb(auth_state),
                    captured_at,
                    remark.strip() or existing.get("remark", ""),
                    existing["id"],
                ),
            )
            if changed:
                _invalidate_cached_sessions(str(existing.get("phone") or ""), phone)
        else:
            row = db.fetchone(
                """INSERT INTO kfc_tickets
                   (id,name,ticket,user_code,phone,open_id,device_id,cookie,auth_state,captured_at,enabled,last_synced_at,remark,"createdAt","updatedAt")
                   VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,true,NULL,%s,now(),now()) RETURNING *""",
                (
                    name.strip(),
                    ticket,
                    user_code,
                    phone,
                    open_id,
                    device_id,
                    cookie,
                    Jsonb(auth_state),
                    captured_at,
                    remark.strip(),
                ),
            )
            _invalidate_cached_sessions(phone)
    return ticket_view(row)


def save_coupon_sync(
    *,
    session_id: str,
    ticket: dict[str, Any],
    coupons: list[dict[str, Any]],
    batch_name: str | None,
    synced_at: datetime,
) -> dict[str, Any]:
    with db.transaction():
        db.execute(
            'UPDATE kfc_tickets SET last_synced_at = %s, "updatedAt" = now() WHERE id = %s',
            (synced_at, session_id),
        )
        platform_id, account_id = ensure_kfc_source(ticket)

        if not coupons:
            db.execute(
                """UPDATE coupons SET kfc_status='STALE', "updatedAt"=now()
                   WHERE platform_id=%s AND account_id=%s
                     AND kfc_status='AVAILABLE' AND local_status='wait'
                     AND COALESCE(last_seen_at, '-infinity'::timestamptz) < %s""",
                (platform_id, account_id, synced_at),
            )
            return {
                "fetched": 0,
                "imported": 0,
                "updated": 0,
                "failed": 0,
                "matched": 0,
                "unclassified": 0,
                "batch": None,
                "items": [],
                "failures": [],
                "syncedAt": iso(synced_at),
            }

        products = db.fetchall('SELECT id, name FROM products ORDER BY "createdAt" ASC')
        products_by_name: dict[str, list[dict[str, Any]]] = {}
        for product in products:
            products_by_name.setdefault(product["name"].strip(), []).append(product)
        mapping_rows = db.fetchall(
            """SELECT m.kfc_product_name, p.id AS product_id
               FROM kfc_product_name_mappings m
               JOIN products p ON p.id=m.product_id"""
        )
        product_mappings = {row["kfc_product_name"].strip(): row["product_id"] for row in mapping_rows}

        name = (batch_name or "").strip() or f"KFC同步-{ticket['name']}-{synced_at.date().isoformat()}"
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches
               (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,NULL,%s,0,0,%s,now(),now()) RETURNING *""",
            (name, platform_id, account_id, len(coupons), synced_at),
        )

        items: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        seen: set[str] = set()
        inserted = updated = matched = unclassified = 0

        for index, source in enumerate(coupons, start=1):
            code = source["couponCode"].strip()
            product_name = source["productName"].strip()
            candidates = products_by_name.get(product_name, [])
            direct_product = candidates[0] if len(candidates) == 1 else None
            resolved_product_id = product_mappings.get(product_name) or (
                direct_product["id"] if direct_product else None
            )
            if not code:
                failures.append({"row": index, "couponCode": code, "reason": "卡券码为空"})
                continue
            if code in seen:
                failures.append({"row": index, "couponCode": code, "reason": "KFC 返回的卡券码重复"})
                continue
            seen.add(code)

            existing = db.fetchone(
                "SELECT * FROM coupons WHERE platform_id = %s AND coupon_code = %s",
                (platform_id, code),
            )
            created_at = source.get("createdAt")
            if existing:
                row = db.fetchone(
                    """UPDATE coupons SET product_name=%s,kfc_status=%s,
                       product_id=COALESCE(%s, product_id),account_id=%s,batch_id=%s,imported_at=%s,last_seen_at=%s,
                       "createdAt"=COALESCE(%s,"createdAt"),"updatedAt"=now()
                       WHERE id=%s RETURNING *""",
                    (
                        product_name,
                        source["couponStatus"],
                        resolved_product_id,
                        account_id,
                        batch["id"],
                        synced_at,
                        synced_at,
                        created_at,
                        existing["id"],
                    ),
                )
                updated += 1
            else:
                row = db.fetchone(
                    """INSERT INTO coupons
                       (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,imported_at,last_seen_at,"createdAt","updatedAt")
                       VALUES (%s,'wait',%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()),now()) RETURNING *""",
                    (
                        code,
                        product_name,
                        source["couponStatus"],
                        platform_id,
                        account_id,
                        resolved_product_id,
                        batch["id"],
                        synced_at,
                        synced_at,
                        created_at,
                    ),
                )
                inserted += 1
            items.append(coupon_view(row))
            matched += 1 if resolved_product_id else 0
            unclassified += 0 if resolved_product_id else 1

        db.execute(
            """UPDATE coupons SET kfc_status='STALE', "updatedAt"=now()
               WHERE platform_id=%s AND account_id=%s
                 AND kfc_status='AVAILABLE' AND local_status='wait'
                 AND COALESCE(last_seen_at, '-infinity'::timestamptz) < %s""",
            (platform_id, account_id, synced_at),
        )
        db.execute(
            'UPDATE coupon_import_batches SET success_count=%s, failed_count=%s, "updatedAt"=now() WHERE id=%s',
            (len(items), len(failures), batch["id"]),
        )
        batch = db.fetchone(
            "SELECT * FROM coupon_import_batches WHERE id=%s",
            (batch["id"],),
        )

    return {
        "batch": batch_view(batch),
        "items": items,
        "failures": failures,
        "inserted": inserted,
        "updated": updated,
        "matched": matched,
        "unclassified": unclassified,
        "fetched": len(coupons),
        "imported": inserted,
        "failed": len(failures),
        "syncedAt": iso(synced_at),
    }
