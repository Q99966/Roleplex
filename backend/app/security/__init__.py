"""认证、授权与凭据安全的稳定导入入口。"""

from .credentials import decrypt_api_key, encrypt_api_key
from .passwords import PasswordPolicyError, hash_password, verify_password
from .tokens import (
    PASSWORD_RESET_CLAIM,
    create_access_token,
    get_current_user,
    get_current_user_pending_password_reset,
    require_owner,
    token_requires_password_reset,
)

__all__ = [
    "PASSWORD_RESET_CLAIM",
    "PasswordPolicyError",
    "create_access_token",
    "decrypt_api_key",
    "encrypt_api_key",
    "get_current_user",
    "get_current_user_pending_password_reset",
    "hash_password",
    "require_owner",
    "token_requires_password_reset",
    "verify_password",
]
