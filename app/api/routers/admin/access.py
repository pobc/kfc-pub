from fastapi import APIRouter, Query

from app.api.dependencies import Admin
from app.api.schemas import AccessLinksCreateInput, AccessLinkUpdateInput
from app.service import access_link_service as access_service
from app.errors import ok

router = APIRouter(prefix="/api/admin", tags=["admin-access"])


@router.get("/access-links")
def list_access_links(
    _: Admin,
    page: str | None = Query(default=None),
    pageSize: str | None = Query(default=None),
    query: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return ok(access_service.list_access_links(page, pageSize, query, status))


@router.post("/access-links")
def create_access_links(body: AccessLinksCreateInput, _: Admin):
    items = access_service.create_access_links(body.productId, body.count)
    return ok({"items": items, "count": len(items)})


@router.patch("/access-links/{link_id}")
def update_access_link(link_id: str, body: AccessLinkUpdateInput, _: Admin):
    return ok(access_service.update_access_link(link_id, body.productId))


@router.delete("/access-links/{link_id}")
def delete_access_link(link_id: str, _: Admin):
    return ok(access_service.delete_access_link(link_id))


@router.get("/redemptions")
def redemptions(
    _: Admin,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    productId: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return ok(access_service.list_redemptions(page, pageSize, productId, status))
