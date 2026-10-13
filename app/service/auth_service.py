from __future__ import annotations

import hashlib
import logging
import secrets
import socket

import bcrypt

from app.config import settings
from app.database.security import security_db
from app.database.user import admin_session_db, user_db
from app.errors import AppError, unauthorized
from app.service.security_service import login_retry_after_seconds

logger = logging.getLogger(__name__)


def local_admin_bypass() -> bool:
    env = settings().environment.lower()
    if env in {"production", "test"}:
        return False
    current = socket.gethostname().strip().lower().rstrip(".")
    allowed = settings().local_admin_hostname.strip().lower().rstrip(".")
    enabled = current == allowed
    logger.debug("local admin bypass checked hostname=%s allowed=%s enabled=%s", current, allowed, enabled)
    return enabled


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AdminAuth:
    def login(self, username: str, password: str, client_ip: str = "unknown") -> str:
        username = username.strip()
        logger.info("admin login requested username=%s client_ip=%s", username, client_ip)
        retry_after = login_retry_after_seconds(security_db.login_failure_state(username))
        if retry_after > 0:
            logger.warning(
                "admin login blocked username=%s client_ip=%s retry_after=%s",
                username,
                client_ip,
                retry_after,
            )
            raise AppError(429, f"密码错误次数过多，请 {retry_after} 秒后再试")

        user = user_db.find_user(username)
        if not user or not user["enabled"]:
            security_db.record_login_failure(username, client_ip)
            logger.warning("admin login failed username=%s client_ip=%s reason=user_not_found_or_disabled", username, client_ip)
            raise unauthorized("账号或密码错误")
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            security_db.record_login_failure(username, client_ip)
            logger.warning("admin login failed username=%s client_ip=%s reason=password", username, client_ip)
            raise unauthorized("账号或密码错误")

        security_db.record_login_success(username, client_ip)
        user_db.touch_last_login(user["id"])
        token = secrets.token_urlsafe(32)
        admin_session_db.create_admin_session(
            user_id=str(user["id"]),
            token_hash=_token_hash(token),
            ttl_minutes=settings().admin_session_ttl_minutes,
        )
        logger.info("admin login succeeded username=%s user_id=%s client_ip=%s", username, user["id"], client_ip)
        return token

    def verify(self, authorization: str | None) -> str:
        if local_admin_bypass():
            logger.debug("admin authorization accepted by local bypass")
            return "LOCAL_BYPASS"
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not token:
            logger.warning("admin authorization rejected reason=missing_token")
            raise unauthorized("请先登录")
        if not admin_session_db.get_valid_admin_session(_token_hash(token)):
            logger.warning("admin authorization rejected reason=invalid_or_expired_session")
            raise unauthorized("登录已失效，请重新登录")
        logger.debug("admin authorization verified")
        return token

    def logout(self, authorization: str | None) -> None:
        if local_admin_bypass():
            logger.info("admin logout skipped by local bypass")
            return
        token = self.verify(authorization)
        admin_session_db.delete_admin_session(_token_hash(token))
        logger.info("admin logout completed")


admin_auth = AdminAuth()
