from __future__ import annotations

import logging
from typing import Any

from app.database import access_link_redemptions, access_links, products
from app.utils import iso

logger = logging.getLogger(__name__)


def list_access_links(page: str | None, page_size: str | None, query: str | None, status: str | None) -> dict[str, Any]:
    result = access_links.list_access_links(page, page_size, query, status)
    logger.info("access links listed page=%s page_size=%s query=%s status=%s", page, page_size, query, status)
    return result


def create_access_links(product_id: str, count: int = 10) -> list[dict[str, Any]]:
    logger.info("creating access links product_id=%s count=%s", product_id, count)
    result = access_links.create_access_links(product_id, count)
    logger.info("access links created product_id=%s count=%s", product_id, len(result))
    return result


def update_access_link(link_id: str, product_id: str) -> dict[str, Any]:
    logger.info("updating access link link_id=%s product_id=%s", link_id, product_id)
    result = access_links.update_access_link(link_id, product_id)
    logger.info("access link updated link_id=%s", link_id)
    return result


def delete_access_link(link_id: str) -> dict[str, bool]:
    logger.info("deleting access link link_id=%s", link_id)
    result = access_links.delete_access_link(link_id)
    logger.info("access link deleted link_id=%s deleted=%s", link_id, result.get("deleted"))
    return result


def list_redemptions(
    page: int = 1, page_size: int = 20, product_id: str | None = None, status: str | None = None
) -> dict[str, Any]:
    result = access_link_redemptions.list_redemptions(page, page_size, product_id, status)
    logger.info(
        "redemptions listed page=%s page_size=%s product_id=%s status=%s",
        page,
        page_size,
        product_id,
        status,
    )
    return result


def get_access_link_state(access_id: str) -> dict[str, Any]:
    link = access_links.get_link(access_id)
    route = {
        "UNUSED": "SELECT_STORE",
        "PROCESSING": "REDEMPTION_PROCESSING",
        "PICKING_UP": "PICKUP_STATUS",
        "FAILED": "ORDER_FAILED",
        "COMPLETED": "ORDER_COMPLETED",
    }
    data = {
        "accessId": access_id,
        "status": link["status"],
        "allowedPage": "COUPON_APPLIED" if link.get("coupon_applied_at") else route[link["status"]],
    }
    if link["status"] == "UNUSED":
        product_rows = access_links.linked_products(access_id)
        for product in product_rows:
            option_config = products.list_product_options(product["id"])
            product["optionGroups"] = option_config["groups"]
        data.update(
            {
                "couponApplied": bool(link.get("coupon_applied_at") and link.get("coupon_cart_id")),
                "couponAppliedAt": iso(link.get("coupon_applied_at")) or "",
                "products": product_rows,
            }
        )
    elif link["status"] == "PROCESSING":
        data["couponApplied"] = False
        data["couponAppliedAt"] = ""
    logger.info(
        "public access link viewed access_id=%s status=%s allowed_page=%s product_count=%s",
        access_id,
        link["status"],
        data["allowedPage"],
        len(data.get("products") or []),
    )
    return data
