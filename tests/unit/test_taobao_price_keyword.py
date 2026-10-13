"""验证抓取清理使用真实搜索关键词，且在登录和数据库操作前拒绝不明确的来源。"""

from urllib.parse import quote

import pytest

from app.errors import AppError
from app.service.taobao_price_service import TaobaoPriceService, _search_keyword_from_url


@pytest.mark.parametrize("encoding", ["utf-8", "gbk", "gb2312", "gb18030"])
def test_source_keyword_respects_search_url_encoding(encoding):
    keyword = "肯德基 代下单+套餐"
    url = f"https://s.taobao.com/search?q={quote(keyword, encoding=encoding)}&ie={encoding}&page=8"
    assert _search_keyword_from_url(url) == keyword


def test_source_keyword_ignores_page_and_preserves_literal_wildcards():
    assert _search_keyword_from_url("https://s.taobao.com/search?q=100%25_折扣&page=2") == "100%_折扣"
    assert _search_keyword_from_url("https://s.taobao.com/search?q=肯德基&q=肯德基") == "肯德基"
    assert _search_keyword_from_url("https://s.taobao.com/search?q=KFC&ie=gbk&tracking=%FF") == "KFC"


@pytest.mark.parametrize("query", ["", "q=", "q=+++", "q=肯德基&q=麦当劳", "q=" + "词" * 201])
def test_invalid_keyword_is_rejected_before_starting_scrape(query, caplog):
    service = TaobaoPriceService()
    before = service.status()
    with caplog.at_level("WARNING", logger="kfc.taobao_price"):
        with pytest.raises(AppError, match="关键词") as error:
            service.start("https://s.taobao.com/search?" + query)
    assert error.value.status_code == 400
    assert service.status() == before
    assert "operation=validate_search_url result=rejected" in caplog.text
