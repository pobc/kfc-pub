from __future__ import annotations

from app.service.kfc import capture_service
from app.service.kfc_account_service import import_kfc_auth_state
from app.database import db


def main() -> None:
    db.open()
    try:
        task = capture_service.start()
        print("已打开 KFC 登录窗口：", task["loginUrl"])
        input("完成手机号/验证码登录后按 Enter 保存登录状态...")
        state = capture_service.capture(task["captureId"])
        name = input("Session 名称（默认 KFC Session）：").strip() or "KFC Session"
        saved = import_kfc_auth_state(name, state)
        capture_service.close(task["captureId"])
        print("保存成功：", saved["id"], saved["userCode"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
