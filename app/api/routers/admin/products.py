from fastapi import APIRouter, File, UploadFile

from app.api.dependencies import Admin
from app.api.schemas import (
    KfcProductNameMappingInput,
    ProductCreateInput,
    ProductOptionsInput,
    ProductUpdateInput,
)
from app.errors import ok
from app.service import admin_service

router = APIRouter(prefix="/api/admin", tags=["admin-products"])


@router.get("/products")
def list_products(_: Admin):
    return ok({"items": admin_service.list_products()})


@router.post("/products")
def create_product(body: ProductCreateInput, _: Admin):
    return ok(admin_service.create_product(body.model_dump()))


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: ProductUpdateInput, _: Admin):
    return ok(admin_service.update_product(product_id, body.model_dump(exclude_unset=True)))


@router.get("/products/{product_id}/options")
def list_product_options(product_id: str, _: Admin):
    return ok(admin_service.list_product_options(product_id))


@router.put("/products/{product_id}/options")
def replace_product_options(product_id: str, body: ProductOptionsInput, _: Admin):
    groups = [group.model_dump() for group in body.groups]
    return ok(admin_service.replace_product_options(product_id, groups))


@router.delete("/products/{product_id}")
def delete_product(product_id: str, _: Admin):
    return ok(admin_service.delete_product(product_id))


@router.post("/product-images")
def upload_product_image(_: Admin, image: UploadFile = File(...)):
    return ok(
        admin_service.upload_image_bytes(
            image.file.read(),
            image.content_type or "",
            image.filename or "image",
        )
    )


@router.get("/kfc-product-name-mappings")
def list_kfc_product_name_mappings(_: Admin):
    return ok({"items": admin_service.list_kfc_product_name_mappings()})


@router.post("/kfc-product-name-mappings")
def upsert_kfc_product_name_mapping(body: KfcProductNameMappingInput, _: Admin):
    return ok(admin_service.upsert_kfc_product_name_mapping(body.kfcProductName, body.productId))


@router.delete("/kfc-product-name-mappings/{mapping_id}")
def delete_kfc_product_name_mapping(mapping_id: str, _: Admin):
    return ok(admin_service.delete_kfc_product_name_mapping(mapping_id))
