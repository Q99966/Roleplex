"""测试账号常量。

实例 Owner 是单例：整轮测试共用同一份数据库，第一个注册者认领 Owner，
之后注册的账号只能是 Guest。因此需要 Owner 权限的用例必须复用同一账号，
不能各自注册新用户，否则会在配置类接口上被 403 拒绝。
"""
from __future__ import annotations

OWNER_USERNAME = "owner_chat"
OWNER_PASSWORD = "password123"
OWNER_NICKNAME = "Owner"
