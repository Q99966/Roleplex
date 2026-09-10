# 会话运行实例与后台 HTTP 服务（W1d）

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner 客户端；内部 Agent 与运行时 |
| 状态 | 原型（W1d 实施中，尚未完整验收） |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-10 |
| 事实来源 | `app/runtime/`、`routers/runtime.py`、`models.py`、运行时迁移与测试 |

## 身份、状态和配额

每个顶层命令/服务有独立 runtime ID 和唯一 `(execution_id,tool_call_id)`，后台服务与 command 共用配额。
World 默认 20、Workspace 默认 5、Conversation 默认 3；正整数上限可调整，存储上限为标准有符号 32 位整数。
预留、启动、运行、停止及未确认回收都占名额；子进程、查询和停止不另计名额。三层同时满足才能预留。
配额变更采用版本比较；降低上限不杀已有进程，尚未启动的请求必须在批准与启动时重查。

状态为 pending/starting/waiting_ready/ready/unhealthy/running/stopping/cleanup_required，以及终态
stopped/exited/failed/rejected/expired/interrupted。ready 只用于已验证 listener 身份及 HTTP 状态的服务。
stop 不是 kill 已发出，只有实际退出和回收验证后才能释放名额；不能按裸 PID 或端口停止。

## Owner API（候选契约，随实现同步）

- `GET /api/runtime/config?scope=world|workspace|conversation&scope_id=<id>`：limit、revision、used；world ID 为 0。
- `PUT /api/runtime/config`：scope、scope_id、limit、expected_revision，workspace 还可修改 services_enabled。
- `GET /api/conversations/{id}/processes`：该会话的登记实例，含安全状态和来源身份，按开始顺序返回；不扫描全机。
- `GET /api/conversations/{id}/processes/{runtime_id}`：Owner 私有详情与冻结启动请求。
- `GET /api/conversations/{id}/processes/{runtime_id}/logs?after=<seq>`：有界 stdout/stderr 块，最多 64 KiB 正文，
  next_seq 与 gap 明确表示环形淘汰；seq 是后端观察顺序，不声称两条管道的真实全局写入顺序。
- `POST /api/conversations/{id}/processes/{runtime_id}/stop`：幂等停止，只针对该登记实例。

有效 Owner Token 和资源归属必须在服务端复核；Guest 403、不存在或跨会话 404，响应 no-store。
原脚本、root、日志仅在这些受保护接口中返回，不进入消息历史/共享 WS、正式日志或前端持久缓存。
`runtime_changed` 只广播 runtime_id、revision、state；Owner 收到通知及重连就绪后重新读取登记。
服务日志在内存环形保留 1 MiB，终态尾部按 World 用途隔离密钥加密保存；7 天或 World 16 MiB 超额时清除旧尾部，
不删除来源/回收审计身份。输出持续排空，绝不逐输出块写数据库。
日志 availability 区分 available、not_recorded、expired、evicted 与 unavailable；到期与预算淘汰不能伪装成空输出。
终态保存及应用启动时执行过期密文清理，读取时也检查期限，不返回过期内容。

## 模型工具与启动移交

拟提供 `workspace_start_service(script,port,health_path="/",lifetime_seconds=7200)`、
`workspace_service_status(runtime_id)`、`workspace_service_logs(runtime_id,after=0)`、`workspace_stop_service(runtime_id)`。
这些工具在 Owner single 和工作区/角色显式启用时使用。start 参数仅表达脚本及受限探针/寿命，不能传 argv、cwd、
环境、审批结果或调用身份。启动批准必须绑定后台服务操作、runtime ID、脚本、目录、解释器、端口、探针和限制；
W1c 普通脚本批准不能消费为服务启动。

后台实例只由独立宿主任务写状态；启动工具等待真实 ready 后移交，随后 generation 可以完成。
移交前取消要收口，移交后停止生成不停止服务。回复、页面、socket 不是服务所有者。
probe 固定 127.0.0.1 的批准端口与相对路径，无 DNS/代理/认证头、不跟重定向、响应体有界且不落日志。
listener 必须属于监管器后代，不能把陌生进程的 200 当成功；监听偏离声明范围时失败并清理。

## 删除、退出与恢复

会话/工作区变更前先持久化回收门槛和冻结清单，逆启动顺序逐项回收。回收条目持久记录来源、选择原因、
实际动作、身份复核、耗时和结果，单项失败继续其他项。重复/重叠操作不得重复实际停止，结束按完整清单对账。
有未确认项时拒绝删除/换绑/切 World。配置降低配额不是回收授权。
应用退出关闭新启动入口，先逐项回收再关闭调度器、DB/日志。包装器不得用固定短超时截断整批回收。
POSIX 包装器通过独占控制管道通知后端退出；包装器被强制终止时管道 EOF 同样触发后端正常关闭。
重复终止信号不得重复打断正在执行的回收；该管道属于内部生命周期控制，不是公开 API。
硬退出由监管器控制通道 EOF 触发子进程收口；重启不重放或盲目接管，PID/出生身份不符不得误杀。
未完成回收清单与旧进程实例关联恢复，不能补造上次已完成终态。

W1d 首先仅开放已验证的 Linux 后台服务运行器；Windows 在原生进程/Job/UTF-8/宿主消失测试通过前不开放该能力。
不接管 Docker 或其他 daemon 资源，不保证阻止恶意脚本提权或杀监管器，仍非 OS 沙箱。

错误与日志事件在对应权威目录登记；本页不把脚本和输出混入诊断字段。
