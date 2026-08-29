"""真实 Provider + 正常世界包装器的端到端测试启动入口。

本入口与兼容数据库 real-E2E 分层：先在本轮临时世界中生成独立数据库、JWT 密钥和 API Key 加密密钥，
再复用真实播种逻辑，最后 exec 正常世界包装器。真实 Key 不进入浏览器、命令行参数或日志。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from real_e2e_server import _load_env_file, seed_database  # noqa: E402


def _world_root() -> Path:
    """解析本轮受控世界根目录，拒绝缺失配置。"""
    raw = os.environ.get("ROLEPLEX_REAL_WORLD_E2E_ROOT", "").strip()
    if not raw:
        raise RuntimeError("真实世界 E2E 缺少环境变量：ROLEPLEX_REAL_WORLD_E2E_ROOT")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()


def main() -> int:
    """创建并播种临时世界，然后以前台进程运行正常世界包装器。"""
    _load_env_file()
    try:
        root = _world_root()
        port = int(os.environ.get("ROLEPLEX_REAL_WORLD_E2E_API_PORT", "8004"))
        # 必须在任何 app.config 导入前切到世界托管模式，避免 settings 缓存兼容数据库路径。
        os.environ.pop("DATABASE_URL", None)
        os.environ["ROLEPLEX_WORLD"] = "default"
        os.environ["WORLDS_DIR"] = str(root)

        from app.worlds.manager import WorldManager

        WorldManager(root).ensure("default")
        asyncio.run(seed_database())
    except Exception as exc:
        # 被调用函数只抛稳定说明或变量名；禁止把环境字典和 Key 拼进错误。
        print(f"真实世界 E2E 初始化失败：{exc}", file=sys.stderr)
        return 1

    wrapper = BACKEND_DIR / "scripts" / "run_world_server.py"
    command = [
        sys.executable,
        str(wrapper),
        "--world", "default",
        "--worlds-dir", str(root),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    os.execvpe(sys.executable, command, dict(os.environ))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
