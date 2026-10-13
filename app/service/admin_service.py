from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from app.database import access_links, coupon_import_batches, coupons, kfc_tickets, products
from app.database.product import kfc_name_mapping_db
from app.database.security import security_db
from app.errors import bad_request

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_IMAGE_DIR = PROJECT_ROOT / "static" / "product-images"


def dashboard() -> dict[str, int]:
    result = {
        "products": products.count_products(),
        "coupons": coupons.count_coupons(),
        "kfcTickets": kfc_tickets.count_kfc_tickets(),
        "accessLinks": access_links.count_access_links(),
        "invalidAccessLinks": security_db.invalid_access_link_count(),
    }
    logger.info("admin dashboard loaded stats=%s", result)
    return result


def list_products() -> list[dict[str, Any]]:
    result = products.list_products()
    logger.info("admin products listed count=%s", len(result))
    return result


def create_product(data: dict[str, Any]) -> dict[str, Any]:
    logger.info("admin product create requested data=%s", data)
    result = products.create_product(data)
    logger.info("admin product created id=%s", result.get("id"))
    return result


def update_product(product_id: str, data: dict[str, Any]) -> dict[str, Any]:
    logger.info("admin product update requested product_id=%s data=%s", product_id, data)
    result = products.update_product(product_id, data)
    logger.info("admin product updated product_id=%s", product_id)
    return result


def list_product_options(product_id: str) -> dict[str, Any]:
    result = products.list_product_options(product_id)
    logger.info(
        "admin product options listed product_id=%s groups=%s",
        product_id,
        len(result.get("groups", [])),
    )
    return result


def replace_product_options(product_id: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    logger.info(
        "admin product options replace requested product_id=%s groups=%s",
        product_id,
        len(groups),
    )
    result = products.replace_product_options(product_id, groups)
    logger.info("admin product options replaced product_id=%s", product_id)
    return result


def delete_product(product_id: str) -> dict[str, bool]:
    logger.info("admin product delete requested product_id=%s", product_id)
    result = products.delete_product(product_id)
    logger.info("admin product deleted product_id=%s deleted=%s", product_id, result.get("deleted"))
    return result


def list_kfc_product_name_mappings() -> list[dict[str, Any]]:
    result = kfc_name_mapping_db.list_mappings()
    logger.info("KFC product name mappings listed count=%s", len(result))
    return result


def upsert_kfc_product_name_mapping(kfc_product_name: str, product_id: str) -> dict[str, Any]:
    logger.info("KFC product name mapping upsert requested kfc_name=%s product_id=%s", kfc_product_name, product_id)
    result = kfc_name_mapping_db.upsert_mapping(kfc_product_name, product_id)
    logger.info("KFC product name mapping saved mapping_id=%s", result.get("id"))
    return result


def delete_kfc_product_name_mapping(mapping_id: str) -> dict[str, bool]:
    logger.info("KFC product name mapping delete requested mapping_id=%s", mapping_id)
    result = kfc_name_mapping_db.delete_mapping(mapping_id)
    logger.info("KFC product name mapping deleted mapping_id=%s deleted=%s", mapping_id, result.get("deleted"))
    return result


def _image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def upload_image_bytes(content: bytes, mime: str, original_name: str) -> dict[str, str]:
    del mime
    logger.info("product image upload requested name=%s size=%s", original_name, len(content))
    if not content:
        raise bad_request("请选择要上传的图片")
    if len(content) > 2 * 1024 * 1024:
        raise bad_request("图片不能超过 2MB")
    detected = _image_type(content)
    if not detected:
        raise bad_request("文件内容不是支持的 JPG、PNG、WEBP、GIF 图片")
    extension, detected_mime = detected
    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    target = PRODUCT_IMAGE_DIR / filename
    temp = PRODUCT_IMAGE_DIR / f".{filename}.tmp"
    try:
        temp.write_bytes(content)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    result = {
        "imageUrl": f"/static/product-images/{filename}",
        "mime": detected_mime,
        "originalName": original_name,
    }
    logger.info("product image uploaded name=%s path=%s", original_name, result["imageUrl"])
    return result


def list_coupon_batches() -> list[dict[str, Any]]:
    result = coupon_import_batches.list_coupon_batches()
    logger.info("coupon batches listed count=%s", len(result))
    return result


def update_coupon_batch(batch_id: str, name: str) -> dict[str, Any]:
    logger.info("coupon batch update requested batch_id=%s name=%s", batch_id, name)
    result = coupon_import_batches.update_coupon_batch(batch_id, name)
    logger.info("coupon batch updated batch_id=%s", batch_id)
    return result


def delete_coupon_batch(batch_id: str) -> dict[str, Any]:
    logger.info("coupon batch delete requested batch_id=%s", batch_id)
    result = coupon_import_batches.delete_coupon_batch(batch_id)
    logger.info("coupon batch deleted batch_id=%s", batch_id)
    return result


def list_coupons() -> list[dict[str, Any]]:
    result = coupons.list_coupons()
    logger.info("coupons listed count=%s", len(result))
    return result


def update_coupon(coupon_id: str, coupon_code: str) -> dict[str, Any]:
    logger.info("coupon update requested coupon_id=%s coupon_code=%s", coupon_id, coupon_code)
    result = coupons.update_coupon(coupon_id, coupon_code)
    logger.info("coupon updated coupon_id=%s", coupon_id)
    return result


def reset_coupon(coupon_id: str) -> dict[str, Any]:
    logger.info("coupon reset requested coupon_id=%s", coupon_id)
    result = coupons.reset_coupon(coupon_id)
    logger.info("coupon reset completed coupon_id=%s local_status=%s", coupon_id, result.get("localStatus"))
    return result


def delete_coupon(coupon_id: str) -> dict[str, bool]:
    logger.info("coupon delete requested coupon_id=%s", coupon_id)
    result = coupons.delete_coupon(coupon_id)
    logger.info("coupon deleted coupon_id=%s deleted=%s", coupon_id, result.get("deleted"))
    return result


def import_manual_coupons(product_id: str, batch_name: str, codes: list[str]) -> dict[str, Any]:
    logger.info("manual coupon import requested product_id=%s batch_name=%s count=%s", product_id, batch_name, len(codes))
    result = coupons.import_manual_coupons(product_id, batch_name, codes)
    logger.info("manual coupon import completed product_id=%s batch_name=%s result=%s", product_id, batch_name, result)
    return result
