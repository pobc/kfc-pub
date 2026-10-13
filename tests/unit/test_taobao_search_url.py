from urllib.parse import parse_qs, quote, urlsplit

import pytest

from app.service.taobao_price_scraper import _normalize_search_url
from app.service.taobao_price_service import TaobaoPriceService
from app.errors import AppError


@pytest.mark.parametrize("encoding", ["utf-8", "gbk", "gb2312", "gb18030"])
def test_search_keyword_keeps_its_declared_encoding(encoding: str) -> None:
    """服务入口和抓取器会各处理一次链接，两次都不能破坏搜索词。"""
    keyword = "肯德基代下单"
    encoded = quote(keyword, encoding=encoding)
    url = f"https://s.taobao.com/search?q={encoded}&ie={encoding}&page=7&tab=all"

    result = _normalize_search_url(_normalize_search_url(url))

    assert parse_qs(urlsplit(result).query, encoding=encoding, errors="strict")["q"] == [keyword]
    assert result == f"https://s.taobao.com/search?q={encoded}&ie={encoding}&tab=all&page=1"


def test_only_page_changes_in_search_url() -> None:
    """转义大小写、空格、重复参数和片段由输入链接拥有，不能被页码处理重写。"""
    query = "q=%e8%82%af%e5%be%b7%e5%9f%ba%20KFC%2B&ie=utf8&tag=a&empty=&tag=b&page=7&%70age=8"
    url = f"https://s.taobao.com/search?{query}#results"

    result = _normalize_search_url(url)

    assert result == (
        "https://s.taobao.com/search?q=%e8%82%af%e5%be%b7%e5%9f%ba%20KFC%2B"
        "&ie=utf8&tag=a&empty=&tag=b&page=1#results"
    )


@pytest.mark.parametrize(
    "keyword",
    [
        "%E8%82%AF%E5%BE%B7%E5%9F%BA%E4",
        "%E8%82%AF%E5%BE%B7%E5%9F%BA%EF%BF%BD",
        "肯德基�",
        "%E8%82%AF%E5%BE%B7%E5%9F%BA%",
        "%E8%82%AF%E5%BE%B7%E5%9F%BA%ZZ",
    ],
)
def test_damaged_search_keyword_is_rejected(keyword: str) -> None:
    """无法还原的中文必须在打开淘宝之前报错，不能静默替换或猜测缺失字符。"""
    with pytest.raises(ValueError, match="搜索词.*损坏.*重新复制"):
        _normalize_search_url(f"https://s.taobao.com/search?q={keyword}&ie=utf8&page=7")


def test_utf8_keyword_without_encoding_parameter_is_supported() -> None:
    result = _normalize_search_url("https://s.taobao.com/search?q=肯德基&tab=all")

    assert parse_qs(urlsplit(result).query)["q"] == ["肯德基"]


def test_unknown_search_encoding_is_rejected() -> None:
    with pytest.raises(ValueError, match="搜索链接编码.*重新复制"):
        _normalize_search_url("https://s.taobao.com/search?q=KFC&ie=unknown")


def test_invalid_link_leaves_task_idle_and_logs_reason(caplog: pytest.LogCaptureFixture) -> None:
    """输入校验失败应保留任务状态，并在关闭调试日志时仍能定位原因。"""
    service = TaobaoPriceService()
    before = service.status()

    with caplog.at_level("WARNING", logger="kfc.taobao_price"):
        with pytest.raises(AppError, match="搜索词编码已损坏") as error:
            service.start("https://s.taobao.com/search?q=%E8%82%AF%E5%BE%B7%E5%9F%BA%E4&ie=utf8")

    assert error.value.status_code == 400
    assert service.status() == before
    assert "operation=validate_search_url result=rejected" in caplog.text
    assert "搜索词编码已损坏" in caplog.text
    assert "https://" not in caplog.text
