"""模型厂商凭据的加密边界。"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings


def _fernet() -> Fernet:
    """根据实例密钥构造厂商 API Key 加密器。"""
    secret = settings.resolved_api_key_secret().encode()
    # Fernet 需要 32 字节的 URL 安全 Base64 密钥，这里根据实例密钥确定性派生。
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt_api_key(value: str) -> str:
    """在厂商 Key 写入数据库前进行加密。"""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_api_key(value: str) -> str:
    """仅为已授权的内部集成解密厂商 Key。"""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("无法解密模型厂商 API Key") from exc

