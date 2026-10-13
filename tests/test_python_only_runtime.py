from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
TEMPLATES = WEB / "templates"
WEB_STATIC = WEB / "static"


def test_web_is_native_html_css_javascript() -> None:
    assert (TEMPLATES / "admin" / "index.html").is_file()
    assert (WEB_STATIC / "admin" / "css" / "admin.css").is_file()
    assert (WEB_STATIC / "admin" / "js" / "app.js").is_file()
    assert (TEMPLATES / "public" / "order.html").is_file()
    assert (WEB_STATIC / "public" / "css" / "order.css").is_file()
    assert (WEB_STATIC / "public" / "js" / "order.js").is_file()

    static_files = {path.name for path in (ROOT / "static").iterdir() if path.is_file()}
    assert static_files == {"favicon-admin.ico", "favicon-order.ico"}
    assert not (ROOT / "static" / "assets").exists()


def test_city_data_is_baked_into_customer_web() -> None:
    content = (WEB_STATIC / "public" / "js" / "cities.js").read_text(encoding="utf-8")
    assert "1301|石家庄" in content
    assert "4401|广州" in content
    assert "6501|乌鲁木齐" in content


def test_no_node_build_project_remains() -> None:
    assert not (ROOT / "web").exists()
    assert not list(ROOT.rglob("package.json"))
    assert not list(ROOT.rglob("package-lock.json"))
    assert not list(ROOT.rglob("tsconfig*.json"))
    assert not list(ROOT.rglob("vite.config.*"))
    assert not list(ROOT.rglob("*.mjs"))
