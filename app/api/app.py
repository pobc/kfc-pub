from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import db
from app.errors import AppError, app_error_handler, validation_error_handler
from app.logging_utils import configure_logging
from app.service.kfc import kfc_h5
from app.service.kfc.init_session import kfc_init_session
from app.service.kfc.session_capture import capture_service
from app.service.redemption_worker import redemption_worker
from app.service.taobao_price_service import taobao_price_service
from app.service.taobao_login_service import taobao_login_service

from .ip_country_guard import enforce_china_ip_only
from .routes import include_routes

configure_logging()
logger = logging.getLogger("kfc.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open()
    redemption_worker.start()
    logger.info("FastAPI backend started on port %s", settings().port)
    try:
        yield
    finally:
        logger.info("FastAPI backend stopping")
        redemption_worker.stop()
        taobao_price_service.stop()
        taobao_login_service.close_all()
        capture_service.close_all()
        kfc_init_session.close()
        kfc_h5.close()
        db.close()
        logger.info("FastAPI backend stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="KFC 专属链接点餐 API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings().web_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(enforce_china_ip_only)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            logger.exception("HTTP request failed method=%s path=%s", request.method, request.url.path)
            raise
        finally:
            duration = (time.perf_counter() - started) * 1000
            logger.info(
                "HTTP %s %s status=%s client=%s duration=%.1fms",
                request.method,
                request.url.path,
                status_code,
                request.client.host if request.client else "unknown",
                duration,
            )

    include_routes(app)
    return app


app = create_app()
