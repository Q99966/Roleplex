from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 本文件位于 `app/config/`，仓库根目录比原来的根模块多一层。
ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
WORLDS_DIR = ROOT_DIR / "worlds"


class Settings(BaseSettings):
    """从环境变量和可选的 `.env` 文件加载应用配置。

    未显式提供的运行时密钥会持久化到数据目录，确保本地服务重启后现有 Token 仍有效、加密配置仍可读取。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    app_name: str = "Roleplex"
    # 空值表示由世界目录推导；显式值保持最高优先级，测试和既有部署不受世界机制影响。
    database_url: str = ""
    world_name: str = Field(default="default", validation_alias="ROLEPLEX_WORLD")
    worlds_dir: str = str(WORLDS_DIR)
    world_control_file: str = ""
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_days: int = 7
    cors_origins: str = "http://localhost:51173,http://127.0.0.1:51173"
    event_buffer_size: int = 512
    # 正常运行默认调用角色配置的真实模型；pytest 与 Playwright 会在各自入口显式开启 fake，
    # 保证自动化回归稳定、离线且不产生模型费用。
    agent_use_fake_provider: bool = False
    # 同时发往 Provider 的工作流执行容量；文件扫描/修改另由既有资源池限制。
    workflow_parallelism: int = Field(default=4, ge=1)
    workspace_resource_wait_seconds: float = Field(default=30, gt=0, le=300)
    pin_budget_ratio: float = 0.30
    # 部署安全 ceiling；每个角色默认 200K，并可在此绝对上限内单独配置。
    max_context_tokens: int = Field(default=2_000_000, ge=4_096, le=2_000_000)
    max_upload_bytes: int = 10 * 1024 * 1024
    workspace_command_timeout_seconds: float = Field(default=30, gt=0, le=300)
    workspace_command_output_bytes: int = Field(default=65_536, ge=1, le=1_048_576)
    workspace_shell_kind: Literal['auto', 'bash', 'powershell'] = 'auto'
    workspace_read_content_bytes: int = Field(default=65536, ge=1024, le=65536)
    workspace_scan_file_bytes: int = Field(default=16 * 1024 * 1024, ge=1024 * 1024, le=64 * 1024 * 1024)
    workspace_scan_total_bytes: int = Field(default=64 * 1024 * 1024, ge=1024 * 1024, le=256 * 1024 * 1024)
    workspace_scan_seconds: float = Field(default=3, ge=0.05, le=30)
    runtime_ready_timeout_seconds: float = Field(default=30, gt=0, le=120)
    log_dir: str = str(LOG_DIR)
    log_level: str = "INFO"
    log_run_kind: Literal["runtime", "unit", "e2e-fake", "e2e-real"] = "runtime"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=10 * 1024 * 1024)
    log_max_seconds: int = Field(default=3600, gt=0, le=3600)
    log_archive_enabled: bool = True
    log_retention_days: int = Field(default=30, gt=0)
    log_max_total_bytes: int = Field(default=1_073_741_824, gt=0)
    log_archive_compresslevel: int = Field(default=6, ge=1, le=9)
    _world_managed: bool = PrivateAttr(default=True)

    @model_validator(mode="after")
    def derive_world_paths(self) -> "Settings":
        """在没有显式数据库地址时从当前世界推导 SQLite 路径。"""
        self._world_managed = not bool(self.database_url)
        if self._world_managed:
            self.database_url = f"sqlite+aiosqlite:///{(self.world_dir / 'roleplex.db').as_posix()}"
        return self

    @property
    def world_managed(self) -> bool:
        """数据库是否由世界目录选择，而不是显式连接串覆盖。"""
        return self._world_managed

    @property
    def world_dir(self) -> Path:
        """返回当前世界目录；兼容模式下仅用于展示，不承载显式数据库。"""
        return Path(self.worlds_dir) / self.world_name

    @property
    def storage_dir(self) -> Path:
        """返回数据库相关文件的根目录。"""
        return self.world_dir if self.world_managed else DATA_DIR

    @property
    def secret_dir(self) -> Path:
        """返回 JWT 与 API Key 加密密钥所属目录。"""
        return self.storage_dir

    @property
    def files_dir(self) -> Path:
        """返回当前世界附件目录。"""
        return self.storage_dir / "files"

    def _load_or_create_secret(self, filename: str, configured: str = "") -> str:
        """加载已配置的密钥，或原子创建本地实例密钥。

        Args:
            filename：相对于数据目录的运行时文件名。
            configured：部署配置显式提供的密钥，可为空。

        Returns:
            配置中提供或持久化的密钥值。
        """
        if configured:
            return configured
        self.secret_dir.mkdir(parents=True, exist_ok=True)
        secret_path = self.secret_dir / filename
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()
        secret = secrets.token_urlsafe(48)
        try:
            secret_path.write_text(secret, encoding="utf-8")
        except FileExistsError:
            return secret_path.read_text(encoding="utf-8").strip()
        return secret

    def resolved_jwt_secret(self) -> str:
        """返回 Token 签名密钥；世界模式强制使用世界文件以保证隔离。"""
        configured = "" if self.world_managed else self.jwt_secret
        return self._load_or_create_secret(".jwt-secret", configured)

    def resolved_api_key_secret(self) -> str:
        """返回用于派生 API Key 加密密钥的实例密钥。"""
        return self._load_or_create_secret(".api-key-secret")

    @property
    def cors_origin_list(self) -> list[str]:
        """将逗号分隔的浏览器来源转换为 CORS 所需的列表格式。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
