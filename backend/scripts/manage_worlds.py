"""Roleplex 世界存档管理 CLI。

在 backend 目录运行：
    python scripts/manage_worlds.py list
    python scripts/manage_worlds.py create <世界名>
    python scripts/manage_worlds.py backup <世界名> --output ../backups
    python scripts/manage_worlds.py adopt <世界名>
    python scripts/manage_worlds.py delete <世界名> --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import DATA_DIR, WORLDS_DIR, settings  # noqa: E402
from app.worlds import WorldActiveError, WorldManager  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """构造世界管理命令行解析器。"""
    parser = argparse.ArgumentParser(description="管理 Roleplex 世界存档")
    parser.add_argument("--worlds-dir", default=str(WORLDS_DIR), help="世界根目录")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出世界")

    create = commands.add_parser("create", help="创建空世界")
    create.add_argument("name")

    backup = commands.add_parser("backup", help="一致性备份世界")
    backup.add_argument("name")
    backup.add_argument("--output", default=str(BACKEND_DIR.parent / "backups"))

    adopt = commands.add_parser("adopt", help="把旧 data 目录复制进世界")
    adopt.add_argument("name")
    adopt.add_argument("--legacy-data-dir", default=str(DATA_DIR))

    delete = commands.add_parser("delete", help="删除世界")
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true", help="确认不可恢复地删除目标世界")
    return parser


def main(argv: list[str] | None = None) -> int:
    """执行世界管理命令并返回退出码。

    Args:
        argv：显式命令行参数；省略时使用进程参数。
    """
    args = build_parser().parse_args(argv)
    manager = WorldManager(args.worlds_dir)
    try:
        if args.command == "list":
            for world in manager.list_worlds():
                marker = "*" if settings.world_managed and world.name == settings.world_name else " "
                print(f"{marker} {world.name}\t{world.created_at}")
            return 0
        if args.command == "create":
            world = manager.create(args.name)
            print(f"已创建世界：{world.name}（{world.path}）")
            return 0
        if args.command == "backup":
            archive = manager.backup(args.name, args.output)
            print(f"备份已创建：{archive}")
            return 0
        if args.command == "adopt":
            world = manager.adopt_legacy(args.name, args.legacy_data_dir)
            print(f"旧数据已接管到：{world.path}；原目录保持不变")
            return 0
        if args.command == "delete":
            if not args.yes:
                print("拒绝删除：必须显式传入 --yes", file=sys.stderr)
                return 2
            # 配置中的默认名不是运行证明；真实租约与未回收登记由管理器原子复核。
            manager.delete(args.name)
            print(f"已删除世界：{args.name}")
            return 0
    except (FileExistsError, FileNotFoundError, WorldActiveError, ValueError, OSError) as exc:
        print(f"世界操作失败：{exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
