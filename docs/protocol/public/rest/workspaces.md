# 当前 World 工作区

| 元数据 | 值 |
|---|---|
| 受众 | 公开（Owner 管理接口；Agent 工具为内部契约） |
| 状态 | W1a/W1b/W1c/E1/E2 与 S2 拒绝诊断已验收；群聊绑定与聊天标题区摘要属于 W2a |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/workspaces.py`、`backend/app/schemas.py`、`backend/app/workspaces/` |
| 关联测试 | `backend/tests/test_workspaces.py`、`frontend/tests/world-managed/world-switching.spec.ts`、`frontend/tests/real-world/workspace-provider.spec.ts` |
| 复核日期 | 2026-09-13 |

## 范围与安全边界

Workspace Binding 属于当前物理 World，只允许当前 World Owner 管理。一个 World 可以保存多个根目录彼此
独立的 Workspace；Owner 在前端为每个 Workspace 手动输入主机绝对路径，后端 canonicalize 后保存并仅向
Owner 返回。切换 World 后必须重新读取列表；Guest 不能枚举工作区、绝对路径或能力状态。产品不使用部署级
allowed-root 白名单限制 Owner 可登记的目录。

W1a 只支持 `managed_directory`，不启动子进程，不提供 Shell、Git、删除、移动、chmod 或自动创建父目录。
文件内容可能发送给角色绑定的模型厂商，因此 `workspace_list/read/write` 均按 dangerous 工具处理；只有
Owner 触发的 single 会话、角色显式启用、会话绑定 active/available 工作区且 execution lease 为 ready 时
才向模型暴露，并在每次调用前重新鉴权和解析路径。

## 表示

```json
{
  "id": 1,
  "display_name": "示例工作区",
  "root_path": "/srv/example/workspace-a",
  "workspace_kind": "managed_directory",
  "file_tools_enabled": true,
  "basic_commands_enabled": false,
  "shell_enabled": false,
  "active": true,
  "availability": "available",
  "last_validated_at": "2026-09-02T12:00:00+08:00",
  "bound_conversation_count": 1,
  "created_at": "2026-09-02T12:00:00+08:00",
  "updated_at": "2026-09-02T12:00:00+08:00"
}
```

`availability` 为 `available/unavailable/busy/disabled`。W1b 起 `basic_commands_enabled` 可由 Owner 开启，
与文件开关独立；W1c 起 `shell_enabled` 默认 false，可由 Owner 独立开启，但每次调用仍需单独审批。
命令契约见 [结构化命令](../../internal/workspace-commands.md)，Shell 开关及逐次执行边界见 [Shell 审批](../messaging/shell-approvals.md)。

## 能力视图

```http
GET /api/workspaces/capabilities
```

```json
{
  "world_name": "default",
  "workspace_kinds": ["managed_directory"],
  "file_tools": ["workspace_list","workspace_read","workspace_write","workspace_edit"],
  "basic_commands_available": true,
  "shell_available": true,
  "shell_kind": "bash",
  "shell_approval_mode": "per_call",
  "shell_timeout_seconds": 30,
  "shell_output_bytes": 65536
}
```

能力接口不返回或限制 Workspace 根路径；未登记 Workspace 时也不回退到后端 cwd、源码目录、用户主目录或
文件系统根。
Shell 不受支持或未解析到允许的可执行文件时，shell_available=false、shell_kind=null，开启 Shell 返回
409 SHELL_NOT_SUPPORTED；能力视图从不返回可执行文件绝对路径。PATCH 兼容新增 shell_enabled 布尔值。

## 列表、登记、复核与解除登记

```http
GET /api/workspaces
POST /api/workspaces
POST /api/workspaces/{workspace_id}/validate
PATCH /api/workspaces/{workspace_id}
DELETE /api/workspaces/{workspace_id}
```

登记请求：

```json
{
  "display_name": "示例工作区",
  "root_path": "/srv/example/workspace-a",
  "create_directory": false,
  "acknowledge_existing_content": true
}
```

- `root_path` 必须是当前后端主机上的绝对路径；拒绝空字节、`~`、未展开环境变量和 glob，并保存 canonical 路径。
- `create_directory=true` 只在精确目标不存在且父目录已经存在时创建一个空目录；不接管或覆盖既有目标。
- 登记既有目录必须设置 `acknowledge_existing_content=true`，确认其中内容可能发送给模型。
- `PATCH` 接受 `active`、`file_tools_enabled` 与 `basic_commands_enabled`；禁用后不能新建 execution lease，
  下一次工具调用重新校验能力。模型不能通过参数修改超时、输出限制或环境。
- `DELETE` 只解除数据库登记并让会话绑定置空，不删除物理目录；重复解除返回 `404 WORKSPACE_NOT_FOUND`。
- 列表按自增 ID 稳定排序；复核重新 canonicalize 绝对根并更新 `last_validated_at`。路径在 World 移到另一台
  主机后不存在时显示 unavailable，Owner 可解除后按新路径重新登记。

## 会话绑定

创建 single 会话时可以提交 `workspace_binding_id`；也可以随后更新：

```http
PUT /api/conversations/{conversation_id}/workspace
```

```json
{"workspace_binding_id":1,"expected_revision":0}
```

`workspace_binding_id=null` 表示解绑。仅 Owner 自己创建的 single 会话可绑定当前 World 中 active + available
工作区；群聊返回 `422 SINGLE_CHAT_REQUIRED`。成功后会话 `revision + 1`、返回完整会话表示并产生
`conversation_updated` 事件。revision 不匹配返回 `409 CONVERSATION_REVISION_CONFLICT`。

## W1a 文件工具内部契约

S1 的 workspace_service_status 状态查询不依赖可写工作区 lease；无 ID 时分页列本会话既有服务，
不放开文件、服务启动或停止权限。其参数/归属/分页契约见[服务协议](../messaging/runtime-services.md)，
本领域的 write/edit 服务占用限制仍保持不变。

工具结果使用紧凑 JSON 文本返回模型，原文不得进入日志或公开工具过程卡。

| 工具 | 输入 | 结果 |
|---|---|---|
| `workspace_list` | `path="."`、`after_name?`、`limit=1..200` | UTF-8 名称稳定排序的 `items` 与 `truncated/next_after_name`；symlink 只报告不跟随，敏感/非法名称不发送给模型 |
| `workspace_read` | 旧 `path/offset_bytes/max_bytes`，或新 `items`，两种互斥 | 旧形式保留 `text/bytes/eof/next_offset/sha256`；items 形式返回下述逐项结果，含一项也不改变结果形态 |
| `workspace_write` | 旧 `path/content/expected_sha256?` 或新 `items` | 旧形式保留 `created/bytes/sha256`；批次结果见下文；新建要求不存在，更新要求旧 hash |
| `workspace_edit` | 旧 `path/old_text/new_text/expected_sha256` 或新 `items` | 旧形式保留 `created=false/bytes/sha256`；批次结果见下文；唯一字面替换已有 UTF-8 文件 |

写入 UTF-8 编码后最大 1 MiB。新建使用 exclusive create；更新在工作区写锁内最终复核 hash，通过同目录
临时文件、flush/fsync 与 atomic replace 完成。`expected_sha256` 为空不表示允许覆盖。

E1 edit 不隐含 read/write 开关：角色必须显式启用 workspace_edit，工作区沿用 file_tools_enabled，仍限 Owner single。
old_text 非空（纯空白片段可以编辑），new_text 可为空但不删除文件；两片段 UTF-8 合计最多 65536 字节，
schema 每片段最多 65536 字符、path 最多 1024 字符，超字符护栏/未知参数/缺少或非法 hash 为 WORKSPACE_EDIT_ARGUMENT_INVALID。
先复核全文件 hash，再字面搜索 old_text：零匹配或多处匹配（含重叠）分别拒绝，不做正则/模糊匹配、全局替换、缩进或换行归一化。
在同一工作区锁内从本次读取的版本构造新内容，输出仍限 1 MiB，复用 write 的提交前 hash 复核和原子替换。
版本冲突只能重新读取/确认后重试，不自动覆盖。运行中服务对 edit 的限制与 write 相同；不隐含停服授权。
差异采集、预算、取消和 Owner 展示复用[工具详情](../messaging/tool-details.md)的 write 对象，不新增独立 diff 协议。
大文件小修改允许执行，但超过 D 的前后合计计算预算时 diff 仍明确降级，不在 E1 偷偷提高预算。

### E2 多文件读取（已人工验收，内部工具契约）

workspace_read 同时支持旧单文件形式和新 items 形式，共用原角色 read 权限、工作区 file_tools_enabled 和 Owner single lease。
旧形式 path 必填，offset_bytes 默认 0，max_bytes 默认 65536，范围及原五字段响应不变；items 即使仅一项也返回批次结果。
items 与任何顶层 path/offset_bytes/max_bytes 键严格互斥（包含 null/默认值），缺失模式、items=null、未知字段或非法类型
返回 WORKSPACE_READ_ARGUMENT_INVALID；嵌套 items 严格整数。旧实验 workspace_read_many 不再暴露/执行，旧角色如仅勾选
实验工具需手动开启 workspace_read，不静默转换权限。历史工具身份不改写，Owner 仍可读取已保存实验记录。
items 长度 1..8，每项 path 长度 1..1024、offset_bytes 默认 0 且 0..2^63-1、max_bytes 默认 4096 且 1..32768；
整数不接受 bool/字符串，未知参数拒绝。标准化输入紧凑 UTF-8 JSON 最大 16384 字节，max_bytes 合计最大 32768。
宿主同时最多 2 批，每批最多 2 项进入授权/读取，不设批次等待队列；其余拒绝 WORKSPACE_BATCH_BUSY。
每项复用完整的路径/敏感文件/链接/UTF-8/1 MiB 文件限制；stat 后实际读取也有 1 MiB+1 字节硬上限。
读取原语不使用新线程池，不承诺磁盘并行加速；逻辑在途读取最多 4 个 1 MiB 文件，保留结果每批最大 64 KiB，
Python 编码对象和临时 JSON 另有开销，不把逻辑字节预算宣传为进程 RSS 上限。

结果为紧凑 JSON：version=1、status、error_code（可空）、items。子项按输入顺序，含 id=item-0..item-7、
operation=read、path、status、error_code、output_limited、result（失败/未执行为 null；成功为单项 read 的五字段）。
子状态 pending/running/success/failed/rejected/cancelled/not_executed；父状态 running/success/partial/failed/cancelled/rejected。
失败与部分完成用既有工具失败前缀，准入/形态拒绝用拒绝前缀；共享工具卡只取得固定顶层错误码，不含逐文件清单。
每项进入读取前重新授权，一项失败继续其他项。输出 JSON 总量不超过 65536 UTF-8 字节，按项等分扣除信封后的预算；
仅当转义后超额时缩短 text，按实际 UTF-8 字节重算 bytes/next_offset、eof=false，output_limited=true，sha256 不变。
不能用截断 JSON 的方式丢失 hash 或游标；单字节预算必要时沿用单文件最多补齐 3 字节的字符边界规则。

取消等待已启动任务收尾，保留已观察成功/失败，未开始为 not_executed、已开始但未返回为 cancelled。
私有逐项结果复用 generation 文件采集作用域，在工具结束或正常取消收尾时由消息所有者加密落库。
进程崩溃时未落库的逐项事实不补造，显示中断和逐项结果未记录；不自动重启 generation 或重读文件补历史。
重复路径/请求允许新的独立观察，hash 可能变化，不是同一时刻快照或历史幂等缓存；只读重试不会写文件，
不得把这种重试方式用于批量写入。Owner 展示契约见[工具详情](../messaging/tool-details.md)。

### E2 多文件写入/编辑（已人工验收，内部工具契约）

原 write/edit 各自增加 items=1..8，严格互斥于该工具所有旧顶层字段（含 null/默认值），不新增角色开关。
items 内分别使用原 write/edit 单项字段；不允许逐项指定操作、工作区、执行身份或额外参数。
旧合法单文件响应不变；items 即使只有一项也返回批次信封。write 形态错误为 WORKSPACE_WRITE_ARGUMENT_INVALID，
edit 沿用 WORKSPACE_EDIT_ARGUMENT_INVALID；内部整批校验另使用 WORKSPACE_BATCH_ARGUMENT_INVALID。
创建 expected_sha256 为空；已有目标必须匹配最近读取的 hash。每个 edit 的片段仍限 64 KiB，最终文件仍限 1 MiB。
整批紧凑 UTF-8 输入最多 256 KiB；元数据预留按每项 1536 + 2×JSON 路径字节数计算，合计不得超过 32 KiB，
超额写前拒绝 WORKSPACE_BATCH_INPUT_TOO_LARGE。预留只为结果身份/diff 元数据，不是文件源码限额放宽。

宿主最多 2 批（write/edit 共用），无外部批次队列；复用工作区命令锁，每次等待该锁最多 5 秒，超时为 WORKSPACE_BATCH_BUSY。
全批逐项授权并复用单文件准备校验：大小、待写内容及 edit 目标的 UTF-8、敏感路径/链接、父目录、hash、唯一匹配与最终文件大小；
预检阶段无文件写入。规范目标或已有硬链接身份重复拒绝 WORKSPACE_BATCH_TARGET_CONFLICT。
执行按输入顺序，每项重新授权/校验，包括服务占用；每项锁外计算 D diff。全批不是事务，预检不隔离外部文件变化。
首个执行失败停止后续项，无自动回滚或重试；只重试明确失败/未执行项且重新取得版本，未确认项必须先核查。
没有持久请求键或跨调用 exactly-once 保证：重复旧批次通常因 hash/目标存在预检失败，no-op 可再次确认；
hash 不防外部 ABA，也不是重放授权。generation/消息幂等和重启不重放语义不变。

模型结果为紧凑 JSON：version=1、status、error_code（可空）、items；每项 id=item-0..item-7、path、operation=write/edit、
status、applied（true/false/null）、error_code（可空）、result（可空或原 created/bytes/sha256）。不包含 diff/原文。
父状态 running/success/partial/failed/rejected/cancelled/result_unconfirmed；子状态 not_executed/running/success/failed/result_unconfirmed。
applied=false 仅表示明确无提交；未知或部分 OS 写入为 null，不把“调用失败”当成“文件没改”。
预检失败父 rejected，失败项 failed，其他 not_executed；执行失败且已有成功为 partial。未知提交状态优先 result_unconfirmed。
正常取消父 cancelled，保存已观察提交；未启动项保持 not_executed，已进入文件操作但无法确认的项为 result_unconfirmed。
全成功无失败前缀；rejected 使用原拒绝前缀，其余非成功返回原失败前缀。共享卡只显示父固定错误码，无文件清单。

模型 JSON 与 Owner 批次详情各限 64 KiB；Owner 全批 diff 最多 1000 行，各项的输入/计算/排队沿用 D 原池与预算。
差异正文按预留元数据后份额缩减，标记 partial 并保留完整计算的增删统计，不把修改次数称为文件净变化。
详情计算/保存失败不改变已成功文件结果，不回滚、不补写。取消或崩溃的私有事实边界见[工具详情](../messaging/tool-details.md)。
不保存整批文件快照、不新增数据库表或调度系统。仍禁止擅自停止其他会话服务。

路径只接受 UTF-8 相对路径；拒绝绝对路径、空字节、`..`、盘符、UNC、环境变量、glob、符号链接逃逸、
`.git`、真实 `.env`、私钥和实例密钥路径。目录名、文件正文、写入内容和绝对路径不得进入正式日志、审计
摘要、E2E summary 或测试失败文本。

## 适用错误码

### S2 原生修改可用性诊断（已人工验收）

write/edit 的 path/items 形式复用类型化可用性判断，不放开现行服务占用或生命周期门槛。
先校验 Owner single、角色存活、双方成员、有效 execution/generation 及绑定所有权；身份/归属失效仍返回
WORKSPACE_TOOL_NOT_AVAILABLE，不附 diagnostic，也不泄漏服务数量/ID。其后分别报告角色权限/工作区开关变化、
绑定/根变化、租用失效、目录不可用；角色已删除/停用属于身份失效，不借诊断绕过鉴权。

有效绑定上的阻塞优先级：失败范围清理或 cleanup_required 服务 → World closing → 范围清理 running/prepared →
单项 stopping → 其他 ACTIVE 服务。多个清理范围并存按 world/workspace/conversation 排序；范围清理只匹配本 World、
本工作区或本会话。保持原阻塞条件，不把成功停止一个实例当成解除所有门槛。
错误码以注册表为准，单项拒绝使用原拒绝前缀；批次预检失败仍无写入，执行阶段已有成功则父 partial，
仅失败项报告本项未执行并保留之前成功结果/diff，后续项不启动，不自动回滚或整批重放。

单项失败结果兼容增加 diagnostic；items 仅失败节点兼容增加此字段，不在父级声称整批未执行。
结构为 version=1、reason、scope（world/workspace/conversation/tool）、executed=false、固定 message/next_steps，
recommended_tool（workspace_service_status 或 null）、services（最多3项 runtime_id/state，未授权/未查询为 null）、
services_truncated、other_sessions_blocking（未判断时 null）。reason 与注册表原因对应；完整紧凑 UTF-8 JSON <=2048 字节。
实例明细必须同时满足本轮实际工具集中存在 status 和当前角色仍启用它；否则只提示 Owner /ps，不返回明细或推荐未暴露工具。
明细只包含同工作区且 owner/存活会话关联/原会话身份都匹配的服务；其他会话或历史归属仅返回布尔汇总，
不返回对方 ID、会话名、脚本或日志。提示不授权停止、不建议通过 Shell 绕过拒绝，也不保证查询后状态仍相同。

完整诊断只进入模型结果及 Owner 加密详情；共享工具卡和 WS/正式日志仅提取固定错误码，不复制诊断正文/实例清单。
公共旧客户端可忽略新增字段，旧成功响应不变。S1 状态查询及 Shell/日志/停止的可执行条件不因诊断而改变。
前端展示与私有保存规则见[工具详情](../messaging/tool-details.md)。

本领域使用 `WORKSPACE_NOT_FOUND`、`WORKSPACE_ROOT_PATH_INVALID`、`WORKSPACE_ROOT_NOT_AVAILABLE`、`WORKSPACE_PATH_INVALID`、
`WORKSPACE_PATH_OUTSIDE_ROOT`、`WORKSPACE_PATH_SENSITIVE`、`WORKSPACE_DIRECTORY_NOT_FOUND`、
`WORKSPACE_DIRECTORY_EXISTS`、`WORKSPACE_EXISTING_CONTENT_ACK_REQUIRED`、`WORKSPACE_NAME_CONFLICT`、
`WORKSPACE_PATH_CONFLICT`、`WORKSPACE_UNAVAILABLE`、`WORKSPACE_BUSY`、`WORKSPACE_FILE_NOT_FOUND`、
`WORKSPACE_FILE_NOT_TEXT`、`WORKSPACE_FILE_TOO_LARGE`、`WORKSPACE_FILE_REVISION_CONFLICT`、
`WORKSPACE_PARENT_NOT_FOUND`、`WORKSPACE_TOOL_NOT_AVAILABLE` 与 `SINGLE_CHAT_REQUIRED`；状态码、终态和重试
语义只以 [错误码注册表](../../error-codes.md) 为准。
E1 另使用 WORKSPACE_EDIT_ARGUMENT_INVALID、WORKSPACE_EDIT_INPUT_TOO_LARGE、WORKSPACE_EDIT_MATCH_NOT_FOUND、
WORKSPACE_EDIT_MATCH_AMBIGUOUS；这些写前拒绝可作为共享工具卡的固定错误码，不包含片段原文。

## 兼容性与降级

会话表示新增可空 `workspace_binding_id`，旧客户端忽略后仍可进行普通聊天。没有绑定或能力关闭时不向模型
发送工具 schema，普通 single/M4a 行为不变。未知工作区状态应按 unavailable 展示，不能回退到任意目录。
