from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from app.config import settings
from app.utils import mask

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "deviceid",
    "device_id",
    "key_user",
    "openid",
    "open_id",
    "phone",
    "sessionid",
    "session_id",
    "ticket",
    "token",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def development_logging_enabled() -> bool:
    return settings().environment.strip().lower() == "development"


def _redact(value: Any, key: str = "") -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS:
        return mask(str(value or ""))
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "<unserializable>"


def log_sensitive(logger: logging.Logger, message: str, value: Any, *args: Any) -> None:
    logger.info(message, *args, safe_json(value))


class DailyFileHandler(logging.Handler):
    """Write logs to logs/YYYY-MM-DD.log and switch files at midnight."""

    def __init__(self, log_dir: Path = LOG_DIR) -> None:
        super().__init__()
        self.log_dir = log_dir
        self._date = ""
        self._stream: TextIO | None = None

    def _stream_for(self, created: float) -> TextIO:
        date = datetime.fromtimestamp(created).strftime("%Y-%m-%d")
        if self._stream is not None and self._date == date:
            return self._stream
        if self._stream is not None:
            self._stream.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._date = date
        self._stream = (self.log_dir / f"{date}.log").open("a", encoding="utf-8")
        return self._stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = self._stream_for(record.created)
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


class _BelowLevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.level


def configure_logging(level: int = logging.INFO) -> None:
    """Configure application logging once, including daily files under ./logs."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if not any(getattr(handler, "_kfc_daily_file", False) for handler in root.handlers):
        file_handler = DailyFileHandler()
        file_handler._kfc_daily_file = True  # type: ignore[attr-defined]
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not any(getattr(handler, "_kfc_console_stdout", False) for handler in root.handlers):
        console_stdout = logging.StreamHandler(sys.stdout)
        console_stdout._kfc_console_stdout = True  # type: ignore[attr-defined]
        console_stdout.setLevel(level)
        console_stdout.addFilter(_BelowLevelFilter(logging.WARNING))
        console_stdout.setFormatter(formatter)
        root.addHandler(console_stdout)

    if not any(getattr(handler, "_kfc_console_stderr", False) for handler in root.handlers):
        console_stderr = logging.StreamHandler(sys.stderr)
        console_stderr._kfc_console_stderr = True  # type: ignore[attr-defined]
        console_stderr.setLevel(max(level, logging.WARNING))
        console_stderr.setFormatter(formatter)
        root.addHandler(console_stderr)

    logging.getLogger("uvicorn.error").propagate = True
