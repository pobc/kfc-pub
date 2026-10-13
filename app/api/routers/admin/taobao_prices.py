from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import StringConstraints

from app.api.dependencies import Admin
from app.api.schemas import TaobaoLoginSaveInput, TaobaoPriceScrapeInput
from app.errors import ok
from app.service.taobao_login_service import taobao_login_service
from app.service.taobao_price_service import taobao_price_service

router = APIRouter(prefix="/api/admin", tags=["admin-taobao-prices"])


@router.get("/taobao-prices")
def list_taobao_prices(
    _: Admin,
    search: str = Query(default="", max_length=200),
    sort_by: Literal["shopName", "productUrl", "itemName", "price", "scrapedAt"] = Query(
        default="scrapedAt",
        alias="sortBy",
    ),
    sort_order: Literal["asc", "desc"] = Query(default="desc", alias="sortOrder"),
    page: int = Query(default=1, ge=1, le=1_000_000),
    keywords: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]]
    | None = Query(default=None, max_length=20),
    source_keywords: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]]
    | None = Query(default=None, alias="sourceKeywords", max_length=20),
):
    return ok(
        taobao_price_service.list_prices(
            search,
            sort_by,
            sort_order,
            keywords,
            source_keywords,
            page=page,
        )
    )


@router.get("/taobao-prices/scrape/status")
def taobao_price_scrape_status(_: Admin):
    return ok(taobao_price_service.status())


@router.post("/taobao-prices/scrape")
def start_taobao_price_scrape(body: TaobaoPriceScrapeInput, _: Admin):
    taobao_login_service.ensure_no_active_capture()
    return ok(taobao_price_service.start(body.url))


@router.get("/taobao-login/status")
def taobao_login_status(_: Admin):
    return ok(taobao_login_service.status())


@router.post("/taobao-login/start")
def start_taobao_login(_: Admin):
    return ok(taobao_login_service.start())


@router.post("/taobao-login/save")
def save_taobao_login(body: TaobaoLoginSaveInput, _: Admin):
    return ok(taobao_login_service.save(body.captureId))


@router.delete("/taobao-login/{capture_id}")
def cancel_taobao_login(capture_id: str, _: Admin):
    return ok(taobao_login_service.close(capture_id))
