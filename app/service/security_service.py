from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import struct
import time
import zlib
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.database.security import security_db
from app.errors import AppError, bad_request, not_found

logger = logging.getLogger(__name__)

PUBLIC_VERIFICATION_COOKIE = "kfc-public-verified"
_CHALLENGE_TTL_SECONDS = 5 * 60
_PROCESS_SECRET = secrets.token_bytes(32)
_CAPTCHA_DIGITS = "23456789"

_DIGIT_FONT = {
    "0": ("11111", "10001", "10011", "10101", "11001", "10001", "11111"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("11110", "00001", "00001", "11110", "10000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("10010", "10010", "10010", "11111", "00010", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01111", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "11110"),
}


def login_retry_after_seconds(state: dict[str, Any]) -> int:
    failure_count = int(state.get("failure_count") or 0)
    last_failed_at = state.get("last_failed_at")
    if failure_count < 3 or last_failed_at is None:
        return 0
    lock_seconds = 3600 if failure_count >= 10 else 300 if failure_count >= 6 else 60
    if last_failed_at.tzinfo is None:
        last_failed_at = last_failed_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last_failed_at).total_seconds()
    retry_after = max(0, int(lock_seconds - elapsed + 0.999))
    if retry_after > 0:
        logger.warning("login retry limited failure_count=%s retry_after=%s", failure_count, retry_after)
    return retry_after


def public_verification_required() -> bool:
    required = security_db.invalid_access_link_threshold_exceeded(
        settings().invalid_access_link_threshold
    )
    logger.debug("public verification requirement checked required=%s", required)
    return required


def require_public_access(access_id: str, client_ip: str, verification_token: str | None) -> None:
    verification_required = public_verification_required()
    if verification_required and not is_verified_token_valid(
        verification_token,
        client_ip,
        access_id,
    ):
        logger.warning(
            "public access blocked access_id=%s client_ip=%s reason=verification_required",
            access_id,
            client_ip,
        )
        raise AppError(428, "访问异常，请先完成人机验证")
    if not security_db.access_link_exists(access_id):
        security_db.record_invalid_access_link(access_id, client_ip)
        logger.warning("invalid public access link access_id=%s client_ip=%s", access_id, client_ip)
        raise not_found("链接不存在或已失效")
    logger.info(
        "public access accepted access_id=%s client_ip=%s verification_required=%s",
        access_id,
        client_ip,
        verification_required,
    )


def create_verification_challenge(access_id: str) -> dict[str, Any]:
    if not public_verification_required():
        logger.info("verification challenge skipped access_id=%s required=false", access_id)
        return {"required": False}
    code = "".join(secrets.choice(_CAPTCHA_DIGITS) for _ in range(6))
    nonce = secrets.token_urlsafe(12)
    answer_mac = hmac.new(
        _signing_key(),
        f"captcha:{nonce}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expires_at = int(time.time()) + _CHALLENGE_TTL_SECONDS
    token = _encode_token(
        {
            "kind": "captcha",
            "nonce": nonce,
            "accessId": access_id,
            "answerMac": answer_mac,
            "exp": expires_at,
        }
    )
    image = base64.b64encode(_captcha_png(code)).decode("ascii")
    logger.info("verification challenge created access_id=%s expires_at=%s", access_id, expires_at)
    return {
        "required": True,
        "challengeToken": token,
        "image": f"data:image/png;base64,{image}",
        "expiresIn": _CHALLENGE_TTL_SECONDS,
    }


def verify_challenge(challenge_token: str, answer: str, client_ip: str, access_id: str) -> str:
    logger.info("verification challenge submitted access_id=%s client_ip=%s answer=%s", access_id, client_ip, answer)
    payload = _decode_token(challenge_token, expected_kind="captcha")
    if not hmac.compare_digest(str(payload.get("accessId") or ""), access_id):
        logger.warning("verification challenge failed access_id=%s client_ip=%s reason=access_id", access_id, client_ip)
        raise bad_request("验证已失效，请重新验证")
    normalized_answer = answer.strip()
    if len(normalized_answer) != 6 or not normalized_answer.isdigit():
        logger.warning("verification challenge failed access_id=%s client_ip=%s reason=format", access_id, client_ip)
        raise bad_request("验证码错误")
    expected = hmac.new(
        _signing_key(),
        f"captcha:{payload['nonce']}:{normalized_answer}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, str(payload.get("answerMac") or "")):
        logger.warning("verification challenge failed access_id=%s client_ip=%s reason=answer", access_id, client_ip)
        raise bad_request("验证码错误")
    token = _encode_token(
        {
            "kind": "verified",
            "ip": client_ip,
            "accessId": access_id,
            "nonce": secrets.token_urlsafe(12),
            "exp": int(time.time()) + settings().public_verification_ttl_minutes * 60,
        }
    )
    logger.info("verification challenge passed access_id=%s client_ip=%s", access_id, client_ip)
    return token


def is_verified_token_valid(token: str | None, client_ip: str, access_id: str) -> bool:
    if not token:
        return False
    try:
        payload = _decode_token(token, expected_kind="verified")
    except Exception:
        return False
    return hmac.compare_digest(str(payload.get("ip") or ""), client_ip) and hmac.compare_digest(
        str(payload.get("accessId") or ""),
        access_id,
    )


def _signing_key() -> bytes:
    configured = settings().public_verification_secret.strip()
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()
    return _PROCESS_SECRET


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _encode_token(payload: dict[str, Any]) -> str:
    body = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )
    signature = _b64encode(
        hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def _decode_token(token: str, *, expected_kind: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
        expected_signature = _b64encode(
            hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("signature")
        payload = json.loads(_b64decode(body).decode("utf-8"))
        if payload.get("kind") != expected_kind:
            raise ValueError("kind")
        if int(payload.get("exp") or 0) <= int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception:
        raise bad_request("验证已失效，请重新验证")


def _set_pixel(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    offset = (y * width + x) * 3
    pixels[offset : offset + 3] = bytes(color)


def _draw_line(
    pixels: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        _set_pixel(pixels, width, height, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _captcha_png(code: str) -> bytes:
    width, height = 188, 64
    pixels = bytearray([248]) * (width * height * 3)

    for _ in range(8):
        shade = 190 + secrets.randbelow(45)
        _draw_line(
            pixels,
            width,
            height,
            secrets.randbelow(width),
            secrets.randbelow(height),
            secrets.randbelow(width),
            secrets.randbelow(height),
            (shade, shade, shade),
        )

    scale = 5
    for index, digit in enumerate(code):
        pattern = _DIGIT_FONT[digit]
        base_x = 10 + index * 30 + secrets.randbelow(5) - 2
        base_y = 13 + secrets.randbelow(7) - 3
        shade = 25 + secrets.randbelow(55)
        for row_index, row in enumerate(pattern):
            for column_index, bit in enumerate(row):
                if bit != "1":
                    continue
                for px in range(scale):
                    for py in range(scale):
                        _set_pixel(
                            pixels,
                            width,
                            height,
                            base_x + column_index * scale + px,
                            base_y + row_index * scale + py,
                            (shade, shade, shade),
                        )

    for _ in range(180):
        shade = 130 + secrets.randbelow(100)
        _set_pixel(
            pixels,
            width,
            height,
            secrets.randbelow(width),
            secrets.randbelow(height),
            (shade, shade, shade),
        )

    raw = bytearray()
    row_bytes = width * 3
    for y in range(height):
        raw.append(0)
        start = y * row_bytes
        raw.extend(pixels[start : start + row_bytes])

    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
