# Shell 逐次审批

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner 客户端；内部工具执行维护者 |
| 状态 | 已实现，已人工验收（W1c；不开放群聊或 Guest；Windows 实机未覆盖） |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 事实来源 | `workspaces/approvals.py`、`workspaces/shell.py`、`routers/approvals.py`、`components/ShellApprovals.tsx` |
| 关联测试 | `tests/test_shell_approvals.py`、Shell fake/real managed-world E2E |
| 复核日期 | 2026-09-10 |

## 工具与执行边界

`workspace_run_shell(script)` 仅接受非空 UTF-8 脚本，最多 65536 字节，禁止 NUL 和额外参数。
调用身份由 Agent 防腐层绑定，不接受模型传入 cwd、环境、工具调用 ID、审批 ID 或 shell 可执行文件。
仅 Owner 触发的 single、active role 显式启用工具、active workspace.shell_enabled、running execution/generation
且未停止、ready lease 及规范根匹配时可用；Shell 永远 dangerous，safe override 不适用。

部署 `WORKSPACE_SHELL_KIND=auto|bash|powershell`，auto 在 POSIX 选 Bash、Windows 选 PowerShell。
无可执行文件时不开放能力。Bash 使用 `--noprofile --norc -s`，PowerShell 使用 `-NoProfile -NonInteractive -Command -`；
脚本经 stdin 传入，不进入 argv、日志、工具摘要或共享消息 part。复用命令运行器的最小环境、输出预算、超时和进程树清理。
Shell 不是 OS 沙箱：批准脚本可以访问宿主绝对路径和网络。UI 必须明确提示，不能宣称仅能访问工作区。
Linux 使用独立的 subreaper 监管进程收养并清理脱离进程组的后代；停止时先通知监管进程收口，再有界强制回收。
该设置只作用于本次监管进程，不修改后端全局收养策略。Windows 继续在交付脚本前分配 Job Object。
其他 POSIX 平台暂不开放 Shell，不能以普通进程组代替上述后代清理承诺。
监管器处理正常的进程生命周期，不提供抵抗恶意脚本杀死监管器、提权或内核不可中断 I/O 的 OS 安全隔离承诺。

## REST 与权限

所有接口需有效 Bearer Token、当前 World Owner 及目标会话成员身份；Guest 返回 403 OWNER_REQUIRED，
非成员/已删除会话 404 CONVERSATION_NOT_FOUND，审批不属于该会话时 404 SHELL_APPROVAL_NOT_FOUND。
所有响应 `Cache-Control: no-store`，脚本不进入浏览器持久化或共享历史缓存。

- `GET /api/conversations/{id}/tool-approvals`：仅返回未过期 pending 列表。
  每项含 id、execution_id、tool_call_id、tool_name、workspace_binding_id、workspace_name、world_name、root_path、
  script、shell_kind、timeout_seconds、output_bytes、request_digest、status、requested_at、expires_at。
- `POST /api/conversations/{id}/tool-approvals/{approval_id}/decision`：
  `{decision:"approve"|"reject",request_digest:"<64 位摘要>"}`。返回 id/status；不能编辑脚本或执行参数。
  摘要不匹配 409 SHELL_APPROVAL_MISMATCH；批准前复核 execution/权限/根和配置，失效返回 409 WORKSPACE_TOOL_NOT_AVAILABLE。
  同一请求重复决定返回已存在终态，不重复唤醒、执行或广播。

## 状态、一致性与恢复

审批记录只有 pending/approved/rejected/expired。pending 持续最多 5 分钟；批准以数据库条件更新
`status=pending AND expires_at>now` 为准，与拒绝/到期竞争只允许一个决定。
密文绑定 execution、workspace、调用 ID、工具名、规范根、解析后的 shell、限制和脚本，解密后复核摘要和归属。
只允许创建该记录的宿主工具任务消费一次批准，不根据已存在 approved 行重新启动执行。
停止/取消关闭 pending；进程启动时将遗留 pending 标记 expired，已 approved 的调用也不重放。
审批通过不代表命令成功，真实退出/超时/取消仍使用工具终态；拒绝/过期向模型返回结构化政策拒绝，不启动进程。

共享领域事件 `approval_changed` 的 payload 只含 approval_id、status；沿用会话 event_seq 和 execution 的链路，
不携带脚本、根、摘要或凭据。Owner 收到通知、首次进入或重连就绪后从专用列表恢复；不按本地计时器猜测批准。
工具输入详情不重复保存脚本；Owner 可在审批界面和受保护的工具详情接口中关联读取既有审批密文。
Shell 输出在调用结束时有界加密保存，供 Owner 按需展开，公开卡仍仅使用安全退出/截断摘要。
详情权限、七天展示期限、输出限额及旧调用降级以[工具执行详情](tool-details.md)为准；不读取机器日志或重新执行补录。

## 审计与降级

`tool.approval_requested` 和 `tool.approval_resolved` 记录审批 ID、digest 及既有 execution/chain/tool_call 关联，
不包含原始脚本/输出/环境/宿主路径。解密、摘要绑定或参数异常统一稳定错误码，不把异常原文回传或记入日志。
旧客户端忽略未知 approval_changed；无法审批的调用最终到期，不自动执行。

PowerShell 的 stdin/NoProfile/NonInteractive 参数依据 [Microsoft 参数文档](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)；
该参数约定与 Linux 上的模拟断言不代替原生 Windows 的编码、Job Object 和取消验证。
