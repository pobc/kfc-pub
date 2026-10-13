from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.dependencies import Admin
from app.api.schemas import TaobaoKeywordInput
from app.errors import ok
from app.service import taobao_keyword_service

router = APIRouter(prefix="/api/admin/taobao-keywords", tags=["admin-taobao-keywords"])
KeywordId = Annotated[int, Path(ge=1, le=9223372036854775807)]


@router.get("")
def list_keywords(_: Admin, search: str = Query(default="", max_length=200)):
    return ok(taobao_keyword_service.list_keywords(search))


@router.get("/{keyword_id}")
def get_keyword(keyword_id: KeywordId, _: Admin):
    return ok(taobao_keyword_service.get_keyword(keyword_id))


@router.post("")
def create_keyword(body: TaobaoKeywordInput, _: Admin):
    return ok(taobao_keyword_service.save_keyword(body.keyword, body.url))


@router.put("/{keyword_id}")
def update_keyword(keyword_id: KeywordId, body: TaobaoKeywordInput, _: Admin):
    return ok(taobao_keyword_service.save_keyword(body.keyword, body.url, keyword_id))


@router.delete("/{keyword_id}")
def delete_keyword(keyword_id: KeywordId, _: Admin):
    return ok(taobao_keyword_service.delete_keyword(keyword_id))
