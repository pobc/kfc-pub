from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.network import client_ip_from_request
from app.service import security_service
from app.service.auth_service import admin_auth


def admin_guard(authorization: Annotated[str | None, Header()] = None) -> None:
    admin_auth.verify(authorization)


def public_access_guard(access_id: str, request: Request) -> None:
    security_service.require_public_access(
        access_id,
        client_ip_from_request(request),
        request.cookies.get(security_service.PUBLIC_VERIFICATION_COOKIE),
    )


Admin = Annotated[None, Depends(admin_guard)]
PublicAccess = Annotated[None, Depends(public_access_guard)]
