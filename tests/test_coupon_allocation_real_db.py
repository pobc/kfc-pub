from __future__ import annotations

import secrets

from app.database import db
from app.database.coupon.assignment_log_db import assign_coupon


def test_coupon_allocation_writes_real_database_rows() -> None:
    marker = secrets.token_hex(4)
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"pytest-{marker}", f"pytest-link-{marker}"),
        )
        platform = db.fetchone(
            """INSERT INTO platforms (id,name,code,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,true,'pytest',now(),now()) RETURNING id""",
            (f"pytest-{marker}", f"PYTEST{marker}"),
        )
        account = db.fetchone(
            """INSERT INTO platform_accounts (id,platform_id,name,identifier,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,true,'pytest',now(),now()) RETURNING id""",
            (platform["id"], f"pytest-{marker}", f"pytest-{marker}"),
        )
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,%s,1,1,0,now(),now(),now()) RETURNING id""",
            (f"pytest-{marker}", platform["id"], account["id"], product["id"]),
        )
        code = f"PYTEST-{marker}"
        db.execute(
            """INSERT INTO coupons
               (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,imported_at,last_seen_at,"createdAt","updatedAt")
               VALUES (%s,'wait',%s,'AVAILABLE',%s,%s,%s,%s,now(),now(),now(),now())""",
            (code, f"pytest-{marker}", platform["id"], account["id"], product["id"], batch["id"]),
        )
        public_id = secrets.token_hex(8)
        link = db.fetchone(
            """INSERT INTO access_links
               (public_id,status,product_id,platform_id,account_id,internal_locked_at,"createdAt","updatedAt")
               VALUES (%s,'UNUSED',%s,%s,%s,now(),now(),now()) RETURNING id""",
            (public_id, product["id"], platform["id"], account["id"]),
        )
    result = assign_coupon(public_id, "127.0.0.1")
    saved = db.fetchone("SELECT coupon_code FROM access_links WHERE id=%s", (link["id"],))
    log = db.fetchone("SELECT id FROM coupon_assignment_logs WHERE access_link_id=%s", (link["id"],))
    assert saved["coupon_code"] == code
    assert log is not None
    assert result["couponId"]
