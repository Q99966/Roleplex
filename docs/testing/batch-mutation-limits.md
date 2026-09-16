# 批量写入/编辑取消固定限制验证

- 日期：2026-09-16。
- 状态：实现与验证完成，用户已确认并授权提交。
- 范围：仅取消批量 write/edit 最多8项、整批JSON 256 KiB及结果元数据预留造成的等价拒绝；读取不变。
- 权威契约：[工作区批量修改](../protocol/public/rest/workspaces.md#e2-多文件写入编辑已人工验收内部工具契约)、[工具详情](../protocol/public/messaging/tool-details.md#e2-批量修改详情已人工验收)。

## 实现与边界

模型接收完整的精简逐项结果，Owner加密详情保留完整节点；模型结果不包含源码或diff，没有新增结果查询工具或数据库表。
差异展示预算与提交事实分离；大结果不会因旧64 KiB总返回量检查整体失效。完整结果大小随项数和路径增长，不承诺任意规模恒定内存/响应大小。
前端先渲染50项，点击“显示后续文件”继续展开；接口数据与已提交文件并未截断。修改工具策略版本更新，防止沿用旧maxItems/说明缓存。
路径祖先冲突检查由全批两两比较改为祖先集合查询；权限、hash、父目录、失败即停、取消及原写入等待语义保持。
单文件1 MiB、单文件编辑片段64 KiB/32处、写队列负载规则与服务保护未调整。现有加密详情七天保留和Owner隔离保持。

## 确定性回归

在 backend 执行：

```bash
python -m pytest tests/test_workspace_batch_mutation.py tests/test_workspace_tool_guidance.py tests/test_workspace_replacements.py -q --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
```

最终专项：43 passed（15.26 秒）。覆盖：
- 12项批量写入与编辑，两种参数均超过256 KiB，含中文UTF-8与JSON转义。
- 一个300000字节的合法文件放入批次成功。
- 300项、整体超过64 KiB的加密详情完整可读；超额diff降级，提交状态与hash不丢失。
- 第10项提交后取消/后续撤权，完整保留成功项、失败项和未执行项。
- UTF-8无效参数仍在执行前拒绝，schema/工具说明、同批祖先/硬链接冲突、版本检查、原取消和编辑行为不回退。

全量回归：463 passed、3 skipped、18 deselected（366.90 秒）。后续补充的大批次取消/撤权与UTF-8边界已由上述43项专项覆盖，不将两轮数量相加。

没有表结构变化，无需新增迁移；本轮没有运行真实PostgreSQL重放。

## 浏览器

在 frontend 执行：

```bash
npm run test:e2e:commands -- batch-mutation.spec.ts batch-unlimited.spec.ts
npm run build
```

结果：2 passed（16.7 秒），构建通过。真实前后端、fake Provider执行60项创建及编辑，包含一个超过旧批次JSON上限的文件；全部文件与详情逐项核对，50项初始渲染、后续展开及刷新通过。
原批次预检失败、窄屏和Guest隔离场景继续通过。首轮曾遇两个测试使用相同角色名而准备失败，已独立命名后复验，不通过放宽断言处理。
截图：logs/tests/e2e/fake/2026-09-16/15-22-05_2fb73785/artifacts/screenshots/60__b248591a_0_0.png。

## 标准真实 World

```bash
npm run test:e2e:real-world -- batch-unlimited-provider.spec.ts
```

使用用户授权中转站与deepseek-flash，1 passed（21.5 秒）。模型一次workspace_write创建12项，再一次workspace_edit修改12项；全部文件为预期内容、两个工具各12项applied=true，无拆批或工具失败后重发。
实际3次Provider调用，厂商报告输出1388 Token；不估算费用，不把确定性大参数测试说成真实模型输出过256 KiB。

- World：data/roleplex-real-world-e2e-20260916152233/default。
- 会话：真实批次取消上限验收。
- 账号：realtest20260916152233；密码沿用测试指南的固定占位值。
- 报告：logs/tests/e2e/real/2026-09-16/15-22-33_db3f8d5b/artifacts/diagnostics/_World_12__71485893_0_0.json，passed与cleanup_passed均为true。

凭据仅在进程环境进入后端加密播种；浏览器截图、trace和video关闭，报告只有白名单状态与计数。测试服务已清理，World按统一保留规则供人工登录核对。
