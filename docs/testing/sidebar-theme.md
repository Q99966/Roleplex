# 侧栏与清爽主题验证

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

状态：已实现，已通过人工验收。复核日期：2026-09-15。
设计与边界见[侧栏与主题](../design/frontend-navigation-theme.md)。本次不改变后端 API、模型参数或工具权限。

## 执行结果

frontend 下运行：

```bash
npm run build
npm run test:e2e -- sidebar-tabs.spec.ts role-output-settings.spec.ts m1-workspace.spec.ts m2-chat.spec.ts recycle-and-tombstone.spec.ts
npm run test:e2e:commands -- search-read.spec.ts batch-mutation.spec.ts
```

最终构建通过。真实前后端、独立测试库、fake Provider：第一组 **16 passed（26.6 秒）**，工具组 **2 passed（15.9 秒）**。
新增侧栏测试验证双标签切换、角色名称/标签/描述搜索、无结果提示、各自搜索词与滚动位置保留、右侧会话/草稿不变、角色编辑入口、键盘切换与窄屏收起/展开。
角色设置验证输出参数实际保存、其他参数保留、工具类别折叠与全选/半选；既有认证、停止生成、权限、回收站与墓碑回归通过。
旧用例同步改为从角色标签进入角色列表，并精确匹配停止按钮，避免与会话标题中的相同文字混淆。

最终界面报告：`logs/tests/e2e/fake/2026-09-15/09-42-37_578506b9/`。
工具组报告：`logs/tests/e2e/fake/2026-09-15/09-40-54_0ef4ac16/`。
已检查浅蓝会话/薄荷角色、角色弹窗以及 390 宽度截图；主操作使用深蓝底白字，辅助文字加深，错误/警示/成功继续保留语义颜色。
本次不宣称完成全站 WCAG 审计。最终构建同时覆盖首页渐变文字与浅色主题样式。

测试服务端口已清理。未进行付费 Provider 调用，界面状态和保存走真实 API，Agent 场景使用固定 fake。

用户随后要求恢复代码 diff 的原深色展示；该区域独立于浅色工作台。write-diff.spec.ts 校验实际新增/删除背景和代码文字颜色，详见该用例最新报告。

Diff 深色恢复验证：构建通过，浏览器 1 passed（7.4 秒），校验深绿新增、深红删除与浅色文字的实际 CSS；报告 `logs/tests/e2e/fake/2026-09-15/09-48-49_6382785a`。
