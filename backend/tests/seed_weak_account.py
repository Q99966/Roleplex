"""端到端测试用的弱口令账号播种脚本。

注册接口会拒绝不合规密码，因此"弱口令账号登录后被强制改密"这条路径无法
从界面构造出来。这里直接写库生成该账号，模拟分发出去的世界或历史遗留账号。

只用于测试库：口令是无实际价值的占位值，不得指向任何真实环境的数据库。
用同步 sqlite3 而不是应用的 async engine，避免与正在运行的后端争抢事件循环；
WAL 下这一次短写入与后端连接并存是安全的。

用法：
    python tests/seed_weak_account.py --database ../data/roleplex-e2e-<时间戳>.db \
        --username <账号名> --password <弱口令> --nickname <昵称>
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import hash_password  # noqa: E402  # 需要先补上 backend 根目录


def main() -> int:
    """插入一个使用弱口令的账号；账号已存在时视为成功并直接返回。

    Returns:
        进程退出码：0 表示账号已就绪，1 表示数据库不存在。
    """
    parser = argparse.ArgumentParser(description="为端到端测试播种弱口令账号")
    parser.add_argument("--database", required=True, help="SQLite 数据库文件路径")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True, help="故意不合规的占位口令")
    parser.add_argument("--nickname", default="弱口令账号")
    args = parser.parse_args()

    database = Path(args.database).resolve()
    if not database.exists():
        print(f"数据库不存在：{database}", file=sys.stderr)
        return 1

    connection = sqlite3.connect(database, timeout=10)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO users"
            " (username, password_hash, nickname, avatar, is_owner, token_version, created_at)"
            " VALUES (?, ?, ?, NULL, 0, 0, ?)",
            (args.username, hash_password(args.password), args.nickname, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    print(f"已就绪：{args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
