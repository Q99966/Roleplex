"""有界参数校验反馈，只返回 schema 字段路径及固定原因，不回显值或异常正文。"""
import json
import re

_REASON = {
    'missing': 'missing', 'extra_forbidden': 'unexpected_field', 'value_error': 'invalid_combination',
    'string_type': 'wrong_type', 'int_type': 'wrong_type', 'int_parsing': 'wrong_type', 'list_type': 'wrong_type',
    'dict_type': 'wrong_type', 'bool_type': 'wrong_type', 'float_type': 'wrong_type', 'literal_error': 'invalid_choice',
    'greater_than': 'out_of_range', 'greater_than_equal': 'out_of_range', 'less_than': 'out_of_range', 'less_than_equal': 'out_of_range',
    'string_too_long': 'out_of_range', 'string_too_short': 'out_of_range', 'too_long': 'out_of_range', 'too_short': 'out_of_range',
}


def argument_error(code: str, error=None, schema=None) -> dict:
    """Args:
        code：宿主固定错误码。
        error：可选 schema 校验异常，只提取类型与路径。
        schema：实际工具 schema，用于限制可返回的字段名。
    """
    names = set()
    if schema is not None:
        document = schema.model_json_schema() if hasattr(schema, 'model_json_schema') else schema.schema()
        def collect(value):
            """Args:
                value：声明 schema 的一个节点，不读取参数值。
            """
            if isinstance(value, dict):
                names.update(name for name in value.get('properties', {}) if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name))
                for child in value.values(): collect(child)
            elif isinstance(value, list):
                for child in value: collect(child)
        collect(document)
    issues = []
    if error is not None:
        try: errors = error.errors(include_input=False, include_context=False, include_url=False)
        except TypeError: errors = error.errors()
        for item in errors[:8]:
            path = [part if type(part) is int and 0 <= part <= 1_000_000 else part if isinstance(part,str) and part in names else '<field>' for part in item.get('loc', ())[:8]]
            issues.append({'path':path, 'reason':_REASON.get(item.get('type'), 'invalid_value')})
    elif code == 'TOOL_ARGUMENT_JSON_INVALID':
        issues.append({'path':[], 'reason':'invalid_json'})
    return {'error_code':code, 'issues':issues}


def rejection_text(detail: dict) -> str:
    """Args:
        detail：仅含宿主固定错误码和安全诊断的对象。
    """
    from .tools import REJECTED_OUTPUT_PREFIX
    return REJECTED_OUTPUT_PREFIX + ' ' + json.dumps({**detail, 'executed':False,
        'recovery':'请按工具定义修正参数后重新调用；本次没有执行，不要重复已成功的其他调用。'},ensure_ascii=False,separators=(',',':'))



def safe_exception_type(error: Exception) -> str:
    """Args:
        error：仅返回预先批准的异常类标签，不读取异常正文。
    """
    name = type(error).__name__
    return name if name in {'ValidationError','TypeError','ValueError','RuntimeError','KeyError','AttributeError',
        'IndexError','OperationalError','IntegrityError','TimeoutError','JSONDecodeError','OSError'} else 'OtherError'


def execution_error_text(_error) -> str:
    """Args:
        _error：工具主动报告的 ToolException；不回显可能含私有内容的异常正文。
    """
    from .tools import FAILED_OUTPUT_PREFIX
    return FAILED_OUTPUT_PREFIX + ' ' + json.dumps({'error_code':'TOOL_EXECUTION_FAILED',
        'result_state':'unknown','recovery':'工具报告执行失败。先核对实际结果，再调整方法；不要盲目重放可能有副作用的操作。'},ensure_ascii=False,separators=(',',':'))
