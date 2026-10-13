from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable


def log_redemption_failure(
    logger: logging.Logger,
    job_id: object,
    stage: str,
    exc: Exception,
    *,
    secrets: Iterable[str] = (),
) -> None:
    """Keep the failing call stack and Playwright reason without logging login URLs."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    for value in sorted({value for value in secrets if value}, key=len, reverse=True):
        detail = detail.replace(value, "<REDACTED>")
    detail = re.sub(r'https?://[^\s<>"\']+', "<URL REDACTED>", detail)
    detail = re.sub(
        r"(?i)((?:couponCode|coupon_code|ticket|token|cookie|authorization|openId|deviceId)\s*[=:]\s*)[^\s,;]+",
        r"\1<REDACTED>",
        detail,
    )
    logger.error("[核销] job=%s stage=%s error=%s\n%s", job_id, stage, type(exc).__name__, detail.rstrip())
