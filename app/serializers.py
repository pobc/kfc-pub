"""数据库记录到接口返回结构的纯数据转换。"""

from __future__ import annotations

from typing import Any

from app.utils import iso


def product_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description") or "",
        "imageUrl": row.get("image_url"),
        "parentLinkId": row.get("parent_link_id") or "",
        "specLinkIds": row.get("spec_link_ids") or [],
        "condimentLinkIds": row.get("condiment_link_ids") or [],
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def coupon_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "couponCode": row["coupon_code"],
        "localStatus": row.get("local_status") or "wait",
        "productName": row.get("product_name") or "",
        "kfcStatus": row.get("kfc_status"),
        "platformId": str(row["platform_id"]),
        "accountId": str(row["account_id"]),
        "productId": str(row["product_id"]) if row.get("product_id") else None,
        "batchId": str(row["batch_id"]),
        "importedAt": iso(row.get("imported_at")),
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def batch_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "platformId": str(row["platform_id"]),
        "accountId": str(row["account_id"]),
        "productId": str(row["product_id"]) if row.get("product_id") else None,
        "totalRows": row["total_rows"],
        "successCount": row["success_count"],
        "failedCount": row["failed_count"],
        "importedAt": iso(row["imported_at"]),
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def ticket_view(row: dict[str, Any]) -> dict[str, Any]:
    ticket = row.get("ticket") or ""
    ticket_masked = "••••••" if len(ticket) <= 12 else f"{ticket[:6]}••••••{ticket[-4:]}"
    phone = row.get("phone") or ""
    return {
        "id": str(row["id"]),
        "name": phone or row["name"],
        "ticketMasked": ticket_masked,
        "userCode": row["user_code"],
        "phone": phone,
        "openId": row.get("open_id") or "",
        "deviceId": row.get("device_id") or "",
        "hasCookie": bool(row.get("cookie")),
        "hasAuthState": bool(row.get("auth_state")),
        "capturedAt": iso(row.get("captured_at")),
        "enabled": bool(row.get("enabled")),
        "lastSyncedAt": iso(row.get("last_synced_at")),
        "remark": row.get("remark") or "",
        "createdAt": iso(row.get("createdAt")),
        "updatedAt": iso(row.get("updatedAt")),
    }


def taobao_price_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "searchKeyword": row.get("search_keyword") or "",
        "shopName": row["shop_name"],
        "productUrl": row["product_url"],
        "itemName": row["item_name"],
        "price": format(row["price"], ".2f"),
        "scrapedAt": iso(row["scraped_at"]),
        "createdAt": iso(row.get("created_at")),
        "updatedAt": iso(row.get("updated_at")),
    }
