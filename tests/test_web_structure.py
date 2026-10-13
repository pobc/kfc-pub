from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.web.routes import (
    admin_asset,
    admin_page,
    mount_web,
    public_asset,
    public_order_page,
)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
TEMPLATES = WEB / "templates"
WEB_STATIC = WEB / "static"
ADMIN_SECTIONS = (
    "dashboard",
    "kfc-tickets",
    "products",
    "access-links",
    "import",
    "coupons",
    "batches",
    "redemptions",
)


def _response_text(response: FileResponse) -> str:
    return Path(response.path).read_text(encoding="utf-8")


def test_web_is_plain_html_css_javascript() -> None:
    assert (TEMPLATES / "admin" / "index.html").is_file()
    assert (TEMPLATES / "admin" / "login.html").is_file()
    assert (TEMPLATES / "public" / "order.html").is_file()
    assert (WEB_STATIC / "admin" / "css" / "admin.css").is_file()
    assert (WEB_STATIC / "admin" / "js" / "app.js").is_file()
    assert (WEB_STATIC / "public" / "css" / "order.css").is_file()
    assert (WEB_STATIC / "public" / "js" / "order.js").is_file()
    for section in ADMIN_SECTIONS:
        assert (TEMPLATES / "admin" / section / "index.html").is_file()

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in WEB.rglob("*")
        if path.is_file() and path.suffix in {".html", ".js", ".css"}
    )
    assert "vue" not in source.lower()
    assert "vite" not in source.lower()
    assert "node_modules" not in source


def test_web_routes_serve_pages_and_assets() -> None:
    app = FastAPI()
    mount_web(app)
    admin_base = f"/{settings().admin_web_md5}/admin"
    route_paths = {route.path for route in app.routes}

    assert "/admin" not in route_paths
    assert admin_base in route_paths
    assert f"{admin_base}/{{path:path}}" in route_paths
    assert "/frontend/admin/{path:path}" in route_paths
    assert "/order/{access_id}" in route_paths
    assert "/frontend/public/{path:path}" in route_paths

    assert "ADMIN WORKSPACE" in _response_text(admin_page())
    assert "欢迎回来" in _response_text(admin_page("login"))
    assert "卡券管理" in _response_text(admin_asset("coupons/index.html"))
    assert Path(admin_asset("js/app.js").path).name == "app.js"
    assert Path(admin_asset("css/admin.css").path).name == "admin.css"
    order_html = _response_text(public_order_page("example-access-id"))
    assert "选择取餐餐厅" in order_html
    assert "processing-view" in order_html
    assert Path(public_asset("js/order.js").path).name == "order.js"
    assert Path(public_asset("css/order.css").path).name == "order.css"


def test_admin_hidden_elements_stay_hidden() -> None:
    admin_index = (TEMPLATES / "admin" / "index.html").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in admin_index


def test_web_blocks_directory_traversal() -> None:
    try:
        admin_asset("../../README.md")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("web path traversal must return 404")
