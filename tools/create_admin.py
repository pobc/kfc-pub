from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import bcrypt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="交互式创建或重置管理员账号")
    parser.add_argument("--username", required=True, help="管理员用户名")
    args = parser.parse_args()
    username = args.username.strip()
    if not username or len(username) > 80:
        parser.error("用户名不能为空，且不能超过 80 个字符")

    password = getpass.getpass("输入管理员密码（至少 12 位）：")
    if len(password) < 12:
        parser.error("管理员密码至少需要 12 位")
    if password != getpass.getpass("再次输入管理员密码："):
        parser.error("两次密码不一致")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        db.execute(
            '''
            INSERT INTO users (username, password_hash, enabled)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (username)
            DO UPDATE SET password_hash = EXCLUDED.password_hash,
                          enabled = TRUE, "updatedAt" = now()
            ''',
            (username, password_hash),
        )
    finally:
        db.close()
    print(f"管理员 {username} 已设置；密码未保存到代码或终端日志。")


if __name__ == "__main__":
    main()
