# 小阶段 5：预算停止与执行事实验证

| 元数据 | 值 |
|---|---|
| 状态 | 已实现，已通过人工验收 |
| 复核日期 | 2026-09-15 |
| 维护者 | Roleplex |
| 事实来源 | Agent 防腐层、消息所有者、调用级凭据及以下测试 |

## 本次补齐

原 T1 已保留真实工具提交凭据与停止原因，但被图预算阻止派发的提议只有一个未消费的数量字段。
现在同响应提议通过独立领域事件整体交接，工具卡明确 not_executed，不伪造开始事件、执行耗时、审批或副作用。
记录时间与执行时间分开；Owner 可按原加密详情规则查看白名单参数，未知工具名降级，不扩展 Shell/MCP 原始参数采集。
已提交、后项已知失败、结果未知和后续未派发分别保留；记录组在取消竞争中先完成收口，不重放操作。
已发现的协议配对异常不会被随后 GraphRecursionError 掩盖。

契约只以[执行事实](../protocol/internal/execution-facts.md#小阶段-5图预算阻止派发的提议)和[工具详情](../protocol/public/messaging/tool-details.md#图预算下未派发的调用)为准。
状态使用现有 JSON 与 String 列，无新增表、字段或迁移。

## 确定性回归

backend 下执行：

```bash
python -m pytest -q --tb=short --show-capture=no
python -m pytest tests/test_execution_summary.py -k cancel_during_undispatched -q --tb=short --show-capture=no
```

最终全量 **379 passed、3 skipped、14 deselected（253.96 秒）**。
全量后扩展实际停止接口路径，与重复取消所有者路径专项 **2 passed**；不将该专项与全量简单相加为互不重叠用例数。
覆盖低预算同响应多提议、批量前项提交/后项确定失败或未知、历史刷新、取消期间整组持久化、无额外模型请求与协议异常优先级。

中间遇到两个测试问题：独立数据库测试重载模块造成新旧 dataclass 类型身份不一致，已统一从当前防腐层收集；
并发测试进程清理同一旧测试目录导致 FileNotFoundError，现只接受已被清理的不存在情况，其他 IO 错误仍抛出，最终全量串行完成。
停止接口专项按实际 HTTP 202 契约断言，并在 finally 释放测试屏障，避免断言失败导致测试清理等待。

## 浏览器

```bash
npm run build
npm run test:e2e:commands -- budget-proposals.spec.ts execution-summary.spec.ts exploration.spec.ts
```

构建通过，真实前后端/fake Provider 浏览器 **3 passed（23.0 秒）**。
默认 15 图步数下，实际完成七个小文件写入，最后同响应两个写入提议未派发；独立文件检查确认只有七个文件。
工具卡与刷新状态准确，无“开始/结束/耗时”伪记录，无系统总结；日志七个实际开始、两个未派发事件、八个模型请求。
既有断线、Guest、探索归组与失败展示回归通过；未派发项不计入探索中的实际读取/搜索次数。
报告 `logs/tests/e2e/fake/2026-09-15/11-38-18_dc010a34/`，已查看未派发卡片截图；测试端口已清理。

## 独立真实 Provider

运行前已说明联网计费，backend 下显式执行：

```bash
python -m pytest tests/contract/test_tool_reliability_real.py -m contract -k real_budget_records -q -s --tb=short --show-capture=no
```

**1 passed、1 skipped、4 deselected（2.72 秒）**；未配置 Anthropic 跳过。
配置模型 deepseek-v4-flash，单次请求指定 max_tokens=256；真实模型提出工具调用，受控图预算 2 阻止派发。
观察到一次 Provider 请求、输出 115 Token、工具未执行、未派发领域记录与 graph_budget 终态；无额外收尾调用。
真实用例只证明模型/防腐层边界，产品鉴权、持久化、并发和浏览器由其他层覆盖，不冒充真实模型全链路浏览器验收。

## 保留的边界

未提高执行上限，未增加每轮总结或模型历史注入，未自动恢复、重放或改变文件大小限制。
仍可能先花费一次模型请求才得知它提出工具而预算不足；不能预先猜测模型会给最终回答还是工具调用。
进程崩溃窗口、未收到完整提议的中断仍不能补造证据；已知未派发记录也不代表任务验收通过。


## 真实 API 完整对照与人工验收

2026-09-15 按用户要求执行完整独立契约命令：

```bash
python -m pytest tests/contract/test_tool_reliability_real.py -m contract -q -s --tb=short --show-capture=no
```

结果 **3 passed、3 skipped（8.24 秒）**；未配置 Anthropic 的三项跳过。模型为 deepseek-v4-flash。

| 场景 | 独立核对结果 | 模型调用 | 输出 Token |
|---|---|---:|---:|
| 正常预算 15 | 文件已提交且内容匹配，读取核验成功，模型确认与回执一致 | 3 | 256 |
| 写后触顶 3 | 文件已提交且内容匹配，后续核验未执行，graph_budget，未追加收尾调用 | 2 | 92 |
| 派发前触顶 2 | 工具未执行，产生未派发领域记录和 graph_budget 终态 | 1 | 84 |

本轮合计 6 次模型调用，厂商报告输出 432 Token。正常/写后触顶的脱敏报告位于
`/home/chen/workspace/testworkspace/roleplex-command/test-20260915132957/`，原始响应与凭据未持久化到报告。
真实测试范围仍为模型与执行层契约，UI、持久化与权限由上述确定性分层测试覆盖。

2026-09-15 用户确认当前版本并授权提交，小阶段 5 人工验收通过。
