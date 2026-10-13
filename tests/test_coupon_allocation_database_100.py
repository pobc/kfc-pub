from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.database import db
from app.database.coupon.assignment_log_db import assign_coupon

TOTAL = 100
WORKERS = 10


def _prepare_rows(marker: str) -> list[str]:
    """向真实 PostgreSQL 写入 100 张测试卡券和 100 个待分配访问链接。"""
    with db.transaction():
        product = db.fetchone(
            """INSERT INTO products (id,name,description,image_url,parent_link_id,spec_link_ids,condiment_link_ids,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,'',NULL,%s,'[]'::jsonb,'[]'::jsonb,now(),now()) RETURNING id""",
            (f"allocation-100-{marker}", f"allocation-link-{marker}"),
        )
        platform = db.fetchone(
            """INSERT INTO platforms (id,name,code,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,true,'100条10线程分配测试',now(),now()) RETURNING id""",
            (f"allocation-{marker}", f"ALLOC{marker}"),
        )
        account = db.fetchone(
            """INSERT INTO platform_accounts (id,platform_id,name,identifier,enabled,remark,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,true,'100条10线程分配测试',now(),now()) RETURNING id""",
            (platform["id"], f"allocation-{marker}", f"allocation-{marker}"),
        )
        batch = db.fetchone(
            """INSERT INTO coupon_import_batches (id,name,platform_id,account_id,product_id,total_rows,success_count,failed_count,imported_at,"createdAt","updatedAt")
               VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,0,now(),now(),now()) RETURNING id""",
            (f"allocation-100-{marker}", platform["id"], account["id"], product["id"], TOTAL, TOTAL),
        )

        public_ids: list[str] = []
        for index in range(TOTAL):
            code = f"ALLOC-{marker}-{index:03d}"
            db.execute(
                """INSERT INTO coupons
                   (coupon_code,local_status,product_name,kfc_status,platform_id,account_id,product_id,batch_id,
                    imported_at,last_seen_at,"createdAt","updatedAt")
                   VALUES (%s,'wait',%s,'AVAILABLE',%s,%s,%s,%s,now(),now(),now(),now())""",
                (code, f"allocation-100-{marker}", platform["id"], account["id"], product["id"], batch["id"]),
            )
            public_id = secrets.token_hex(8)
            db.execute(
                """INSERT INTO access_links
                   (public_id,status,product_id,platform_id,account_id,internal_locked_at,"createdAt","updatedAt")
                   VALUES (%s,'UNUSED',%s,%s,%s,now(),now(),now())""",
                (public_id, product["id"], platform["id"], account["id"]),
            )
            public_ids.append(public_id)
    return public_ids


def _assign(index: int, public_id: str) -> dict:
    """调用生产仓储层中的并发卡券分配逻辑。"""
    return assign_coupon(public_id, f"127.0.0.{(index % 250) + 1}")


def run_concurrent_allocation_test() -> None:
    marker = secrets.token_hex(4)
    public_ids = _prepare_rows(marker)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="coupon-allocate") as executor:
        futures = [executor.submit(_assign, index, public_id) for index, public_id in enumerate(public_ids, start=1)]
        for future in as_completed(futures):
            results.append(future.result())

    assert len(results) == TOTAL, f"业务分配调用完成数量错误：期望 {TOTAL}，实际 {len(results)}"

    missing_links = db.fetchall(
        """SELECT public_id FROM access_links
           WHERE public_id = ANY(%s) AND coupon_code IS NULL""",
        (public_ids,),
    )
    duplicate_allocations = db.fetchall(
        """SELECT coupon_code, COUNT(*) AS assigned_count
           FROM access_links
           WHERE public_id = ANY(%s) AND coupon_code IS NOT NULL
           GROUP BY coupon_code
           HAVING COUNT(*) > 1""",
        (public_ids,),
    )
    assigned_links = db.fetchall(
        """SELECT id, public_id, coupon_code FROM access_links
           WHERE public_id = ANY(%s)""",
        (public_ids,),
    )
    link_ids = [row["id"] for row in assigned_links]
    logs = db.fetchall(
        """SELECT access_link_id, coupon_id FROM coupon_assignment_logs
           WHERE access_link_id = ANY(%s)""",
        (link_ids,),
    )

    assert len(assigned_links) == TOTAL, f"访问链接数量错误：期望 {TOTAL}，实际 {len(assigned_links)}"
    assert not missing_links, f"存在未分配卡券的链接：{[row['public_id'] for row in missing_links]}"
    assert not duplicate_allocations, (
        f"存在同一张卡券被重复分配：{[(row['coupon_code'], row['assigned_count']) for row in duplicate_allocations]}"
    )

    assigned_coupon_codes = [row["coupon_code"] for row in assigned_links]
    assert len(set(assigned_coupon_codes)) == TOTAL, "100 个链接没有获得 100 张不同的卡券"
    assert len(logs) == TOTAL, f"分配日志数量错误：期望 {TOTAL}，实际 {len(logs)}"
    assert len({row["coupon_id"] for row in logs}) == TOTAL, "分配日志中存在重复 coupon_id"


def test_coupon_allocation_100_links_10_threads_real_database() -> None:
    run_concurrent_allocation_test()


if __name__ == "__main__":
    db.open()
    try:
        run_concurrent_allocation_test()
    finally:
        db.close()
