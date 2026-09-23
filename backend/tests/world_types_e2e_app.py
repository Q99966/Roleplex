"""测试专用 ASGI 装配；生产 app.main 不导入受控类型。"""
from world_types_fixture import install
install()

from app.main import app  # noqa: E402,F401
