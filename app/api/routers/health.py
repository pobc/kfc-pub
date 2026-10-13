from fastapi import APIRouter

from app.errors import ok

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return ok({"status": "ok", "stack": "Python FastAPI"})
