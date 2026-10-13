from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.config import settings

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parents[1]
TEMPLATES_DIR = WEB_DIR / "templates"
WEB_STATIC_DIR = WEB_DIR / "static"
PROJECT_STATIC_DIR = PROJECT_ROOT / "static"
ADMIN_TEMPLATES_DIR = TEMPLATES_DIR / "admin"
ADMIN_STATIC_DIR = WEB_STATIC_DIR / "admin"
PUBLIC_TEMPLATES_DIR = TEMPLATES_DIR / "public"
PUBLIC_STATIC_DIR = WEB_STATIC_DIR / "public"

DEV_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def _safe_file(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise _not_found()
    return candidate if candidate.is_file() else None


def _file(root: Path, relative_path: str) -> FileResponse:
    path = _safe_file(root, relative_path)
    if not path:
        raise _not_found()
    return FileResponse(path, headers=DEV_NO_CACHE_HEADERS)


def admin_page(path: str = "") -> FileResponse:
    if path == "login":
        return _file(ADMIN_TEMPLATES_DIR, "login.html")
    return _file(ADMIN_TEMPLATES_DIR, "index.html")


def admin_asset(path: str) -> FileResponse:
    if path.startswith(("css/", "js/")):
        return _file(ADMIN_STATIC_DIR, path)
    return _file(ADMIN_TEMPLATES_DIR, path)


def public_order_page(access_id: str) -> FileResponse:
    del access_id
    return _file(PUBLIC_TEMPLATES_DIR, "order.html")


def public_asset(path: str) -> FileResponse:
    if path.startswith(("css/", "js/")):
        return _file(PUBLIC_STATIC_DIR, path)
    return _file(PUBLIC_TEMPLATES_DIR, path)


def static_asset(path: str) -> FileResponse:
    return _file(PROJECT_STATIC_DIR, path)


def mount_web(app: FastAPI) -> None:
    admin_base = f"/{settings().admin_web_md5}/admin"
    app.add_api_route(
        admin_base,
        admin_page,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        f"{admin_base}/{{path:path}}",
        admin_page,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/frontend/admin/{path:path}",
        admin_asset,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/order/{access_id}",
        public_order_page,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/frontend/public/{path:path}",
        public_asset,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/static/{path:path}",
        static_asset,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/",
        lambda: RedirectResponse(url="/order/demo-unused", status_code=307),
        methods=["GET"],
        include_in_schema=False,
    )
