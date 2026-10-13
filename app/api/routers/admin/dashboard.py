from fastapi import APIRouter

from app.api.dependencies import Admin
from app.service import admin_service
from app.errors import ok

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/dashboard")
def dashboard(_: Admin):
    return ok(admin_service.dashboard())
