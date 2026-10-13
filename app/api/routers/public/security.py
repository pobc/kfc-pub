from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.schemas import PublicVerificationInput
from app.config import settings
from app.errors import ok
from app.network import client_ip_from_request
from app.service import security_service

router = APIRouter(prefix="/api/public/verification", tags=["public-verification"])


@router.get("/challenge")
def challenge(accessId: str):
    return ok(security_service.create_verification_challenge(accessId))


@router.post("/verify")
def verify(accessId: str, body: PublicVerificationInput, request: Request, response: Response):
    token = security_service.verify_challenge(
        body.challengeToken,
        body.answer,
        client_ip_from_request(request),
        accessId,
    )
    response.set_cookie(
        security_service.PUBLIC_VERIFICATION_COOKIE,
        token,
        max_age=settings().public_verification_ttl_minutes * 60,
        httponly=True,
        secure=settings().environment.strip().lower() == "production",
        samesite="lax",
        path="/",
    )
    return ok({"verified": True})
