from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.api.dependencies import Admin
from app.api.schemas import LoginInput
from app.errors import ok
from app.network import client_ip_from_request
from app.service.auth_service import admin_auth, local_admin_bypass

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


@router.post("/login")
def login(body: LoginInput, request: Request):
    token = admin_auth.login(
        body.username,
        body.password,
        client_ip=client_ip_from_request(request),
    )
    return ok({"token": token, "expiresIn": 8 * 60 * 60})


@router.get("/local-bypass")
def local_bypass():
    return ok({"enabled": local_admin_bypass()})


@router.post("/logout")
def logout(_: Admin, authorization: Annotated[str | None, Header()] = None):
    admin_auth.logout(authorization)
    return ok({"loggedOut": True})
