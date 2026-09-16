# 中断执行事实交接验收

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 日期：2026-09-16。
- 状态：实现与验证完成，用户已确认并授权提交。
- 权威契约：[中断上下文](../protocol/internal/interruption-context.md)、[数据模型](../protocol/internal/data-model.md)。

## 行为与范围

用户仍直接发送普通消息；没有继续按钮、关键词门槛或每轮可见摘要。当前角色最近一次回复中断时，ContextBuilder按身份、工作区和权限核对最小事实，放在动态历史尾部；当前用户新要求不改写。新消息继续使用原有新执行链语义，不恢复旧图或后台自动重放。

原生write/edit新增写前prepared和提交后confirmed加密证据，在diff和整批返回之前保存。记录不包含源码。写前记录等待后重新鉴权，避免在新异步边界绕过撤权；取消等待已经开始的记录收口。采集故障不将已提交文件改判失败。
历史confirmed不代表当前未变化；prepared且当前hash符合预期，仅确认当前内容，不补造历史成功。较新的未确认操作不能被较早成功遮盖。详情过期、缺失与不完整交接保持未知。
群聊按角色隔离来源，不扩大原生文件工具的单聊权限；Shell/MCP仅交接公开调用状态与未知副作用提示，不移用脚本/批准或自动重放。

## 后端与迁移

在 backend 执行：

```bash
python scripts/check_migrations.py
python -m pytest tests/test_chat_flow.py tests/test_interruption_context.py tests/test_workspace_batch_mutation.py tests/test_execution_usage.py -q --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
```

迁移0018通过SQLite升级/回退、metadata、外键检查；PostgreSQL离线SQL编译660行。未运行真实PostgreSQL完整重放。
专项62 passed（62.55秒）；最终中断专项17 passed（27.07秒），覆盖后续补充的外部工具提示与交接大小边界，不与全量相加。
全量首次发现旧测试固定断言context schema=4；本次协议升级为5，已同步断言及文档。最终全量481 passed、3 skipped、18 deselected（386.76秒）。

覆盖非关键词新要求、文件版本变化、确认/未确认、过期、撤权、换绑、正常完成不注入、Guest隔离与群聊角色边界、上下文不足降级、采集失败、写前等待后撤权。
进程级测试启动真实子进程，在文件已提交、confirmed尚未保存时使用受控os._exit退出：文件真实落盘，数据库保留prepared；新上下文只报告matches_expected_content_only。子进程被等待回收，不在主测试进程模拟一个并未发生的退出。

## 浏览器

在 frontend 执行：

```bash
npm run test:e2e:commands -- interruption-context.spec.ts
npm run build
```

普通浏览器使用真实前后端与fake Provider，最终复验2 passed（9.3秒），构建通过：
- 写入→点击停止→刷新→提出新的修改要求；fake只从实际交接获取hash，一次edit完成，没有再次write。
- 正常完成后换新问题，文件不被重复修改，界面没有交接摘要正文。
- 停止后撤销文件权限，旧私有文件证据不进入模型，文件保持原样。

截图：logs/tests/e2e/fake/2026-09-16/15-53-32_28e14903/artifacts/screenshots/_false_5aa87e94_0_0.png。
新增提交证据接入后的60项批量创建/编辑浏览器兼容复验：1 passed（13.7秒），完整文件结果与分段展示保持。

## 标准真实 World

```bash
npm run test:e2e:real-world -- interruption-context-provider.spec.ts
```

使用用户授权的中转站deepseek-flash，1 passed（19.0秒）。创建interruption.txt后由浏览器主动停止；下一条消息直接要求把alpha=1改为alpha=2，不含“继续”关键词。
两次角色回复分别stopped/done；新请求只有一次workspace_edit，文件内容验证正确；两条消息仍是不同chain，没有暗中重启旧任务或清零旧计数。

- World：data/roleplex-real-world-e2e-20260916155552/default。
- 会话：真实中断事实交接验收。
- 账号：realtest20260916155552；密码沿用测试指南的固定占位值。
- 报告：logs/tests/e2e/real/2026-09-16/15-55-52_2bfce96d/artifacts/diagnostics/_World__890a8394_0_0.json。

观察到4次Provider调用开始、3次完整结束；完整返回的厂商输出用量合计595 Token。被取消请求未提供完整usage，不记作零，也不把595称为全部实际消耗。
真实测试关闭截图/trace/video；凭据只从进程环境进入后端加密播种，报告仅保存状态和白名单数值；测试服务已清理，World按统一规则保留。

## 实际边界

只交接最近本角色的中断来源，不声称恢复全部历史或模型内部思考。交接有大小与扫描预算，遗漏明确标不完整；这不是文件执行数量限制。没有跨文件事务、断电持久性或任意崩溃恰好一次保证；未知外部副作用仍需核对。
