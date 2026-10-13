from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import db, products
from app.service.kfc.h5_client import KfcH5Client
from app.service.kfc.redemption_browser import RedemptionBrowser, run_browser_task
from app.service.kfc.redemption_logging import log_redemption_failure
from app.service.kfc_account_service import get_enabled_auth_for_account


logger = logging.getLogger("app.tools.coupon_redemption")


@dataclass(frozen=True)
class CouponContext:
    account_id: str
    product_id: str
    product_name: str
    auth: dict[str, Any]
    has_product_options: bool


@dataclass(frozen=True)
class StoreContext:
    code: str
    name: str
    image_url: str


class VerificationTrackingBrowser(RedemptionBrowser):
    """记录真实用券流程是否进入了自动滑块验证。"""

    def __init__(self, auth: dict[str, Any], *, job_id: str) -> None:
        super().__init__(auth, job_id=job_id)
        self.verification = "not-requested"

    async def solve_slider(self) -> str:
        self.verification = "requested"
        self.verification = await super().solve_slider()
        return self.verification


def _canonical_store_name(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).strip()
    for suffix in ("餐厅", "店"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def load_coupon_context(coupon_code: str) -> CouponContext:
    coupon = db.fetchone(
        """SELECT c.account_id, c.product_id, c.kfc_status, c.local_status,
                  COALESCE(p.name, c.product_name, '') AS product_name
           FROM coupons c
           LEFT JOIN products p ON p.id=c.product_id
           WHERE c.coupon_code=%s
           LIMIT 1""",
        (coupon_code,),
    )
    if not coupon:
        raise RuntimeError("本地数据库中不存在指定券码")
    if coupon.get("kfc_status") != "AVAILABLE" or coupon.get("local_status") != "wait":
        raise RuntimeError("指定券码当前不是可用状态")
    if not coupon.get("account_id") or not coupon.get("product_id"):
        raise RuntimeError("指定券码缺少账号或餐品归属")

    account_id = str(coupon["account_id"])
    product_id = str(coupon["product_id"])
    return CouponContext(
        account_id=account_id,
        product_id=product_id,
        product_name=str(coupon.get("product_name") or ""),
        auth=get_enabled_auth_for_account(account_id),
        has_product_options=products.has_product_options(product_id),
    )


def find_store(city_code: str, store_name: str) -> StoreContext:
    target = _canonical_store_name(store_name)
    client = KfcH5Client()
    try:
        stores = client.search_stores(city_code, target, page_size=20)
    finally:
        client.close()

    matches = [store for store in stores if _canonical_store_name(str(store.get("name") or "")) == target]
    if len(matches) != 1:
        raise RuntimeError(f"门店搜索结果无法唯一匹配：{store_name}")
    store = matches[0]
    if store.get("status") != "OPEN":
        raise RuntimeError("目标门店当前未营业")
    image_url = str(store.get("imageUrl") or "").strip()
    if not image_url:
        raise RuntimeError("目标门店缺少可用的匹配图片")
    return StoreContext(code=str(store["storeCode"]), name=str(store["name"]), image_url=image_url)


async def verify_coupon_redemption(
    *,
    coupon_code: str,
    city_code: str,
    city_name: str,
    store_name: str,
    pickup_type: int,
    screenshot_path: Path,
) -> dict[str, Any]:
    coupon = load_coupon_context(coupon_code)
    store = find_store(city_code, store_name)
    browser = VerificationTrackingBrowser(coupon.auth, job_id="coupon-redemption-check")
    try:
        async with browser:
            try:
                async with asyncio.timeout(120):
                    logger.info(
                        "用券验证开始 city=%s store=%s store_code=%s pickup_type=%s product=%s",
                        city_name,
                        store.name,
                        store.code,
                        pickup_type,
                        coupon.product_name,
                    )
                    await browser.open_menu(
                        store_code=store.code,
                        city_name=city_name,
                        store_name=store.name,
                        image_url=store.image_url,
                        pickup_type=pickup_type,
                    )
                    cart_id = await browser.use_coupon(
                        coupon_code,
                        pickup_type,
                        has_product_options=coupon.has_product_options,
                        production_info=[],
                    )
                    await browser._wait(500)
                    screenshot = screenshot_path.resolve()
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    await browser.page.screenshot(path=str(screenshot), full_page=True)
                    logger.info(
                        "用券验证完成 result=success verification=%s screenshot=%s",
                        browser.verification,
                        screenshot,
                    )
                    return {
                        "result": "success",
                        "verification": browser.verification,
                        "storeCode": store.code,
                        "storeName": store.name,
                        "cartCreated": bool(cart_id),
                        "screenshot": str(screenshot),
                    }
            except Exception:
                failure = screenshot_path.resolve().with_name(
                    f"{screenshot_path.stem}-failed{screenshot_path.suffix or '.png'}"
                )
                try:
                    failure.parent.mkdir(parents=True, exist_ok=True)
                    await browser.page.screenshot(path=str(failure), full_page=True)
                    logger.error("用券验证失败，已保存现场截图 screenshot=%s", failure)
                except Exception:
                    logger.exception("用券验证失败后保存现场截图失败")
                raise
    except Exception as exc:
        log_redemption_failure(logger, browser.job_id, browser.stage, exc, secrets=browser.log_secrets)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在真实无头 Chrome 中只执行用券、自动验证和截图")
    parser.add_argument("--coupon-code", required=True)
    parser.add_argument("--city-code", default="440100")
    parser.add_argument("--city-name", default="广州")
    parser.add_argument("--store-name", required=True)
    parser.add_argument("--pickup-type", type=int, choices=(1, 2), default=1, help="1 外带，2 堂食")
    parser.add_argument("--screenshot", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    from app.logging_utils import configure_logging

    args = parse_args()
    configure_logging()
    logging.getLogger("app.service.kfc.h5_config").setLevel(logging.WARNING)
    logging.getLogger("app.service.kfc.http_client").setLevel(logging.WARNING)
    try:
        result = run_browser_task(
            verify_coupon_redemption(
                coupon_code=args.coupon_code.strip(),
                city_code=args.city_code.strip(),
                city_name=args.city_name.strip(),
                store_name=args.store_name.strip(),
                pickup_type=args.pickup_type,
                screenshot_path=args.screenshot,
            )
        )
    except Exception as exc:
        print(json.dumps({"result": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
