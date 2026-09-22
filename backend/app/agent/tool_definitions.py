"""统一模型工具定义的规范化格式；描述、预算和实际绑定不各自拼参数结构。"""
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool


def tool_definition(name, description, schema):
    """从真实参数模型生成 Provider 使用的定义，不创建可执行回调或打开外部资源。"""
    return convert_to_openai_tool(StructuredTool(name=name, description=description, args_schema=schema))['function']
