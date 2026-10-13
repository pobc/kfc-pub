"""Public access-link routes.

This public boundary may only call local business operations. It must never
import KFC HTTP clients, expose KFC authentication material, or proxy raw KFC
requests/responses.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import public_access_guard
from app.errors import ok
from app.service import access_link_service as access_service

router = APIRouter(
    prefix="/api/public/access-links",
    tags=["public-access"],
    dependencies=[Depends(public_access_guard)],
)


@router.get("/{access_id}")
def get_access_link_state(access_id: str):
    return ok(access_service.get_access_link_state(access_id))
