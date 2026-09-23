"""产品类型的显式装配点；类型分支在此导入并返回自己的描述器。"""
from .contracts import WorldTypeDescriptor


def installed_types() -> tuple[WorldTypeDescriptor, ...]:
    # general 由核心提供；受控测试类型仅在测试启动器中注册。
    return ()
