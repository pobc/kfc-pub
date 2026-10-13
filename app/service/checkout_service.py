from __future__ import annotations

import logging
from typing import Any

from app.database import access_links, order_confirmation_logs, orders, products
from app.database.coupon import redemption_job_db as redemption_jobs
from app.errors import AppError, bad_gateway, conflict, unavailable
from app.service.kfc.coupon_sync import kfc_h5
from app.service.kfc_account_service import get_enabled_auth_for_account
from app.service.kfc.query_order_detail import kfc_query_order_detail
from app.utils import iso

logger = logging.getLogger(__name__)

_PUBLIC_STORE_FIELDS = (
    "storeCode",
    "name",
    "address",
    "phone",
    "city",
    "cityCode",
    "status",
    "businessHours",
    "imageUrl",
)
_PUBLIC_PRODUCT_FIELDS = (
    "id",
    "description",
    "imageUrl",
    "price",
    "enabled",
    "minQty",
    "maxQty",
)
_PUBLIC_ORDER_FIELDS = (
    "orderNumber",
    "stage",
    "statusName",
    "statusText",
    "queueNumber",
    "pickupCode",
    "stopPolling",
)
_PUBLIC_ORDER_ITEM_FIELDS = (
    "name",
    "quantity",
    "description",
    "imageUrl",
)
_PUBLIC_ORDER_COMPONENT_FIELDS = ("name", "quantity")


def _public_store_view(store: dict[str, Any]) -> dict[str, Any]:
    """Return the strict public allowlist for a KFC store result."""
    return {field: store[field] for field in _PUBLIC_STORE_FIELDS if field in store}


def _public_product_view(product: dict[str, Any]) -> dict[str, Any]:
    """Hide KFC menu identifiers and expose only the customer-facing product DTO."""
    return {field: product[field] for field in _PUBLIC_PRODUCT_FIELDS if field in product}


def _public_order_component_view(component: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Recursively allow only Chinese display text and quantity for meal components."""
    view = {field: component[field] for field in _PUBLIC_ORDER_COMPONENT_FIELDS if field in component}
    children = component.get("components")
    view["components"] = (
        [_public_order_component_view(child, depth + 1) for child in children if isinstance(child, dict)]
        if depth < 3 and isinstance(children, list)
        else []
    )
    return view


def _public_order_item_view(item: dict[str, Any]) -> dict[str, Any]:
    """Expose only customer-facing fields from an order item snapshot."""
    view = {field: item[field] for field in _PUBLIC_ORDER_ITEM_FIELDS if field in item}
    components = item.get("components")
    view["components"] = (
        [_public_order_component_view(component) for component in components if isinstance(component, dict)]
        if isinstance(components, list)
        else []
    )
    return view


def _public_order_view(order: dict[str, Any]) -> dict[str, Any]:
    """Hide upstream order identifiers and snapshots from the public API."""
    view = {field: order[field] for field in _PUBLIC_ORDER_FIELDS if field in order}
    store = order.get("store")
    view["store"] = _public_store_view(store) if isinstance(store, dict) else {}
    items = order.get("items")
    view["items"] = (
        [_public_order_item_view(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    )
    return view


def _waiting_order_view(status_text: str) -> dict[str, Any]:
    return {
        "stage": "SUBMITTED",
        "statusName": "",
        "statusText": status_text,
        "queueNumber": "",
        "pickupCode": "",
        "stopPolling": False,
        "store": {},
        "items": [],
    }


def _coupon_applied_view(
    applied_at: str,
    *,
    code: Any = 0,
    msg: str = "卡券已使用并确认订单",
) -> dict[str, Any]:
    return {
        "code": code,
        "msg": msg,
        "applied": True,
        "appliedAt": applied_at,
        "status": "APPLIED",
        "message": msg,
    }


def _processing_view() -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "核销任务已提交，正在处理中",
        "applied": False,
        "status": "PROCESSING",
        "message": "核销任务已提交，正在处理中",
    }


def search_stores(access_id: str, gb_city_code: str, keyword: str) -> dict[str, Any]:
    logger.info("public store search requested access_id=%s city_code=%s keyword=%s", access_id, gb_city_code, keyword)
    link = access_links.get_link(access_id)
    if link["status"] != "UNUSED" or link.get("coupon_applied_at"):
        logger.info("public store search skipped access_id=%s status=%s", access_id, link["status"])
        return {"stores": []}
    try:
        stores = kfc_h5.search_stores(gb_city_code, keyword)
    except AppError as exc:
        logger.warning("KFC store search failed for public request access_id=%s", access_id, exc_info=True)
        raise bad_gateway("餐厅搜索暂时不可用") from exc
    result = {"stores": [_public_store_view(store) for store in stores]}
    logger.info("public store search completed access_id=%s count=%s", access_id, len(result["stores"]))
    return result


def menu(access_id: str) -> dict[str, Any]:
    logger.info("public product list requested access_id=%s", access_id)
    link = access_links.get_link(access_id)
    if link["status"] != "UNUSED" or link.get("coupon_applied_at"):
        logger.info("public product list skipped access_id=%s status=%s", access_id, link["status"])
        return {"products": []}
    product_rows = access_links.linked_products(access_id)
    result = {"products": [_public_product_view({**product, "price": 0, "enabled": True}) for product in product_rows]}
    logger.info("public product list completed access_id=%s count=%s", access_id, len(result["products"]))
    return result


def confirm_store(
    access_id: str,
    store_code: str,
    store_address: str,
    visitor_ip: str,
    *,
    store_name: str = "",
    city_name: str = "",
    image_url: str = "",
) -> dict[str, str]:
    logger.info("restaurant selected access_id=%s store=%s", access_id, store_code)
    try:
        return order_confirmation_logs.record_order_confirmation(
            access_id,
            visitor_ip,
            store_code,
            store_address,
            store_name=store_name,
            city_name=city_name,
            image_url=image_url,
        )
    except Exception:
        logger.exception("restaurant confirmation failed access_id=%s store=%s", access_id, store_code)
        raise


def apply_coupon(
    access_id: str,
    *,
    store_code: str,
    pack_type: int,
    items: list[dict[str, Any]],
    option_ids: list[int],
    visitor_ip: str,
) -> dict[str, Any]:
    logger.info(
        "coupon apply requested access_id=%s store_code=%s pack_type=%s items=%s option_ids=%s visitor_ip=%s",
        access_id,
        store_code,
        pack_type,
        items,
        option_ids,
        visitor_ip,
    )
    current = access_links.get_link(access_id)
    if current.get("coupon_applied_at") and current.get("coupon_cart_id"):
        logger.info(
            "coupon apply already completed access_id=%s applied_at=%s", access_id, current.get("coupon_applied_at")
        )
        return _coupon_applied_view(iso(current["coupon_applied_at"]))
    if current["status"] == "PROCESSING":
        logger.info("coupon apply already processing access_id=%s", access_id)
        return _processing_view()
    if current["status"] != "UNUSED":
        logger.warning("coupon apply rejected access_id=%s status=%s", access_id, current["status"])
        raise conflict("该链接当前不可核销")

    product_id = str(current.get("product_id") or "").strip()
    production_info = products.selected_option_snapshot(product_id, option_ids) if product_id else []
    order_confirmation_logs.save_production_info(access_id, store_code, production_info)

    try:
        store = kfc_h5.get_store(store_code)
    except AppError as exc:
        logger.warning(
            "KFC store cache lookup failed before queueing redemption access_id=%s store_code=%s",
            access_id,
            store_code,
            exc_info=True,
        )
        raise bad_gateway("餐厅信息已失效，请重新搜索并选择餐厅") from exc

    store_view = _public_store_view(store)
    if store_view.get("status") != "OPEN":
        logger.warning("coupon apply rejected closed store access_id=%s store_code=%s", access_id, store_code)
        raise conflict("所选餐厅当前不可使用卡券")
    store_entry = kfc_h5.stores.get(store_code)
    store_snapshot = store_entry.get("raw") if isinstance(store_entry, dict) else None
    if not isinstance(store_snapshot, dict) or not store_snapshot:
        logger.warning("coupon apply missing store snapshot access_id=%s store_code=%s", access_id, store_code)
        raise bad_gateway("餐厅信息已失效，请重新搜索并选择餐厅")

    job = redemption_jobs.enqueue(
        access_id,
        store_code=store_code,
        store_snapshot=store_snapshot,
        pack_type=pack_type,
        items=items,
        visitor_ip=visitor_ip,
    )
    logger.info(
        "redemption job enqueued access_id=%s job_id=%s status=%s coupon_id=%s account_id=%s",
        access_id,
        job.get("id"),
        job.get("status"),
        job.get("couponId"),
        job.get("accountId"),
    )
    if job.get("status") == "COMPLETED":
        return _coupon_applied_view(job.get("appliedAt") or "")
    return _processing_view()


def get_order_info(access_id: str) -> dict[str, Any]:
    logger.info("public order info requested access_id=%s", access_id)
    link = access_links.get_link(access_id)

    if link["status"] == "FAILED":
        return {
            "stage": "FAILED",
            "statusName": "",
            "statusText": "下单失败",
            "queueNumber": "",
            "pickupCode": "",
            "stopPolling": True,
            "store": {},
            "items": [],
        }

    existing_order = orders.find_order_for_link(access_id)
    if existing_order:
        saved_view = orders.order_view(existing_order)
        if saved_view["stopPolling"]:
            # 已取得取餐号后只读本地快照，不再加载账号或访问 KFC。
            logger.info(
                "订单查询使用本地结果 access_id=%s stage=%s has_pickup_number=%s",
                access_id,
                saved_view["stage"],
                bool(saved_view["queueNumber"] or saved_view["pickupCode"]),
            )
            return _public_order_view(saved_view)
    # 尚无取餐号且 Worker 仍在提交时，不根据提前保存的购物车号查询上游。
    if link["status"] == "PROCESSING":
        return _waiting_order_view("正在使用卡券并提交订单")
    external_order_id = str(
        (existing_order or {}).get("external_order_id") or link.get("cart_id") or link.get("coupon_cart_id") or ""
    ).strip()
    if not external_order_id:
        logger.info("public order info waiting for coupon_cart_id access_id=%s", access_id)
        return _waiting_order_view("正在等待肯德基生成订单")

    account_id = str(link.get("account_id") or "").strip()
    if not account_id:
        logger.warning("public order info unavailable access_id=%s reason=missing_account_id", access_id)
        raise unavailable("当前订单没有关联 KFC 账号")

    try:
        auth = get_enabled_auth_for_account(account_id)
        phone = str(auth.get("phone") or "").strip()
        if not phone:
            raise unavailable("当前 KFC 账号没有可用手机号")
        logger.info(
            "KFC order detail query started access_id=%s account_id=%s phone=%s order_id=%s",
            access_id,
            account_id,
            phone,
            external_order_id,
        )
        detail = kfc_query_order_detail.get_order_info(phone=phone, order_id=external_order_id)
    except AppError as exc:
        logger.warning("KFC order detail query failed for public request access_id=%s", access_id, exc_info=True)
        raise bad_gateway("订单信息暂时无法查询") from exc

    status = {
        "stage": detail.stage,
        "statusCode": detail.status_code,
        "statusName": detail.status_name,
        "queueNumber": detail.pickup_number,
        "pickupCode": "",
        "store": detail.store,
        "items": detail.items,
        "rawStatus": detail.raw_status,
    }
    result = _public_order_view(orders.save_order_detail(access_id, external_order_id, status))
    logger.info(
        "public order info refreshed access_id=%s order_id=%s stage=%s status_code=%s status_name=%s queue_number=%s",
        access_id,
        external_order_id,
        detail.stage,
        detail.status_code,
        detail.status_name,
        detail.pickup_number,
    )
    return result
