from fastapi import APIRouter

from app.api.dependencies import Admin
from app.api.schemas import (
    CouponBatchUpdateInput,
    CouponUpdateInput,
    KfcCouponSyncInput,
    ManualCouponImportInput,
)
from app.service import admin_service, kfc_account_service
from app.errors import ok

router = APIRouter(prefix="/api/admin", tags=["admin-coupons"])


@router.get("/coupon-batches")
def list_coupon_batches(_: Admin):
    return ok({"items": admin_service.list_coupon_batches()})


@router.patch("/coupon-batches/{batch_id}")
def update_coupon_batch(batch_id: str, body: CouponBatchUpdateInput, _: Admin):
    return ok(admin_service.update_coupon_batch(batch_id, body.name))


@router.delete("/coupon-batches/{batch_id}")
def delete_coupon_batch(batch_id: str, _: Admin):
    return ok(admin_service.delete_coupon_batch(batch_id))


@router.get("/coupons")
def list_coupons(_: Admin):
    return ok({"items": admin_service.list_coupons()})


@router.patch("/coupons/{coupon_id}")
def update_coupon(coupon_id: str, body: CouponUpdateInput, _: Admin):
    return ok(admin_service.update_coupon(coupon_id, body.couponCode))


@router.post("/coupons/{coupon_id}/reset")
def reset_coupon(coupon_id: str, _: Admin):
    return ok(admin_service.reset_coupon(coupon_id))


@router.delete("/coupons/{coupon_id}")
def delete_coupon(coupon_id: str, _: Admin):
    return ok(admin_service.delete_coupon(coupon_id))


@router.post("/coupons/import")
def import_coupons(body: ManualCouponImportInput, _: Admin):
    return ok(admin_service.import_manual_coupons(body.productId, body.batchName, body.couponCodes))


@router.post("/coupons/sync-kfc")
def sync_coupons(body: KfcCouponSyncInput, _: Admin):
    return ok(kfc_account_service.sync_kfc_coupons(body.ticketId, body.batchName))
