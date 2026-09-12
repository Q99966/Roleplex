# 当前 World 工作区

| 元数据 | 值 |
|---|---|
| 受众 | 公开（Owner 管理接口；Agent 工具为内部契约） |
| 状态 | W1a/W1b/W1c 已验收；E1 单文件编辑已人工验收；群聊绑定与聊天标题区摘要属于 W2a |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/workspaces.py`、`backend/app/schemas.py`、`backend/app/workspaces/` |
| 关联测试 | `backend/tests/test_workspaces.py`、`frontend/tests/world-managed/world-switching.spec.ts`、`frontend/tests/real-world/workspace-provider.spec.ts` |
| 复核日期 | 2026-09-12 |

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

工具结果使用紧凑 JSON 文本返回模型，原文不得进入日志或公开工具过程卡。

| 工具 | 输入 | 结果 |
|---|---|---|
| `workspace_list` | `path="."`、`after_name?`、`limit=1..200` | UTF-8 名称稳定排序的 `items` 与 `truncated/next_after_name`；symlink 只报告不跟随，敏感/非法名称不发送给模型 |
| `workspace_read` | `path`、`offset_bytes>=0`、`max_bytes=1..65536` | `text/bytes/eof/next_offset/sha256`；只读最大 1 MiB 的 UTF-8 普通文件且不拆坏字符 |
| `workspace_write` | `path/content/expected_sha256?` | `created/bytes/sha256`；新建要求目标不存在，更新要求 hash 完全匹配 |
| `workspace_edit` | `path/old_text/new_text/expected_sha256`，均必填 | `created=false/bytes/sha256`；仅对已有 UTF-8 文件做一次唯一字面替换（E1 已实现） |

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

路径只接受 UTF-8 相对路径；拒绝绝对路径、空字节、`..`、盘符、UNC、环境变量、glob、符号链接逃逸、
`.git`、真实 `.env`、私钥和实例密钥路径。目录名、文件正文、写入内容和绝对路径不得进入正式日志、审计
摘要、E2E summary 或测试失败文本。

## 适用错误码

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
