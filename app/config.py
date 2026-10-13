from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("APP_ENV", "development")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "3000"))
    web_origin: str = os.getenv("WEB_ORIGIN", "http://localhost:3000")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://kfc:change-me@localhost:5432/kfc")
    local_admin_hostname: str = os.getenv("LOCAL_ADMIN_HOSTNAME", "")
    admin_web_md5: str = os.getenv("ADMIN_WEB_MD5", "change-me-admin-path")
    admin_session_ttl_minutes: int = int(os.getenv("ADMIN_SESSION_TTL_MINUTES", "720"))
    public_web_url: str = os.getenv("PUBLIC_WEB_URL", "http://localhost:3000")
    invalid_access_link_threshold: int = int(os.getenv("INVALID_ACCESS_LINK_THRESHOLD", "200"))
    public_verification_ttl_minutes: int = int(os.getenv("PUBLIC_VERIFICATION_TTL_MINUTES", "30"))
    public_verification_secret: str = os.getenv("PUBLIC_VERIFICATION_SECRET", "")
    kfc_h5_base_url: str = os.getenv("KFC_H5_BASE_URL", "https://order.kfc.com.cn").rstrip("/")
    kfc_coupon_base_url: str = os.getenv("KFC_COUPON_BASE_URL", "https://appcoupon.kfc.com.cn/api").rstrip("/")
    kfc_request_timeout_ms: int = int(os.getenv("KFC_REQUEST_TIMEOUT_MS", "30000"))
    kfc_config_cache_ms: int = int(os.getenv("KFC_CONFIG_CACHE_MS", "600000"))
    coupon_lock_timeout_seconds: int = int(os.getenv("COUPON_LOCK_TIMEOUT_SECONDS", "180"))
    capture_task_ttl_seconds: int = int(os.getenv("KFC_CAPTURE_TASK_TTL_SECONDS", "900"))
    taobao_search_url: str = os.getenv(
        "TAOBAO_SEARCH_URL",
        "https://s.taobao.com/search?q=%E8%82%AF%E5%BE%B7%E5%9F%BA%E4%BB%A3%E4%B8%8B%E5%8D%95&page=1",
    )
    taobao_browser_profile_dir: str = os.getenv("TAOBAO_BROWSER_PROFILE_DIR", ".kfc/taobao-price-browser")
    taobao_browser_executable_path: str = os.getenv("TAOBAO_BROWSER_EXECUTABLE_PATH", "")
    taobao_browser_headless: bool = os.getenv("TAOBAO_BROWSER_HEADLESS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    taobao_scrape_max_products: int = int(os.getenv("TAOBAO_SCRAPE_MAX_PRODUCTS", "60"))
    taobao_page_timeout_ms: int = int(os.getenv("TAOBAO_PAGE_TIMEOUT_MS", "60000"))
    taobao_login_timeout_seconds: int = int(os.getenv("TAOBAO_LOGIN_TIMEOUT_SECONDS", "300"))
    taobao_login_capture_ttl_seconds: int = int(os.getenv("TAOBAO_LOGIN_CAPTURE_TTL_SECONDS", "900"))


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
