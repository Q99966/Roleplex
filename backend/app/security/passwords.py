"""密码策略与哈希：本项目关于密码可用性和存储方式的唯一权威。

注册、改密和登录时的弱密码判定必须全部经过本模块，避免同一规则在
schema、路由和前端各写一份而互相漂移。前端可以复制这里的规则做实时提示，
但服务端判定以本模块为准。

策略要求：
- 至少 10 个字符（按字符计，不按字节计）；
- 同时包含字母、数字和符号三类；
- UTF-8 编码不超过 72 字节。

72 字节上限不是产品偏好，而是 bcrypt 的硬限制：bcrypt 只取前 72 字节，
超出部分被静默截断，导致"80 个字符的密码"与"它的前 72 字节"等价。
如果不在策略层拦住，用户会以为自己设了更长的密码而实际没有。
"""
from __future__ import annotations

import string

import bcrypt

# 长度按字符计：面向用户的"至少 10 位"应该对中文和 emoji 有一致含义。
MIN_LENGTH = 10

# bcrypt 只处理前 72 字节，超出部分被静默丢弃，必须在入口拒绝而不是任其截断。
MAX_BYTES = 72

# 符号取 ASCII 可见的非字母数字字符；空格可以出现在密码里，但不计作符号。
SYMBOLS = frozenset(set(string.punctuation))
_LETTERS = frozenset(set(string.ascii_letters))
_DIGITS = frozenset(set(string.digits))

# 面向用户的规则说明，供接口错误详情和前端提示复用，保证措辞一致。
REQUIREMENTS: tuple[str, ...] = (
    f"至少 {MIN_LENGTH} 个字符",
    "包含字母",
    "包含数字",
    "包含符号（例如 !@#$%^&*）",
)


class PasswordPolicyError(ValueError):
    """密码不满足策略时抛出，携带面向用户的具体原因。

    Args:
        reasons：未通过的具体条目，用于在接口错误详情中逐条回显。
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("；".join(reasons))


def hash_password(password: str) -> str:
    """对用户密码进行哈希，且不保留明文。

    Args:
        password：仅在认证输入阶段接收的明文密码。

    Returns:
        适合存入数据库的 bcrypt 编码密码哈希。
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """使用已存储的 bcrypt 哈希校验候选密码。"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def check(password: str) -> list[str]:
    """返回密码未满足的策略条目；全部满足时返回空列表。

    Args:
        password：待校验的明文密码，仅在内存中使用，不写日志。

    Returns:
        未通过的原因列表，顺序稳定，便于前端逐条展示。
    """
    reasons: list[str] = []
    characters = set(password)
    if len(password) < MIN_LENGTH:
        reasons.append(f"至少需要 {MIN_LENGTH} 个字符")
    if not characters & _LETTERS:
        reasons.append("需要包含字母")
    if not characters & _DIGITS:
        reasons.append("需要包含数字")
    if not characters & SYMBOLS:
        reasons.append("需要包含符号")
    if len(password.encode("utf-8")) > MAX_BYTES:
        # 放在最后：先告诉用户缺什么，再告诉用户太长，提示顺序更自然。
        reasons.append(f"不能超过 {MAX_BYTES} 字节（当前编码后过长）")
    return reasons


def is_compliant(password: str) -> bool:
    """判断密码是否满足当前策略。

    登录时用它识别历史遗留或分发包中的弱密码：判定结果只用于要求改密，
    不用于拒绝登录——分发出去的世界必须先能登进来才有机会改密。
    """
    return not check(password)


def validate(password: str) -> None:
    """校验密码，不合规时抛出 `PasswordPolicyError`。

    Args:
        password：注册或改密时提交的新密码明文。

    Raises:
        PasswordPolicyError：密码不满足策略，异常中携带逐条原因。
    """
    reasons = check(password)
    if reasons:
        raise PasswordPolicyError(reasons)
