from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    """从环境变量和可选的 `.env` 文件加载应用配置。

    未显式提供的运行时密钥会持久化到数据目录，确保本地服务重启后现有 Token 仍有效、加密配置仍可读取。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Roleplex"
    database_url: str = Field(default=f"sqlite+aiosqlite:///{(DATA_DIR / 'roleplex.db').as_posix()}")
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_days: int = 7
    cors_origins: str = "http://localhost:51173,http://127.0.0.1:51173"
    event_buffer_size: int = 512
    pin_budget_ratio: float = 0.30
    max_context_tokens: int = 100_000
    max_upload_bytes: int = 10 * 1024 * 1024

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
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        secret_path = DATA_DIR / filename
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()
        secret = secrets.token_urlsafe(48)
        try:
            secret_path.write_text(secret, encoding="utf-8")
        except FileExistsError:
            return secret_path.read_text(encoding="utf-8").strip()
        return secret

    def resolved_jwt_secret(self) -> str:
        """返回用于校验和签发访问 Token 的签名密钥。"""
        return self._load_or_create_secret(".jwt-secret", self.jwt_secret)

    def resolved_api_key_secret(self) -> str:
        """返回用于派生 API Key 加密密钥的实例密钥。"""
        return self._load_or_create_secret(".api-key-secret")

    @property
    def cors_origin_list(self) -> list[str]:
        """将逗号分隔的浏览器来源转换为 CORS 所需的列表格式。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
