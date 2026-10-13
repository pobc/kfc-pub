import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: Path) -> str:
    return "\n".join(file.read_text(encoding="utf-8") for file in path.rglob("*.py"))


def test_backend_is_split_into_layers() -> None:
    for path in (
        "app/api",
        "app/service",
        "app/database",
        "app/frontend",
    ):
        assert (ROOT / path).is_dir()
    assert not (ROOT / "app" / "business").exists()
    assert (ROOT / "app" / "constants.py").is_file()
    assert (ROOT / "app" / "utils.py").is_file()
    assert not (ROOT / "app" / "core").exists()


def test_no_code_uses_core_import_paths() -> None:
    source = _source(ROOT / "app") + "\n" + _source(ROOT / "tools")
    assert "from app.core" not in source
    assert "import app.core" not in source


def test_service_modules_are_flat_except_kfc() -> None:
    service = ROOT / "app" / "service"
    required = {
        "access_link_service.py",
        "admin_service.py",
        "auth_service.py",
        "checkout_service.py",
        "kfc_account_service.py",
        "redemption_worker.py",
    }
    files = {path.name for path in service.glob("*.py") if path.name != "__init__.py"}
    assert required <= files
    assert not list(service.rglob("service.py"))

    subdirs = {path.name for path in service.iterdir() if path.is_dir() and path.name != "__pycache__"}
    assert subdirs == {"kfc"}


def test_kfc_modules_live_under_service_kfc() -> None:
    kfc = ROOT / "app" / "service" / "kfc"
    files = {path.name for path in kfc.glob("*.py") if path.name != "__init__.py"}
    assert {
        "h5_client.py",
        "coupon_sync.py",
        "init_session.py",
        "session_capture.py",
        "http_client.py",
        "h5_config.py",
        "contracts.py",
        "query_order_detail.py",
    } <= files
    assert "store_client.py" not in files
    assert "checkout_client.py" not in files
    assert "account_service.py" not in files


def test_no_code_uses_legacy_business_import_paths() -> None:
    source = _source(ROOT / "app") + "\n" + _source(ROOT / "tools")
    legacy_imports = (
        "from app.business",
        "import app.business",
        "from app.capture import",
        "import app.capture",
        "from app.kfc import",
        "import app.kfc",
        "from app.kfc_coupon import",
        "import app.kfc_coupon",
        "from app.kfc_h5 import",
        "import app.kfc_h5",
        "from app.services import",
        "import app.services",
    )
    for legacy_import in legacy_imports:
        assert legacy_import not in source, legacy_import


def test_service_layer_never_executes_sql() -> None:
    sql_literal = re.compile(r"[\"']\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
    for file in (ROOT / "app" / "service").rglob("*.py"):
        if file.name == "__init__.py":
            continue
        source = file.read_text(encoding="utf-8")
        assert "db.connection(" not in source, file
        assert "conn.execute(" not in source, file
        assert not sql_literal.search(source), file


def test_database_layer_does_not_depend_on_service_or_kfc_http() -> None:
    source = _source(ROOT / "app" / "database")
    assert "from app.service" not in source
    assert "import app.service" not in source
    assert "from app.business" not in source
    assert "import app.business" not in source
    assert "from app.kfc" not in source
    assert "import requests" not in source
    assert "import httpx" not in source


def test_database_modules_are_grouped_and_named_consistently() -> None:
    database = ROOT / "app" / "database"
    required_domains = {"access", "coupon", "kfc", "order", "platform", "product", "security", "user"}
    domain_dirs = {path.name: path for path in database.iterdir() if path.is_dir() and path.name != "__pycache__"}
    assert required_domains <= set(domain_dirs)
    for name in required_domains:
        files = [path for path in domain_dirs[name].glob("*.py") if path.name != "__init__.py"]
        assert files, domain_dirs[name]
        assert all(path.name.endswith("_db.py") for path in files), domain_dirs[name]
    assert not (database / "repositories").exists()
    assert not list(database.rglob("*_repository.py"))
    assert (database / "db_connection.py").is_file()
    assert (database / "schema.py").is_file()
    assert (database / "integrity_db.py").is_file()
    assert not (database / "connection.py").exists()


def test_no_code_uses_database_repositories_import_path() -> None:
    legacy = "app.database." + "repositories"
    source = _source(ROOT / "app") + "\n" + _source(ROOT / "tools") + "\n" + _source(ROOT / "tests")
    assert legacy not in source


def test_kfc_external_calls_live_in_service_layer() -> None:
    source = (ROOT / "app" / "service" / "kfc_account_service.py").read_text(encoding="utf-8")
    assert "kfc_coupon_sync.list_coupons" in source


def test_public_api_does_not_directly_expose_kfc_client_contracts() -> None:
    boundary_files = (
        ROOT / "app" / "api" / "routers" / "public" / "access.py",
        ROOT / "app" / "api" / "routers" / "public" / "checkout.py",
    )
    forbidden_import_markers = (
        "app.service.kfc",
        "kfc_h5",
        "KfcH5Client",
        "STORE_SEARCH_API",
        "CART_USE_COUPON_CODE_API",
        "ORDER_STATUS_API",
    )
    for file in boundary_files:
        source = file.read_text(encoding="utf-8")
        for marker in forbidden_import_markers:
            assert marker not in source, f"{file}: {marker}"

    checkout_service = (ROOT / "app" / "service" / "checkout_service.py").read_text(encoding="utf-8")
    assert '"storePayload"' not in checkout_service
    assert '"rawResponse"' not in checkout_service
    assert "/preorder-portal/api/" not in checkout_service
    assert "_PUBLIC_STORE_FIELDS" in checkout_service
    assert "_PUBLIC_PRODUCT_FIELDS" in checkout_service
    assert "_PUBLIC_ORDER_FIELDS" in checkout_service

    schemas = (ROOT / "app" / "api" / "schemas.py").read_text(encoding="utf-8")
    apply_schema = schemas.split("class ApplyCouponInput", 1)[1]
    assert "store: dict" not in apply_schema
    assert 'ConfigDict(extra="forbid")' in schemas

    frontend_api = (ROOT / "app" / "frontend" / "public" / "js" / "api.js").read_text(encoding="utf-8")
    assert "JSON.stringify({ storeCode, packType, items })" in frontend_api
    assert "JSON.stringify({ storeCode, store, packType, items })" not in frontend_api


def test_root_main_is_single_application_entry() -> None:
    entry = ROOT / "main.py"
    source = entry.read_text(encoding="utf-8")
    assert entry.is_file()
    assert "from app.api.app import app" in source
    assert "from app.config import settings" in source
    assert "mount_frontend(app)" in source
    assert "config.host" in source
    assert "config.port" in source
    assert "reload=development" in source
    assert "access_log=False" in source
    assert "uvicorn.run(" in source
    assert '"main:app"' in source
    assert "@app." not in source
    assert not (ROOT / "app" / "main.py").exists()
