from fastapi import FastAPI

from .routers.admin import access as admin_access
from .routers.admin import auth as admin_auth
from .routers.admin import coupons as admin_coupons
from .routers.admin import dashboard as admin_dashboard
from .routers.admin import products as admin_products
from .routers.admin import sessions as admin_sessions
from .routers.admin import taobao_prices as admin_taobao_prices
from .routers.admin import taobao_keywords as admin_taobao_keywords
from .routers.health import router as health_router
from .routers.public import access as public_access
from .routers.public import checkout as public_checkout
from .routers.public import security as public_security


def include_routes(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(admin_auth.router)
    app.include_router(admin_dashboard.router)
    app.include_router(admin_products.router)
    app.include_router(admin_coupons.router)
    app.include_router(admin_access.router)
    app.include_router(admin_sessions.router)
    app.include_router(admin_taobao_prices.router)
    app.include_router(admin_taobao_keywords.router)
    app.include_router(public_security.router)
    app.include_router(public_access.router)
    app.include_router(public_checkout.router)
