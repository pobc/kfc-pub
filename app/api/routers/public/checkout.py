"""Public checkout routes.

These endpoints expose only KFC-independent business DTOs. They must not accept
or return raw KFC payloads, KFC credentials, upstream endpoint paths, or generic
proxy parameters.
"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import public_access_guard
from app.api.schemas import ApplyCouponInput, ConfirmStoreInput, SearchStoresInput
from app.errors import ok
from app.network import client_ip_from_request
from app.service import checkout_service

router = APIRouter(
    prefix="/api/public/access-links/{access_id}",
    tags=["public-checkout"],
    dependencies=[Depends(public_access_guard)],
)


@router.post("/stores/search")
def search_stores(access_id: str, body: SearchStoresInput):
    return ok(checkout_service.search_stores(access_id, body.gbCityCode, body.keyword))


@router.post("/coupon/apply")
def apply_coupon(access_id: str, body: ApplyCouponInput, request: Request):
    return ok(
        checkout_service.apply_coupon(
            access_id,
            store_code=body.storeCode,
            pack_type=body.packType,
            items=[item.model_dump() for item in body.items],
            option_ids=body.optionIds,
            visitor_ip=client_ip_from_request(request),
        )
    )


@router.post("/stores/confirm")
def confirm_store(access_id: str, body: ConfirmStoreInput, request: Request):
    return ok(
        checkout_service.confirm_store(
            access_id,
            body.storeCode,
            body.storeAddress,
            client_ip_from_request(request),
            store_name=body.storeName,
            city_name=body.cityName,
            image_url=body.storeImageUrl,
        )
    )


@router.get("/orders/getOrderInfo")
def get_order_info(access_id: str):
    return ok(checkout_service.get_order_info(access_id))
