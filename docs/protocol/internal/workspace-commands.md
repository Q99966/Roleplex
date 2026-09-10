# 工作区结构化命令

| 元数据 | 值 |
|---|---|
| 受众 | 内部 Agent、后端与测试维护者 |
| 状态 | 已实现（W1b 已人工验收；Windows 实机验证未覆盖） |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/workspaces/commands.py`、`command_worker.py`、`tools.py` |
| 关联测试 | `backend/tests/test_workspace_commands.py`、`frontend/tests/world-managed/world-switching.spec.ts`、`frontend/tests/commands/commands.spec.ts`、`frontend/tests/real-world/commands-provider.spec.ts` |
| 复核日期 | 2026-09-09 |

## 输入、归属与权限

`workspace_run_command(command, args={})` 只接受下表，所有额外字段拒绝：

| command | args |
|---|---|
| `pwd` | 空对象 |
| `list` | 可选 `path`，默认 `.` |
| `read` / `count` | 必填相对文件 `path` |

命令 ID 不能是可执行路径；参数禁止 Shell 元字符、换行、环境赋值、任意 argv、cwd 或环境覆盖。
路径复用 [W1a 路径规则](../public/rest/workspaces.md)，父进程与受控 worker 都校验。
W1b 额外拒绝冒号（Windows ADS）及组件末尾点/空格，避免 Windows 路径别名绕过敏感名称检查。read/count
只处理最大 1 MiB 的普通 UTF-8 文件，list 最多返回 200 个非敏感条目。
count 返回 UTF-8 字节数和逻辑行数（非空且无末尾换行的最后一行也计入）。

仅 Owner 触发、single、角色显式开启该工具、绑定 active/available 且 basic_commands_enabled、
execution/generation running 且未请求停止、lease ready 时暴露并执行。每次调用重新鉴权；文件与命令
开关独立，同一 lease 内命令串行。工作区不可用时隐藏工具或返回 WORKSPACE_TOOL_NOT_AVAILABLE，不回退 cwd。
无新增表、无幂等重放；命令只读，失败可由模型重新请求，重启不恢复调用。

## 子进程与结果

服务端使用已解析的 Python 可执行路径、隔离模式和固定 worker 文件，通过 exec 启动；不执行工作区代码、
不加载工作区模块或用户 site 包。输入经 stdin 传递，cwd 固定为 lease 根；环境只含所需系统路径与 UTF-8 设置。
不继承 Provider Key、JWT、后端环境或用户 Python 配置。不支持的平台返回 COMMAND_NOT_SUPPORTED。

部署配置 `WORKSPACE_COMMAND_TIMEOUT_SECONDS` 默认 30 秒、硬上限 300 秒；
`WORKSPACE_COMMAND_OUTPUT_BYTES` 默认 65536、硬上限 1048576。两者仅由主机配置设置，模型不得覆盖。
stdout/stderr 并发排空，合计有界保留，各流独立 seq/bytes，截断后仍排空以避免死锁。
取消、超时和正常父进程退出均回收该次子进程树；任务取消必须等待清理后再传播。

结果为 JSON 文本：command、status（exited/timed_out/cancelled）、exit_code（可空）、stdout、stderr、
stdout_seq/stderr_seq（读取块数）、stdout_bytes/stderr_bytes（收到的字节总数）、truncated、duration_ms；
失败附稳定 error_code。超时为 COMMAND_TIMEOUT，非零退出为 COMMAND_FAILED；参数/平台错误发生在启动前。
权限/参数/路径政策拒绝在工具领域状态中为 rejected；进程非零退出为 error，超时的日志状态为 timeout。
generation 取消继续使用 stopped 终态；取消时模型不再接收结果，过程卡和审计必须收口。
进程重启后的遗留命令卡显示 failed + EXECUTION_INTERRUPTED，exit_code 为空且不填 command_status；
这只表示执行被中断，不推测命令的退出结果，不补造工具完成审计或重放子进程。

## 领域事件、公开摘要与日志

复用 ToolCallStarted/ToolCallFinished，不向业务层暴露框架或操作系统事件。
开始摘要只含允许的 command ID；结束新增可选 command_summary，字段为 command、command_status、
exit_code、truncated、error_code。工具过程卡使用相同允许字段；duration_ms 沿用既有字段。
未知字段可忽略，旧客户端继续展示既有工具卡。原始 cwd、路径、stdout/stderr 不进入共享卡片、正式日志、
审计参数摘要、测试报告；本轮模型可见，Owner 可通过独立受保护的[执行详情接口](../public/messaging/tool-details.md)
查看有界内容。工具日志复用已有开始/完成事实，不逐输出块写日志。

错误语义以 [错误码注册表](../error-codes.md) 为权威；权限与绑定接口以
[当前 World 工作区](../public/rest/workspaces.md) 为权威。安全检查不能依赖模型或命令名称自述。
