"""显式注册的类型白名单；创建选最新版，已存档版本必须精确匹配。"""
import re

from .contracts import WorldTypeDescriptor
from ..worlds.compatibility import WorldTypeUnavailable

_types: dict[tuple[str, int], WorldTypeDescriptor] = {}
_installed = False


def _load_installed():
    global _installed
    if _installed:
        return
    from .installed import installed_types
    for definition in installed_types():
        register(definition)
    _installed = True


def valid_type_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) is not None


def register(descriptor: WorldTypeDescriptor):
    """在应用启动前注册受信实现；重复身份和缺少资料验证器都拒绝。"""
    if not valid_type_id(descriptor.id) or type(descriptor.version) is not int or descriptor.version < 1:
        raise ValueError('WORLD_TYPE_DESCRIPTOR_INVALID')
    if bool(descriptor.build_context) != bool(descriptor.validate_sources):
        raise ValueError('WORLD_TYPE_SOURCE_VALIDATOR_REQUIRED')
    if len({a.name for a in descriptor.activities}) != len(descriptor.activities) or any(
        not valid_type_id(a.name) or 'request_key' in a.parameters_model.model_fields for a in descriptor.activities):
        raise ValueError('WORLD_TYPE_DESCRIPTOR_INVALID')
    key = (descriptor.id, descriptor.version)
    if key in _types:
        raise ValueError('WORLD_TYPE_ALREADY_REGISTERED')
    _types[key] = descriptor


def unregister(type_id, version):
    """测试/受信装配使用；生产执行期间不热卸载类型。"""
    if type_id != 'general':
        _types.pop((type_id, version), None)


def resolve(type_id='general', version=None):
    _load_installed()
    if not valid_type_id(type_id) or version is not None and (type(version) is not int or version < 1):
        raise WorldTypeUnavailable('WORLD_TYPE_UNAVAILABLE')
    if version is None:
        version = max((v for kind, v in _types if kind == type_id), default=0)
    descriptor = _types.get((type_id, version))
    if descriptor is None:
        raise WorldTypeUnavailable('WORLD_TYPE_UNAVAILABLE')
    return descriptor


def catalog():
    _load_installed()
    return [resolve(kind).view() for kind in sorted({kind for kind, _ in _types})]


register(WorldTypeDescriptor(id='general', version=1, name='通用世界',
    description='管理角色、会话和工作流，自由安排协作任务。'))
