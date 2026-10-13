from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER_HTML = ROOT / "app/web/templates/public/order.html"
ORDER_CSS = ROOT / "app/web/static/public/css/order.css"
ORDER_JS = ROOT / "app/web/static/public/js/order.js"


class _ViewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.views: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        element_id = attr_map.get("id")
        if element_id and element_id.endswith("-view"):
            self.views[element_id] = attr_map


def test_hidden_attribute_cannot_be_overridden_by_grid_styles() -> None:
    css = re.sub(r"\s+", "", ORDER_CSS.read_text(encoding="utf-8")).lower()
    assert "[hidden]{display:none!important}" in css


def test_only_loading_view_is_initially_visible() -> None:
    parser = _ViewParser()
    parser.feed(ORDER_HTML.read_text(encoding="utf-8"))

    assert "hidden" not in parser.views["loading-view"]
    for view_id, attrs in parser.views.items():
        if view_id == "loading-view":
            continue
        assert "hidden" in attrs, f"{view_id} must start hidden"


def test_pickup_view_has_one_number_and_renders_order_items() -> None:
    html = ORDER_HTML.read_text(encoding="utf-8")
    script = ORDER_JS.read_text(encoding="utf-8")
    pickup_html = html.split('<section id="pickup-view"', 1)[1].split("</section>", 1)[0]

    assert pickup_html.count("取餐号") == 1
    assert "排队号" not in pickup_html
    assert "取餐码" not in pickup_html
    assert 'id="pickup-number"' in pickup_html
    assert 'id="pickup-order-number"' in pickup_html
    assert 'id="pickup-item-list"' in pickup_html
    assert "pickupNumberFor(order)" in script
    assert "order.items" in script
    assert "pickup-item-list" in script
    assert "pickup-item-components" in script
    assert "state.order?.stopPolling" in script
    assert "!pickupNumberFor(state.order)" in script
    assert 'id="pickup-loading"' in pickup_html
    assert 'class="pickup-spinner"' in pickup_html
    assert "if(link.status==='COMPLETED'){setStep('pickup');await pollOrderInfo();return;}" in script
