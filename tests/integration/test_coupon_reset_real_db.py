from __future__ import annotations

import secrets

from app.database import db
from app.database.coupon.coupon_db import reset_coupon


def test_reset_coupon_changes_local_status_to_wait() -> None:
    marker = secrets.token_hex(4)
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products
               (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"coupon-reset-{marker}", f"coupon-reset-link-{marker}"),
        )
        platform = db.fetchone(
            """INSERT INTO platforms (id,name,code,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,true,'pytest',now(),now()) RETURNING id""",
            (f"coupon-reset-{marker}", f"RESET{marker}"),
        )
        account = db.fetchone(
            """INSERT INTO platform_accounts (id,platform_id,name,identifier,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,true,'pytest',now(),now()) RETURNING id""",
            (platform["id"], f"coupon-reset-{marker}", f"coupon-reset-{marker}"),
        )
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches
               (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,%s,1,1,0,now(),now(),now()) RETURNING id""",
            (f"coupon-reset-{marker}", platform["id"], account["id"], product["id"]),
        )
        coupon = db.fetchone(
            """INSERT INTO coupons
               (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,imported_at,last_seen_at,"createdAt","updatedAt")
               VALUES (%s,'assigned','pytest','AVAILABLE',%s,%s,%s,%s,now(),now(),now(),now()) RETURNING id""",
            (f"RESET-{marker}", platform["id"], account["id"], product["id"], batch["id"]),
        )

    result = reset_coupon(str(coupon["id"]))
    saved = db.fetchone("SELECT local_status FROM coupons WHERE id=%s", (coupon["id"],))

    assert result["localStatus"] == "wait"
    assert saved["local_status"] == "wait"
