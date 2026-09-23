"""随代码安装的世界类型；类型身份来自存档，不从存档加载任意模块。"""
from .contracts import (ActivityPlan, Initialization, WorldActivityDefinition, WorldContext,
    WorldMaterial, WorldToolDefinition, WorldTypeDescriptor)
from .registry import catalog, register, resolve

__all__ = ['ActivityPlan', 'Initialization', 'WorldActivityDefinition', 'WorldContext', 'WorldMaterial',
    'WorldToolDefinition', 'WorldTypeDescriptor', 'assert_active', 'catalog', 'register', 'resolve']


async def assert_active(actor):
    """类型操作的取消/撤权检查；导入注册契约本身不提前初始化数据库配置。"""
    from .service import assert_active as check
    await check(actor)
