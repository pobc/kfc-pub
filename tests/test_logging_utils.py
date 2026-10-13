from __future__ import annotations

import logging
import sys

from app.logging_utils import DailyFileHandler, configure_logging


def test_configure_logging_splits_regular_and_warning_console_output() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    root.handlers = []
    try:
        configure_logging()
        console_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, DailyFileHandler)
        ]
        stdout_handler = next(
            handler for handler in console_handlers if getattr(handler, "_kfc_console_stdout", False)
        )
        stderr_handler = next(
            handler for handler in console_handlers if getattr(handler, "_kfc_console_stderr", False)
        )
        info_record = logging.LogRecord("test", logging.INFO, __file__, 1, "info", (), None)
        warning_record = logging.LogRecord("test", logging.WARNING, __file__, 1, "warning", (), None)

        assert len(console_handlers) == 2
        assert stdout_handler.stream is sys.stdout
        assert stdout_handler.filter(info_record)
        assert not stdout_handler.filter(warning_record)
        assert stderr_handler.stream is sys.stderr
        assert stderr_handler.level == logging.WARNING
    finally:
        for handler in root.handlers:
            if handler not in original_handlers:
                handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
