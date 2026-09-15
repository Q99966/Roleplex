"""工具说明的示例可直接解析，字段帮助与模型实际收到的 schema 一致。"""
import json
import pytest
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from test_workspace_commands import command_root, isolated_command_database, command_conversation


def test_exported_tool_fields_have_help_and_examples_validate():
    """通过实际工具转换器检查说明，避免只改常量却没有发送给模型。"""
    from app.workspaces.tools import WORKSPACE_TOOL_SCHEMAS, _tool_description, _tool_parameters
    def placeholder(**kwargs):
        """Args:
            kwargs：仅导出定义，不执行任何操作。
        """
        return ''
    examples = 0
    for name,schema in WORKSPACE_TOOL_SCHEMAS.items():
        description=_tool_description(name,edit_available=True)
        tool=StructuredTool.from_function(placeholder,name=name,description=description,args_schema=schema)
        exported=convert_to_openai_tool(tool)['function']
        assert exported['name']==name and exported['description']==description
        assert exported['parameters']==_tool_parameters(name)
        for field,details in exported['parameters']['properties'].items():
            assert details.get('description'), f'{name}.{field} 缺少参数说明'
        if '例：{' in description:
            example,_=json.JSONDecoder().raw_decode(description.split('例：',1)[1])
            schema.model_validate(example)
            examples+=1
    assert examples>=5


def test_examples_preserve_real_version_requirement_and_mode_exclusion():
    """编辑示例不伪造 hash，模式互斥仍由原 schema 强制执行。"""
    from app.workspaces.tools import WorkspaceReadInput,WorkspaceEditInput
    from app.workspaces.replacements import ReplacementInput
    from pydantic import ValidationError
    example=WorkspaceEditInput.model_fields['replacements'].description.split('例如',1)[1].rstrip('。')
    for item in json.loads(example): ReplacementInput.model_validate(item)
    with pytest.raises(ValidationError):
        WorkspaceReadInput(path='src/game.js',start_line=1,max_bytes=64)
    with pytest.raises(ValidationError):
        WorkspaceEditInput(path='src/game.js',old_text='old',new_text='new')


@pytest.mark.anyio
async def test_budget_policy_accounts_for_parameter_help_without_exposing_more_tools(command_root,isolated_command_database):
    """Args:
        command_root：受控工作区目录。
        isolated_command_database：本轮独立数据库。
    """
    from app.db import SessionLocal
    from app.models import Role,Conversation
    from app.workspaces.tools import workspace_tool_policy
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        async with SessionLocal() as session:
            role=await session.get(Role,rid)
            conversation=await session.get(Conversation,cid)
            policy=await workspace_tool_policy(session,conversation=conversation,role=role,triggered_by_user_id=role.created_by)
            assert [tool['name'] for tool in policy['exposed_tools']]==['workspace_run_command']
            assert policy['exposed_tools'][0]['parameters']['properties']['command']['description']
            denied=await workspace_tool_policy(session,conversation=conversation,role=role,triggered_by_user_id=-1)
            assert denied['exposed_tools']==[]
