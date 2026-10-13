from __future__ import annotations

import logging

import uvicorn

from app.api.app import app
from app.config import settings
from app.web import mount_web

# 保留开发环境 reload 热重载，但屏蔽 watchfiles 的文件变更 INFO 日志。
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

mount_web(app)


def main() -> None:
    config = settings()
    development = config.environment.strip().lower() == "development"
    print(f"KFC FastAPI 运行中：http://localhost:{config.port}", flush=True)
    print("关闭窗口或 Ctrl+C 即停止。", flush=True)
    uvicorn.run(
        "main:app",
        host=config.host,
        port=config.port,
        reload=development,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
