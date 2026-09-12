"""原生文件工具的稳定分类，供权限、采集与能力查询共用，不授予执行权限。"""

WORKSPACE_MUTATION_TOOLS = ('workspace_write', 'workspace_edit')
WORKSPACE_CAPTURE_TOOLS = (*WORKSPACE_MUTATION_TOOLS, 'workspace_read')
WORKSPACE_FILE_TOOLS = ('workspace_list', 'workspace_read', *WORKSPACE_MUTATION_TOOLS)
