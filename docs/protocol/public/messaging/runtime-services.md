# 会话运行实例与后台 HTTP 服务（W1d）

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner 客户端；内部 Agent 与运行时 |
| 状态 | W1d 与 S1 只读服务发现已人工验收 |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-13 |
| 事实来源 | `app/runtime/`、`routers/runtime.py`、`models.py`、运行时迁移与测试 |

## 身份、状态和配额

G 授权补齐（已人工验收）：服务与 Shell 继续接受实际脚本并分别逐次审批，不新增固定启动入口白名单。
申请、审批及实际脚本交付前复核 Owner/角色会话成员关系、执行身份、绑定与配置；宿主已创建但未交付脚本时
撤销能力仍须阻止执行，并走正常回收。资源/审批复核复用工作区授权，ready 后使用登记服务身份管理，
不要求来源 generation 持续 running。现有请求、状态和错误码不变；执行授权失败沿用 WORKSPACE_TOOL_NOT_AVAILABLE。
Shell 与服务开关独立，审批不可跨工具/调用移用。UI 明示脚本可能读写文件、访问网络，未采集文件差异不代表没有修改；
服务启动失败或回收成功也不代表文件/其他副作用已经回滚。复核不是对任意外部文件变化的原子隔离。

每个顶层命令/服务有独立 runtime ID 和唯一 `(execution_id,tool_call_id)`，后台服务与 command 共用配额。
World 默认 20、Workspace 默认 5、Conversation 默认 3；正整数上限可调整，存储上限为标准有符号 32 位整数。
预留、启动、运行、停止及未确认回收都占名额；子进程、查询和停止不另计名额。三层同时满足才能预留。
配额变更采用版本比较；降低上限不杀已有进程，尚未启动的请求必须在批准与启动时重查。
停用配置的版本在冻结回收清单的同一事务中再次比较；过期请求不能先停止服务再报告版本冲突。

状态为 pending/starting/waiting_ready/ready/unhealthy/running/stopping/cleanup_required，以及终态
stopped/exited/failed/rejected/expired/interrupted。ready 只用于已验证 listener 身份及 HTTP 状态的服务。
stop 不是 kill 已发出，只有实际退出和回收验证后才能释放名额；不能按裸 PID 或端口停止。

## Owner API

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
实例创建/状态更新与对应 runtime_changed 在同一数据库事务提交；事件写入失败必须回滚状态，提交后才广播。
网络广播中断不撤销已提交事实，重连按 event_seq 回放或由快照后的登记读取恢复。
服务日志在内存环形保留 1 MiB，终态尾部按 World 用途隔离密钥加密保存；7 天或 World 16 MiB 超额时清除旧尾部，
不删除来源/回收审计身份。输出持续排空，绝不逐输出块写数据库。
日志 availability 区分 available、not_recorded、expired、evicted 与 unavailable；到期与预算淘汰不能伪装成空输出。
终态保存及应用启动时执行过期密文清理，读取时也检查期限，不返回过期内容。

## 模型工具与启动移交

提供 `workspace_start_service(script,port,health_path="/",lifetime_seconds=7200)`、
`workspace_service_status(runtime_id)`、`workspace_service_logs(runtime_id,after=0)`、`workspace_stop_service(runtime_id)`。
启动、日志、停止在 Owner single 和工作区/角色显式启用时使用；状态查询的 S1 扩展见下文。start 参数仅表达脚本及受限探针/寿命，不能传 argv、cwd、
环境、审批结果或调用身份。启动批准必须绑定后台服务操作、runtime ID、脚本、目录、解释器、端口、探针和限制；
W1c 普通脚本批准不能消费为服务启动。
同工作区运行服务时，Owner 仍可申请 workspace_run_shell 的逐次审批；审批视图提示共享文件和服务影响，Shell 继续占配额。
原生 workspace_write 的服务占用限制暂保留，但不构成对 Shell 或服务脚本写文件的隔离；清理门槛及未确认回收仍阻止 Shell。

后台实例只由独立宿主任务写状态；启动工具等待真实 ready 后移交，随后 generation 可以完成。
移交前取消要收口，移交后停止生成不停止服务。回复、页面、socket 不是服务所有者。
probe 固定 127.0.0.1 的批准端口与相对路径，无 DNS/代理/认证头、不跟重定向、响应体有界且不落日志。
服务寿命以单调时钟执行，UTC 到期时间仅用于展示；墙钟回退不能延长已批准寿命，到期进入 expired。
listener 必须属于监管器后代，不能把陌生进程的 200 当成功；监听偏离声明范围时失败并清理。

## S1 Agent 服务发现（已人工验收）

workspace_service_status 的旧 runtime_id 查询保持 runtime_id/state/port/health_code 四字段响应，包含本会话已登记终态，
原可查询的 command 状态亦保留兼容。不传 runtime_id 则列出当前会话 kind=service 且属于既有 ACTIVE 集合的实例；
包含 pending/starting/waiting_ready/ready/unhealthy/running/stopping/cleanup_required，不混称全部就绪。
列表不含终态或一次性命令，不扫描 OS、不读取脚本/日志或当前目录。不存在或不可见 ID 统一 RUNTIME_NOT_FOUND。

列表参数 cursor（可空）、limit 默认 50，严格整数 1..100；runtime_id 必须 32 位小写十六进制，显式 null/空 ID 拒绝，
且不能与任意 cursor/limit 键混传（包括 null/默认值）。未知字段、无效形态沿用 RUNTIME_ARGUMENT_INVALID。
返回 items/has_more/next_cursor；每项仅 runtime_id/state/port/health_code/workspace_binding_id，指向本会话登记的原工作区引用。
不返回 can_stop；查询结果不授予停止权限。按 sequence、id 降序 seek 分页，最多读取 limit+1 个必要字段投影，
不加载密文日志列。每次紧凑 UTF-8 JSON 最大 64 KiB；异常数据导致超预算时明确查询失败，不截断 JSON 或返回假空列表。
无服务才返回 items=[]、has_more=false、next_cursor=null。

不透明游标包含格式版本、当前 stream epoch、Owner、会话和末项 sequence/id；它不是授权凭据，作用域/形态错误
为 RUNTIME_QUERY_CURSOR_INVALID。重启/切 World 后 epoch 不同为 RUNTIME_QUERY_CURSOR_EXPIRED，重新从首页查询。
列表随真实状态变化，不是快照；续页期间退出的实例可以消失，新登记可在重新查询首页时出现，不提供跨页固定总量承诺。
数据库/内部查询故障为 RUNTIME_QUERY_FAILED，正常拒绝与实际失败分别使用既有工具拒绝/失败前缀。

状态工具创建和每次调用均要求有效 Owner single execution/generation、未取消、活跃且显式启用 status 的角色，
以及 Owner/角色双方当前会话成员关系；查询目标按 owner_id、conversation_ref_id 及原 conversation_id 复核。
查询不依赖当前 Workspace Binding、文件 lease、启动开关、服务配额、Shell 平台可用性或 RuntimeGate/清理操作门槛；
这些条件不应阻止有权限的当前 execution 查看既有登记。它也不释放或绕过任何控制/写入门槛。
会话/角色已删除、权限/成员已撤销、execution 已结束或取消仍拒绝 WORKSPACE_TOOL_NOT_AVAILABLE；
应用退出、数据库关闭后不承诺继续查询。历史工作区与当前绑定不同不影响本会话状态查询，也不因此开放其文件访问。
只读状态不创建租用、运行实例、审批或回收记录。启动、日志、停止权限与 write/edit 停服规则不变；
S2 原生修改拒绝诊断见[工作区协议](../rest/workspaces.md)，不会赋予跨会话停止或绕过回收门槛的能力。
模型结果及 Owner 私有详情可以含本会话列表；共享卡/WS/正式日志不复制列表和游标，私有采集见工具详情协议。

## 删除、退出与恢复

会话/工作区变更前先持久化回收门槛和冻结清单，逆启动顺序逐项回收。回收条目持久记录来源、选择原因、
实际动作、身份复核、耗时和结果，单项失败继续其他项。重复/重叠操作不得重复实际停止，结束按完整清单对账。
有未确认项时拒绝删除/换绑/切 World。配置降低配额不是回收授权。
交互删除/停用/换绑的确认与会话资源版本在冻结清单事务中复核；预览后新增实例不能绕过明确确认，旧版本不能先停再报冲突。
应用退出关闭新启动入口，先逐项回收再关闭调度器、DB/日志。包装器不得用固定短超时截断整批回收。
POSIX 包装器通过独占控制管道通知后端退出；包装器被强制终止时管道 EOF 同样触发后端正常关闭。
重复终止信号不得重复打断正在执行的回收；该管道属于内部生命周期控制，不是公开 API。
硬退出由监管器控制通道 EOF 触发子进程收口；重启不重放或盲目接管，PID/出生身份不符不得误杀。
未完成回收清单与旧进程实例关联恢复，不能补造上次已完成终态。
回收清单持久化成功后，请求取消不能中断后续目标；宿主继续逐项处理，结束后才向调用者报告取消。
机器审计或某项结果保存失败也应继续尝试其余目标；变更提交前已知故障必须阻止提交，并记录失败批次。
资源已提交后才发生的最终记录故障不能撤销已停止服务或已完成变更；返回错误不表示从未执行，客户端应重新读取实际状态。
停止请求共用每实例的宿主任务；单项等待预算为 12 秒，超时返回 RUNTIME_CLEANUP_UNCONFIRMED 并继续下一项。
超时或客户端取消不撤销正在进行的回收，也不释放仍活动的登记；后续请求复用该任务而不是再次发起停止。
正常宿主以独立控制管道接收监管器的后代回收证明，不把脚本退出码或日志正文当成证明。
监管器也写入与本次启动身份绑定的私有持久凭据；后端硬退出后，只有凭据有效且原根已退出，或已证明主机重启，
才释放旧服务占用。同启动期内缺少凭据的旧服务保留 cleanup_required，不单凭根 PID 消失推测成功。
监管器未送达证明、输出管道未收口或进程仍存活时不算已确认回收；脚本自行返回 125 等退出码不会冒充监管失败。
已进入 cleanup_required 的实例，重启或再次停止时不能仅凭根 PID 消失释放占用；需要保留诊断与未确认状态。
若登记时保存的内核启动 UUID 与当前有效 UUID 不同，可确认旧主机启动期的实例已退出；不向同号的新进程发送信号。
旧数据缺少该身份时不推测。单纯重启后端不能替代主机重启证明。

W1d 首先仅开放已验证的 Linux 后台服务运行器；Windows 在原生进程/Job/UTF-8/宿主消失测试通过前不开放该能力。
不接管 Docker 或其他 daemon 资源，不保证阻止恶意脚本提权或杀监管器，仍非 OS 沙箱。

错误与日志事件在对应权威目录登记；本页不把脚本和输出混入诊断字段。
