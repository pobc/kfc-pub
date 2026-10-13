from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def bad_request(message: str) -> AppError:
    return AppError(400, message)


def unauthorized(message: str) -> AppError:
    return AppError(401, message)


def not_found(message: str) -> AppError:
    return AppError(404, message)


def conflict(message: str) -> AppError:
    return AppError(409, message)


def bad_gateway(message: str) -> AppError:
    return AppError(502, message)


def unavailable(message: str) -> AppError:
    return AppError(503, message)


def ok(data: Any) -> dict[str, Any]:
    return {"data": data}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"message": exc.message})


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    message = str(errors[0].get("msg", "请求参数无效")) if errors else "请求参数无效"
    return JSONResponse(status_code=400, content={"message": message})
