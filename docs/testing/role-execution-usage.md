# 右侧角色执行用量验证

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 状态：实现与验证完成，待人工验收；复核日期：2026-09-15。
- 本轮只增加用量观测，新增时间、Token、费用限制暂缓；现有共享决策配置不变。
- 权威契约：[角色执行用量](../protocol/public/rest/execution-usage.md)、[数据模型](../protocol/internal/data-model.md)。

## 展示与数据来源

进入右侧“会话成员”，展开对应角色“执行用量”，可切换“最近一次”与“本会话累计”。
显示缓存未命中输入 Token、输出 Token、缓存命中 Token、输入缓存命中率（派生口径见权威契约），已记录模型调用数、整轮耗时、模型调用耗时合计、状态与停止原因；调用数为框架调用，不是逐次 HTTP 请求。
角色与会话范围独立，Owner 且为会话成员才能查询；折叠时不请求，运行中按约两秒刷新，不随每个 Token 刷请求。
业务记录按 execution_id/call_index 唯一关联，重复完成不重复累计。旧执行未采集、旧回复没有 execution、调用记录或字段缺失都不能冒充零用量；必要时显示“未知”和已记录部分。
页面不读取日志回填，不把缓存再次加进输入，不提供价格/费用硬封顶；fake Provider 不提供伪造 Token。最新查询只读取所需元数据，不读取历史正文。

## 数据库与回归

迁移 0015 增加观测表与执行标记，0016 支持旧回复关联核对索引；SQLite 升级/回退、metadata 一致性、外键检查以及 PostgreSQL 离线 SQL 编译（617 行）通过。未运行真实 PostgreSQL 服务上的完整重放。

在 backend 运行：

```bash
python scripts/check_migrations.py
python -m pytest -q tests/test_execution_usage.py tests/test_workspace_edit.py tests/test_execution_summary.py --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
```

首次全量发现文件已提交但取消打断开始记录落库时，详情缺少 write 凭据；未放松测试。现在在正文执行前保留安全元数据、凭据确认前只导出快照、显式关闭事件流并等待工具收口，取消时可补齐未交付的实际提交。
同步屏障覆盖工具开始落库暂停、模型用量交接暂停两种已提交场景，专项 45 passed（40.83 秒）。这是同进程交接修复，不是跨进程恰好一次保证。
修复后全量 **450 passed、3 skipped、18 deselected（309.78 秒）**。后续只读查询精简与旧无 execution 回复核对，由用量专项补验：10 passed（14.99 秒）；不把专项与全量相加。
其余覆盖 Owner/Guest/归属、重复完成、部分字段、fake 用量过滤、采集失败不影响任务、取消后已知用量保留和重启后未知状态。
算术测试数字为直接服务层数据夹具，不由 fake Provider 产生，也不作为真实消耗报告。

## 浏览器

```bash
# frontend
npm run test:e2e:commands -- role-usage.spec.ts argument-recovery.spec.ts
npm run test:e2e -- conversation-details.spec.ts
npm run build
```

用量与参数恢复 **4 passed（15.9 秒）**；详情转盘/键盘/窄屏 **1 passed（10.5 秒）**；构建通过。
检查最近与累计切换、刷新保留、未知态、Guest 不显示且 API 拒绝、原转盘交互和草稿保持。已查看角色用量截图。补充旧无 execution 回复处理后，用量浏览器专项再次通过（1 passed，9.0 秒）。
浏览器报告：`logs/tests/e2e/fake/2026-09-15/18-47-04_ddc838ff/`。

## 标准真实 World

专用命令会联网并消耗额度，执行前已说明：

```bash
npm run test:e2e:real-world -- role-usage-provider.spec.ts
```

Token Rhythm/deepseek-flash，正常 World 包装器；两个真实角色各回复两轮。API 累计值逐字段与厂商完成事件对照，右栏检查最近一次/累计及刷新后的输出数值；不通过 fake 数字冒充真实验证。
结果 **1 passed（16.9 秒）**，共四次模型请求：

| 角色 | 调用数 | 输入 Token | 输出 Token |
|---|---|---|---|
| 真实用量甲 | 2 | 356 | 132 |
| 真实用量乙 | 2 | 371 | 232 |

合计输出 364 Token，未使用官方 Key。缓存字段同样按已报告值/未知对照，不推算未提供值。
World：`data/roleplex-real-world-e2e-20260915185344/default/`；会话：`真实角色用量验收`。
账号：`realtest20260915185344`；密码为固定测试占位值 `Roleplex-Real-E2E-1`。
报告：`logs/tests/e2e/real/2026-09-15/18-53-44_a547f692/`。关闭截图/trace/video，报告只保存白名单数值，测试服务已清理。

缓存展示调整补验：用量专项 14 passed（20.29 秒），浏览器专项 1 passed（8.6 秒），前端构建通过。新增调用配对、输入加权、缺字段、零分母与无效命中量回归；本次算术与展示调整未额外调用真实 Provider。
