# 当前 World 工作区

| 元数据 | 值 |
|---|---|
| 受众 | 公开（Owner 管理接口；Agent 工具为内部契约） |
| 状态 | 已实现单聊/群聊绑定及原生文件工具；命令、Shell、后台服务工具仅限单聊 |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/workspaces.py`、`backend/app/schemas.py`、`backend/app/workspaces/` |
| 关联测试 | `backend/tests/test_workspaces.py`、`frontend/tests/world-managed/world-switching.spec.ts`、`frontend/tests/real-world/workspace-provider.spec.ts` |
| 复核日期 | 2026-09-16 |

## 范围与安全边界

Workspace Binding 属于当前物理 World，只允许当前 World Owner 管理。一个 World 可以保存多个根目录彼此
独立的 Workspace；Owner 在前端为每个 Workspace 手动输入主机绝对路径，后端 canonicalize 后保存并仅向
Owner 返回。切换 World 后必须重新读取列表；Guest 不能枚举工作区、绝对路径或能力状态。产品不使用部署级
allowed-root 白名单限制 Owner 可登记的目录。

W1a 只支持 `managed_directory`，不启动子进程，不提供 Shell、Git、删除、移动或 chmod；原生写入支持在授权根内自动创建缺失的父目录。
文件内容可能发送给角色绑定的模型厂商，因此 `workspace_list/read/write` 均按 dangerous 工具处理；只有
Owner 触发的 single 或 group 会话、角色显式启用、会话绑定 active/available 工作区且 execution lease 为 ready 时
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
  "file_tools": ["workspace_list","workspace_read","workspace_search","workspace_write","workspace_edit"],
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

创建 single 或 group 会话时可以提交 `workspace_binding_id`；也可以随后更新：

```http
PUT /api/conversations/{conversation_id}/workspace
```

```json
{"workspace_binding_id":1,"expected_revision":0}
```

`workspace_binding_id=null` 表示解绑。仅 Owner 自己创建且仍为成员的 single 或 group 会话可绑定当前 World 中 active + available
工作区。成功后会话 `revision + 1`、返回完整会话表示并产生
`conversation_updated` 事件。revision 不匹配返回 `409 CONVERSATION_REVISION_CONFLICT`。

换绑或解绑时，存在 queued/running generation 返回 `409 WORKSPACE_BUSY`；需先停止或等待整条消息链完成。
提交时再次检查活动链与 revision，防止等待期间新消息或其他窗口修改造成错绑。已有托管实例时要求
`confirm_cleanup=true`，沿用运行实例回收协调流程；回收未确认不提交绑定。

前端右侧“会话概览”模块显示工作区，Owner 可打开选择器绑定、更换或选择“不绑定工作区”后保存。
Guest 仅看到是否绑定，不获得目录、配置或修改入口。

### 群聊文件执行

按现有 @ 顺序串行执行（无 @ 不触发角色），每个 `group_role` execution 独立获取并收口 ready lease，
前一角色释放后下一角色才能使用同一工作区；每次工具调用检查 Owner、会话归属、用户和角色成员关系、
角色存活及各自工具开关、工作区文件开关、当前绑定、lease 和 generation 未停止状态。
移出角色、撤销权限或停止后，已捕获工具也不能继续执行；停止整链取消后续角色，已提交文件事实保留，不回滚或重放。
群聊只开放 `workspace_list/read/search/write/edit`，即使角色开启命令、Shell 或后台服务工具也不暴露；
Guest 触发的角色回复不获得文件工具。跨会话工作区租用、hash 冲突和服务占用门槛保持不变。

## W1a 文件工具内部契约

S1 的 workspace_service_status 状态查询不依赖可写工作区 lease；无 ID 时分页列本会话既有服务，
不放开文件、服务启动或停止权限。其参数/归属/分页契约见[服务协议](../messaging/runtime-services.md)，
本领域的 write/edit 服务占用限制仍保持不变。

工具结果使用紧凑 JSON 文本返回模型，原文不得进入日志或公开工具过程卡。

| 工具 | 输入 | 结果 |
|---|---|---|
| `workspace_list` | `path="."`、`after_name?`、`limit=1..200` | UTF-8 名称稳定排序的 `items` 与 `truncated/next_after_name`；symlink 只报告不跟随，敏感/非法名称不发送给模型 |
| `workspace_read` / `workspace_search` | T2 字节/行范围读取与文件名/文本定位 | [搜索与范围读取权威契约](../../internal/workspace-search-read.md)；旧单文件五字段响应兼容 |
| `workspace_write` | 旧 `path/content/expected_sha256?` 或新 `items` | 旧形式保留 `created/bytes/sha256` 并兼容新增父目录计数；批次结果见下文；新建要求不存在，更新要求旧 hash |
| `workspace_edit` | 单文件旧片段或 `replacements`；跨文件 `items` | 旧形式保留 `created=false/bytes/sha256`；批次结果见下文；精确替换已有 UTF-8 文件，见本篇 T3 契约 |

写入 UTF-8 编码后最大 1 MiB。新建使用 exclusive create；更新在工作区写锁内最终复核 hash，通过同目录
临时文件、flush/fsync 与 atomic replace 完成。`expected_sha256` 为空不表示允许覆盖。

E1 edit 不隐含 read/write 开关：角色必须显式启用 workspace_edit，工作区沿用 file_tools_enabled，限 Owner 触发的单聊或串行群聊。
old_text 非空（纯空白片段可以编辑），new_text 可为空但不删除文件；两片段 UTF-8 合计最多 65536 字节，
schema 每片段最多 65536 字符、path 最多 1024 字符，超字符护栏/未知参数/缺少或非法 hash 为 WORKSPACE_EDIT_ARGUMENT_INVALID。
先复核全文件 hash，再字面搜索 old_text：零匹配或多处匹配（含重叠）分别拒绝，不做正则/模糊匹配、全局替换、缩进或换行归一化。
在同一工作区锁内从本次读取的版本构造新内容，输出仍限 1 MiB，复用 write 的提交前 hash 复核和原子替换。
版本冲突只能重新读取/确认后重试，不自动覆盖。运行中服务对 edit 的限制与 write 相同；不隐含停服授权。
差异采集、预算、取消和 Owner 展示复用[工具详情](../messaging/tool-details.md)的 write 对象，不新增独立 diff 协议。
大文件小修改允许执行，但超过 D 的前后合计计算预算时 diff 仍明确降级，不在 E1 偷偷提高预算。

### 写入自动创建父目录

workspace_write 的 path 与 items 形式均自动创建缺失的父目录，不要求模型先调用 Shell。
先检查完整路径的敏感组件/深度、已有祖先的目录类型与链接、内容大小及文件版本；批量全批预检无目录副作用。
预检通过后，在每项实际写入阶段持既有写锁逐层创建并重新校验路径，文件目标仍遵守独占新建与旧 hash 更新规则。
同批目标之间存在文件/目录祖先冲突时整批拒绝；已有非目录祖先、链接或敏感路径不得替换或绕过。
创建目录失败返回 WORKSPACE_PARENT_NOT_FOUND；之后文件失败可能保留空目录，不回滚或自动删除目录。
文件提交凭据仅证明文件内容提交，不能把创建父目录当成文件提交成功；目录计数和兼容语义见[工具详情](../messaging/tool-details.md#自动创建父目录的调用级证据)。无法抵抗外部进程在路径检查与文件系统操作之间刻意替换路径，不承诺 OS 级隔离。
本补充不改变工作区登记时 create_directory 的单层目录行为，不扩展 edit 为文件创建，也不修改写队列或大小额度。

### T2 搜索、范围读取与统一准入

T2 复用 workspace_read 与原工作区文件开关，新增角色显式 workspace_search 开关，默认不为旧角色启用。
扫描/内容/完整 JSON 预算、按行/字节模式、expected_sha256、批次状态、排队与取消以
[搜索与范围读取协议](../../internal/workspace-search-read.md)为唯一权威。本篇不再重复旧 E2 的 4 KiB/32 KiB 申请值预算。
Owner 逐项/行范围/搜索详情以[工具详情](../messaging/tool-details.md)为准，Guest 不获得查询、路径或正文。
原实验 workspace_read_many 不再暴露，旧记录仍只读兼容；不把只读重试规则用于写入或自动恢复已中断执行。

### E2 多文件写入/编辑（已人工验收，内部工具契约）

write/edit 的 items 为非空数组，不设固定文件项数上限（2026-09-16 修改已人工验收），严格互斥于该工具所有旧顶层字段（含 null/默认值），不新增角色开关。
items 内分别使用 write/edit 单项字段（edit 兼容本篇 T3 replacements）；不允许逐项指定操作、工作区、执行身份或额外参数。
旧合法单文件响应不变；items 即使只有一项也返回批次信封。write 形态错误为 WORKSPACE_WRITE_ARGUMENT_INVALID，
edit 沿用 WORKSPACE_EDIT_ARGUMENT_INVALID；内部整批校验另使用 WORKSPACE_BATCH_ARGUMENT_INVALID。
创建 expected_sha256 为空；已有目标必须匹配最近读取的 hash。每个 edit 的片段仍限 64 KiB，最终文件仍限 1 MiB。
批量 write/edit 取消整批256 KiB参数及结果元数据预留准入检查；不改变单文件大小与编辑片段边界。既有写队列容量与等待参数累计量仍生效，负载拒绝使用原等待诊断，不再作为单批输入大小限制。

修改准入现已覆盖单项和批量，采用[有界等待](#原生修改的有界等待)；复用工作区命令锁，繁忙原因按固定阶段区分。
全批逐项授权并复用单文件准备校验：大小、待写内容及 edit 目标的 UTF-8、敏感路径/链接、父目录、hash、唯一匹配与最终文件大小；
预检阶段无文件写入。规范目标或已有硬链接身份重复拒绝 WORKSPACE_BATCH_TARGET_CONFLICT。
执行按输入顺序，每项重新授权/校验，包括服务占用；每项锁外计算 D diff。全批不是事务，预检不隔离外部文件变化。
首个执行失败停止后续项，无自动回滚或重试；只重试明确失败/未执行项且重新取得版本，未确认项必须先核查。
没有持久请求键或跨调用 exactly-once 保证：重复旧批次通常因 hash/目标存在预检失败，no-op 可再次确认；
hash 不防外部 ABA，也不是重放授权。generation/消息幂等和重启不重放语义不变。

模型结果为紧凑 JSON：version=1、status、error_code（可空）、items；每项 id=item-0..item-(n-1)、path、operation=write/edit、
status、applied（true/false/null）、error_code（可空）、result（可空或原 created/bytes/sha256）。不包含 diff/原文。
父状态 running/success/partial/failed/rejected/cancelled/result_unconfirmed；子状态 not_executed/running/success/failed/result_unconfirmed。
applied=false 仅表示明确无提交；未知或部分 OS 写入为 null，不把“调用失败”当成“文件没改”。
预检失败父 rejected，失败项 failed，其他 not_executed；执行失败且已有成功为 partial。未知提交状态优先 result_unconfirmed。
正常取消父 cancelled，保存已观察提交；未启动项保持 not_executed，已进入文件操作但无法确认的项为 result_unconfirmed。
全成功无失败前缀；rejected 使用原拒绝前缀，其余非成功返回原失败前缀。共享卡只显示父固定错误码，无文件清单。

模型结果不含源码/diff，完整返回所有节点的路径、状态和结果；Owner详情同样保留完整逐项事实，整体不再限64 KiB。体积随项数和路径增长，不承诺恒定响应大小。
仅所有非空 write 差异对象的紧凑JSON字节合计限64 KiB、diff合计限1000行；按节点均分展示预算，放不下时 write=null，绝不丢弃节点或提交结果。各项的diff输入/计算/排队沿用 D 原池与预算，不把展示省略变成写入失败。
详情计算/保存失败不改变已成功文件结果，不回滚、不补写。取消或崩溃的私有事实边界见[工具详情](../messaging/tool-details.md)。
不保存整批文件快照、不新增数据库表或调度系统。仍禁止擅自停止其他会话服务。

路径只接受 UTF-8 相对路径；拒绝绝对路径、空字节、`..`、盘符、UNC、环境变量、glob、符号链接逃逸、
`.git`、真实 `.env`、私钥和实例密钥路径。目录名、文件正文、写入内容和绝对路径不得进入正式日志、审计
摘要、E2E summary 或测试失败文本。

## 适用错误码

### S2 原生修改可用性诊断（已人工验收）

write/edit 的 path/items 形式复用类型化可用性判断，不放开现行服务占用或生命周期门槛。
先校验 Owner 单聊或串行群聊、角色存活、双方成员、有效 execution/generation 及绑定所有权；身份/归属失效仍返回
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
`WORKSPACE_PARENT_NOT_FOUND`、`WORKSPACE_TOOL_NOT_AVAILABLE`；状态码、终态和重试
语义只以 [错误码注册表](../../error-codes.md) 为准。
E1 另使用 WORKSPACE_EDIT_ARGUMENT_INVALID、WORKSPACE_EDIT_INPUT_TOO_LARGE、WORKSPACE_EDIT_MATCH_NOT_FOUND、
WORKSPACE_EDIT_MATCH_AMBIGUOUS；这些写前拒绝可作为共享工具卡的固定错误码，不包含片段原文。

## 兼容性与降级

会话表示新增可空 `workspace_binding_id`，旧客户端忽略后仍可进行普通聊天。没有绑定或能力关闭时不向模型
发送工具 schema，普通 single/M4a 行为不变。未知工作区状态应按 unavailable 展示，不能回退到任意目录。


### 原生修改的有界等待

单项与批量 write/edit 共用进程内 FIFO 写入准入：2 个执行、8 个等待，等待参数实际 UTF-8 JSON 合计最多 2 MiB。
排队与各次工作区命令锁/文件写锁等待累计最多 30 秒；不重置等待额度，不将文件同步提交放进等待超时。保留待执行参数，不自动重放已提交或未知操作。
排队/锁等待后按原规则重新授权和验证版本；全批预检前拒绝全部未执行，执行阶段失败保留前项并停止后项。
WORKSPACE_BATCH_BUSY 的 details/wait_diagnostic 仅携带 phase=queue/lock、reason=queue_full/queue_bytes/queue_timeout/lock_timeout/closed。
批量 wait_diagnostic 位于结果根，单项位于错误 details；均为可选兼容字段，旧客户端可忽略。取消/关闭移除等待者并回收活跃任务。
当前队列不提供跨进程互斥，也未接入尚未实现的 Agent 墙钟预算；Shell 实际执行仍使用原互斥锁，人工审批等待不持该锁。


### T3 多片段编辑

workspace_edit 在旧 old_text/new_text 之外兼容增加 replacements=[{old_text,new_text}, ...]，两种形式严格互斥（含显式 null）。
顶层单文件和 items 内节点均支持；每个文件节点 1..32 项，片段合计 UTF-8 64 KiB，最终文件 1 MiB；单文件片段边界、排队/权限/服务保护不变；批次项数与总JSON按本篇E2最新修订。
所有旧片段在同一原始版本唯一匹配，区间不得重复或重叠；允许相邻片段，不允许依赖前项生成文本。不做正则、Unicode 或换行规范化。
全部通过后构造最终内容并复用一次原子替换与一份 diff。任意匹配校验失败，不改变原文件；不扩展为跨文件事务或断电保证。
匹配失败的 details（批次节点为 edit_error）包含一基 replacement_index；重叠另含 conflicting_replacement_index。
recovery 固定为 reread_and_adjust 或 split_non_overlapping，不回显正文。原错误码沿用，新增 WORKSPACE_EDIT_OVERLAP。
成功返回结构沿用 created/bytes/sha256；内容相同沿用 unchanged diff。新输入的 Owner 采集只保留替换数量/各项字节数，未知字段和原始片段不进入日志。
