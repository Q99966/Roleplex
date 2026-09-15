# 工具参数错误恢复与诊断

- 状态：修正及验证完成，用户已确认当前版本并授权提交；复核日期：2026-09-15。
- 权威范围：[完整计划](../plan/tool-reliability-capability-roadmap-v1.md)、[Agent 运行时](../protocol/internal/agent-runtime.md)、[Owner 工具详情](../protocol/public/messaging/tool-details.md)、[错误码](../protocol/error-codes.md)。

## 已确认的问题与修正

原防腐层将 invalid_tool_calls 统一终止为 AGENT_PROTOCOL_ERROR；框架自行生成的部分工具错误结果没有对应完整宿主事件，也可能令 pending 调用一直未配对。
现在，具有可靠调用身份的参数 JSON/schema 错误及不可用工具，在正文执行前返回结构化未执行反馈，模型可在原授权决策预算内修正。同响应合法调用独立执行，不重放已成功操作，不为纠错额外增加预算。
参数错误记录使用既有未派发事实与 Owner 加密详情；只返回固定原因、已声明字段路径和索引，不回显参数值、未知字段名、源码或异常正文。
坏 JSON 不自动修复或执行；SDK 原始 arguments 可用时严格复核，避免把自动补齐的 JSON 当成原始合法参数。原始参数不可用时，只能按已有结构与终止元数据判断，不声称识别所有未标明的上游截断。
工具主动 ToolException 使用 TOOL_EXECUTION_FAILED 完整返回模型，可能已发生副作用，不当作确定未执行。未知执行异常仍终止，并保存实际凭据。
真正的调用身份错误、结果缺失/错配、事件流不完整及运行时异常分别使用具体错误码；运行时异常日志增加固定 error_phase/error_type，前端提供对应中文说明。

2026-09-15 16:09 的原故障仅能确认第二次模型调用返回后发生协议错误（厂商报告输出 36,792 Token），旧日志没有保存足够分支信息，不能据此认定该次一定是坏 JSON 或某个参数字段。本修正针对已复现代码路径，不冒充对历史响应的完整还原。

## 确定性与浏览器验证

在 backend 运行：

```bash
python -m pytest -q tests/test_tool_argument_recovery.py tests/test_agent_decision_budget.py tests/test_agent_loop.py --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
```

全量 **437 passed、3 skipped、18 deselected（290.12 秒）**。之后增加原始 JSON 严格检查与对应宽松解析回归，专项 **47 passed（4.70 秒）**，不与全量相加。
覆盖字段错误/坏 JSON/未知工具修正、混合合法非法调用、直接返回工具纠错、预算不续额、截断/取消、重复身份、主动执行失败、诊断脱敏、真实文件服务与持久化/刷新。

在 frontend 运行：

```bash
npm run test:e2e:commands -- argument-recovery.spec.ts budget-proposals.spec.ts
npm run build
```

浏览器 **5 passed（18.2 秒）**，构建通过。字段/JSON 错误卡明确未执行和具体原因，后续调用真实写入；刷新后诊断保留，Guest 无私有字段详情；重复身份显示中文终止说明且无文件副作用；既有最后决策边界回归通过。已检查参数诊断截图。

## 标准真实 World 验证

```bash
npm run test:e2e:real-world -- argument-recovery-provider.spec.ts
```

本轮使用已授权 Token Rhythm / deepseek-flash，正常 World 包装器；通过浏览器要求真实模型第一次将 max_bytes 传为错误类型，再根据服务端错误修正为整数 64 读取 proof.txt。
结果 **1 passed（47.9 秒）**：一次未执行的 TOOL_ARGUMENT_INVALID、一次成功读取、整轮正常结束，Owner 详情明确 max_bytes 类型错误。3 次模型调用，厂商报告输出合计 376 Token。
测试后独立读取加密详情核对：第二次实际参数 path=proof.txt、max_bytes=64，返回内容与受控文件一致，活跃 generation 为 0。
本例不宣称真实模型自然产生坏 JSON 的概率；坏 JSON、宽松解析、身份丢失和混合调用等边界由确定性分片与真实框架回归覆盖。

World：`data/roleplex-real-world-e2e-20260915163731/default/`。
会话：`真实工具参数纠错验收`。
账号：`realtest20260915163731`；密码：测试指南的固定占位值 `Roleplex-Real-E2E-1`。
报告：`logs/tests/e2e/real/2026-09-15/16-37-31_f1da4282/`。
关闭截图/trace/video，报告仅白名单统计；真实模型正文仅保留在 World 数据库供 Owner 查看。测试服务已清理，未使用官方 Key。
