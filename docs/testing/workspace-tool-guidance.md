# 工作区工具说明整理验证

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 状态：说明与字段帮助已整理，验证通过，已获用户人工验收并授权提交。
- 日期：2026-09-15；关联代码：workspaces/tools.py、batch_read.py、batch_mutation.py、replacements.py、service_query.py。
- 不改变工具参数接口、验证规则、权限、文件额度、队列或副作用行为；策略版本更新为 18。

## 调整

description 聚焦用途、关键规则和短示例；Field.description 解释参数含义、互斥形式和动态值来源。
移除开发历史、旧返回字段数量与执行框架术语。保留真实 hash、审批、服务保护、批次/片段总预算、版本冲突、未知结果核对等关键使用边界。
list/read/search/write/command/shell 包含可解析的典型 JSON 示例；编辑的 replacements 提供片段示例，真实 hash 与 runtime_id 必须从前序结果取得，不提供可照抄的虚构值。
只读范围、列表分页、服务日志 next_seq 等名称与实际实现核对。结构化命令的参数对象在模型导出 schema 中属于必填，说明明确 pwd 使用空对象，避免把 Python 的默认工厂误当成模型可见可选字段。

工具定义仍在每次模型请求中一并提供，没有按标题动态加载详情。workspace_tool_policy 仅用于预算估算与指纹，不重复注入系统提示；现在包含实际工具转换器导出的 parameters，避免移动字段说明后漏算或使用不同 schema。

## 大小测量

以当前主机参数导出完整 11 个工具，统一 UTF-8 紧凑 JSON 口径：

| 项目 | 整理前 | 整理后 |
|---|---|---|
| description 正文合计 | 7,047 字节 | 4,736 字节 |
| 完整工具定义 | 14,688 字节 | 17,826 字节 |

正文缩短约 33%，完整定义因字段帮助增加约 21%。这是定义字节数，不是厂商 Token，也不证明总成本下降。实际角色只发送已暴露工具子集。

## 回归

```bash
# backend
python -m pytest -q tests/test_workspace_tool_guidance.py tests/test_context_builder.py tests/test_tool_execution_policy.py --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
# frontend
npm run test:e2e:commands -- search-read.spec.ts replacements.spec.ts
```

读取/编辑/服务发现等初始专项 52 passed。全量 441 passed、3 skipped、18 deselected（305.15 秒）。
全量后将预算 schema 对齐实际工具转换器导出结构，补充逐工具精确比较，相关专项 15 passed（12.77 秒），不与全量相加。
示例通过实际工具定义转换及原 schema 校验，所有顶层参数具有帮助文本，动态 hash 仍强制校验，权限集合不扩大。
普通浏览器回归 2 passed（14.5 秒），覆盖搜索、范围读取、多片段编辑及 Guest 详情隔离。

## 标准真实 World

专用命令会联网消耗额度，执行前已告知，普通 CI 不运行：

```bash
npm run test:e2e:real-world -- tool-guidance-provider.spec.ts
```

使用授权的 Token Rhythm/deepseek-flash；关闭截图、trace/video，Key 仅在后端加密播种。结果 **1 passed（22.6 秒）**。
真实模型搜索 initialScore/resetScore、按行读取相距较远的两个位置、局部编辑并创建 docs/change-note.txt；独立文件比较确认两处得分均为 5，长段无关内容原样保留。
覆盖 workspace_search/read/edit/write，参数拒绝 0 次，5 次模型请求，厂商报告输出合计 1,268 Token；任务正常结束，清理通过，未使用官方 Key。
本例是有明确工具路径要求的兼容性 smoke，不能据单次成功宣称自然任务误用率下降，也不冒充已完成多模型对照实验。

World：`data/roleplex-real-world-e2e-20260915172151/default/`。
会话：`真实工具说明验收`。
账号：`realtest20260915172151`；密码：固定测试占位值 `Roleplex-Real-E2E-1`。
报告：`logs/tests/e2e/real/2026-09-15/17-21-51_90dc7650/`。

2026-09-15 用户反馈本版输出消耗与工具使用流畅度改善，并确认提交；这是人工使用反馈，不替代多次对照的量化结论。
