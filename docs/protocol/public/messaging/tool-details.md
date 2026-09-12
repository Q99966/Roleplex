# 工具执行详情

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner 接口；采集与存储为内部约定 |
| 状态 | D/E1、E2 探索归组与批量读取节点已人工验收 |
| 协议版本 | 4（兼容新增 read_batch 私有文件节点） |
| 维护者 | Roleplex |
| 事实来源 | `app/services/tool_details.py`、`app/routers/messages.py`、`ToolExecutionDetail` |
| 复核日期 | 2026-09-12 |
| 测试 | `backend/tests/test_tool_timeline.py`、`frontend/tests/commands/`、`frontend/tests/real-world/commands-provider.spec.ts` |

## 接口与资源归属

`GET /api/conversations/{conversation_id}/messages/{message_id}/tools/{call_id}`

需要有效 Owner Token、当前 World 会话归属和用户成员关系；回收站会话不可读，Guest 固定 403 OWNER_REQUIRED。
消息、会话或工具调用不匹配返回 404 TOOL_DETAILS_NOT_FOUND（会话不可见时 CONVERSATION_NOT_FOUND）。
响应 `Cache-Control: no-store`。客户端切换账号/World/会话时清除已加载详情，不使用 localStorage 缓存。
该只读接口幂等，不执行工具、不读取宿主文件，也不读取机器日志文件。

返回 availability 为 available/not_recorded/expired/unavailable。存在记录时还包含 tool_name、status、
started_at、ended_at（可空）、expires_at、input/output（可空）。input/output 各为
`{text, bytes, truncated}`：text 为有界文本，bytes 为截断前 UTF-8 字节数，truncated 表示截断。
status 为 running/success/failed/rejected/cancelled/interrupted；未记录输出不是空输出，工具未结束时 output=null。
expired 和 unavailable 不返回输入输出；解密失败为 unavailable，不回显密文、异常或密钥。

## 采集与保留

通用采集覆盖已实现的 workspace_list/read/write/run_command；输入仅保留对应工具 schema 的字段，未知字段不保存。
workspace_run_shell 使用下述专用审批关联和结构化输出规则，不把脚本加入通用输入采集白名单。
未知/MCP/未接入工具仅有摘要，不自动采集原始对象。每份输入/结果最多 65536 UTF-8 字节，不截坏字符，
移除终端控制序列。内容按文本显示，禁止作为 HTML、脚本或终端转义执行。

详情是 Owner 私有业务数据，用当前 World 密钥按独立用途派生密钥加密；密文绑定 message_id 与 call_id，
不能跨调用替换。只有通过服务端资源鉴权后才解密；不进入共享消息/WS、机器日志、工具审计或失败报告。
开始时保存输入，结束时保存结果；由消息所有者在更新工具 part 的同一事务写入，无逐 token/输出块事务。
调用开始 7 天后接口立即视为 expired，启动清除过期密文，随消息物理删除级联删除。取消只记录已观察到的
状态，无结果时 output=null；重启将 running 变 interrupted，ended_at 为空，不推断工具退出结果。

旧调用返回 not_recorded，不从文件或日志补造详情；已有摘要不变。公开工具卡仍只含安全元数据。

## Shell 详情扩展（W1c 验收修正，已实现、已人工验收）

Owner 仍通过同一详情端点读取，兼容增加 `shell` 对象：
`script`（ToolCapture 或 null）、`approval_status`（pending/approved/rejected/expired 或 null）、
`approval_wait_ms`（决定时间减申请时间；未决定为 null）、`execution_duration_ms`（运行器实测；未知为 null）、
`stdout` / `stderr`（ToolCapture 或 null）、`output_availability`（recorded/pending/not_executed/not_recorded）。
另含 `execution_status`（exited/timed_out/cancelled/not_executed/unavailable 或 null）与 `exit_code`（整数或 null）。
调用总耗时沿用共享 part.duration_ms，不与实际执行耗时混称。

脚本通过 message → generation → execution → approval 的完整归属链和 call_id/tool_name 关联读取，复核密文
摘要及身份；不复制进 ToolExecutionDetail.input_encrypted。详情创建不再要求 Shell 有私有输入副本。
stdout/stderr 正文合计保留最多 65536 UTF-8 字节，元数据另计，清理终端控制符且保持结构化表示，不截坏 JSON。
每个流分别保留运行器原始字节数与截断标记；实际进程空输出为非空 capture 对象、text=""，不等于未记录。
输出和实测执行元数据保存于既有 output_encrypted，由消息所有者在工具结束事务中写入，不逐输出片段落库。

展示期限为调用开始后 7 天，旧调用没有详情行时以审批申请时间计算；过期/解密失败不返回任何脚本和输出。
输出沿用既有启动清理与级联删除；关联展示不会改变审批审计密文的原有保留策略。
旧 Shell part 没有 detail_available 时也允许 Owner 主动查询：审批可关联则恢复脚本及已知决定，未保存的输出和
实测执行耗时必须标记未记录。拒绝/过期明确为未执行；批准本身不证明进程实际执行或成功。
不从总耗时减审批耗时估算执行耗时，不从模型回答、文件或机器日志还原输出，不重新执行旧脚本。

## D 写入差异扩展（已实现，已人工验收）

同一 Owner 详情端点兼容增加 `write`，旧 input/output 与 shell 语义不变。write 为
`{version:1, availability, reason, files}`，独立对象序列化 UTF-8 最多 65536 字节；D 的 files 最多一个，
不代表批量执行。availability 为 recorded/partial/unavailable/not_executed/result_unconfirmed/pending/not_recorded；
reason 可空，非空仅 input_budget/line_budget/queue_full/queue_timeout/compute_timeout/cancelled/capture_failed/not_text/shutdown。

文件节点含 id（本调用内 file-0）、path（规范相对路径）、operation（created/modified/unchanged）、applied（bool 或 null）、
before_sha256（新建为 null）、after_sha256、before_bytes、after_bytes、added/removed（无法完整计算时 null）、hunks。
hunk 含 old_start/old_lines/new_start/new_lines 与 lines；行含 kind（context/insert/delete）、old_line/new_line（不适用为 null）、
text（不含该行换行符）与 ending（lf/crlf/none）。CR 未紧邻 LF 时作为原文控制字符，不伪造额外行。
界面将控制字符可见转义，绝不执行 HTML/终端序列；partial 不等于内容无变化，完整统计和已保留行数分开。

差异依据本次原生 write 执行处的旧字节和成功提交的新字节，不能在读取详情时再读宿主文件；不从模型返回值构造旧内容。
计算输入合计 256 KiB、最多 10000 行；展示最多 1000 行、上下文各 3 行。候选库 difflib 使用独立可回收计算进程，
进程内最多 2 个计算、4 个等待（等待原文最多 1 MiB），等待上限 250ms、计算截止 1s；满载/超时只降级差异，
不重写、回滚已完成文件或改变模型可见的 write 结果。计算期间不持有原生写锁或数据库事务。

执行作用域内私有采集通过领域 ToolCallFinished.private_output 交给原消息所有者，不进入模型工具响应、共享 WS 或日志。
复用 output_encrypted 保存带格式版本 write-v1 的私有记录，读取时拆出原 output 和 write；不新增表。
加密绑定、鉴权、7 天期限、过期清理和消息物理删除规则不变；关闭/切换清除前端内容，旧调用返回 not_recorded，不补造差异。
按人工确认的展示修订，Owner 的 write 卡默认展开并在进入可视范围后加载私有详情；diff 直接位于工具卡内，
不再显示原始 input/output 或提供文件节点二级折叠。该调整不删除后端原始记录、不改变接口字段或 Guest 鉴权。
取消后只保留已观察事实：已确认写入可为 applied=true 而差异 unavailable/cancelled；写入结果无法确认则 result_unconfirmed。
not_executed 只用于已知写前拒绝，不用于可能发生部分写入的 OS 故障。落库失败不能据此声称文件未修改，也不自动重试写入。
差异正文加密保存失败时尽力保存有界提交元数据和 capture_failed；加密边界整体不可用则不保存明文并降级未记录。
计算进程无法确认回收时关闭该池准入，不通过新建池绕过限制；应用关闭仍复核回收，失败不伪装成成功。

## 消息顺序与兼容

E1 workspace_edit（已人工验收）复用上述 write-v1 加密格式及公开 write 对象，tool_name 保留 workspace_edit，
只产生 modified/unchanged 节点，不创建文件。私有输入白名单为 path/old_text/new_text/expected_sha256；
片段不进入共享事件、机器日志或摘要。成功提交后的前后字节来自共同文件执行层，不把模型提交的片段当作已应用 diff。
前端与 write 相同默认展开、进入可视范围加载、只显示时间/文件/diff；不展示原始输入输出，Guest 不请求私有详情。

新角色消息 `timeline_version=1`，parts_json 按事件顺序包含 text 和 tool_call；文本段有稳定 part_id。
`message_delta` 兼容新增 part_id/part_index，增量只追加目标文本段。工具开始固定前段，结束原位更新。
工具 part 的 detail_available 表示该调用支持 Owner 详情；这是安全能力标记，不包含私有正文。
旧消息 timeline_version=0，工具位置未记录，客户端降级显示历史执行记录，不能猜测真实位置。
客户端忽略旧 revision，重连/快照复用有序 parts。ContextBuilder 将新分段还原为原正文后投影，保持历史文本语义。

### E2 探索展示归组（已人工验收，不新增 wire contract）

客户端仅对同一条 timeline_version=1 角色消息内连续且身份完整唯一的 workspace_read/workspace_list
进行展示归组；至少两项显示“探索”标题，单项仍为原工具卡。正文（包括空段）、其他/未知工具或内容均是边界，
不跨消息/generation/execution 合并，不从 Shell 脚本推断读取或搜索。旧版与未知版消息逐项保守展示。
这是客户端派生视图，不产生新调用、父子关系、事件或数据库行，不承诺批量执行、并行或文件快照。

摘要只使用共享安全元数据，以调用次数而非文件数统计；失败/拒绝/取消/中断/未知状态在折叠后仍可见。
各调用保留真实名称、身份、状态、耗时及详情入口。组默认展开，用户折叠后流式追加/完成不重置选择；
刷新重新默认展开，调用顺序及既有阅读锚点可恢复，折叠隐藏锚点回退至组标题。
归组不主动预取路径、参数或文件内容，Owner 按需读取原详情端点，Guest 不请求私有详情；权限及保留期限不变。

### E2 批量读取详情（已人工验收）

同一 Owner 端点兼容增加 read_batch（可空）；workspace_read 的 items 形式使用，旧 path 形式仍返回原 input/output，
不增加 read_batch 字段。字段等同该工具已校验的
version/status/error_code/items，逐项结果、字节预算、续读与取消语义以[工作区工具契约](../rest/workspaces.md)为准。
items 形式 output=null，不重复传输第二份大 JSON；input 仅采集 items 的 path/offset_bytes/max_bytes，未知嵌套字段不保存。
无批次输出时仅用已保存输入识别展示模式，不由输入推断执行结果；旧实验 workspace_read_many 历史记录继续只读兼容。
执行处在既有 generation 文件采集作用域保留结果，由原消息所有者加密保存 read-batch-v1 到 output_encrypted，
不新增数据表、Trace 或子调用。详情读取校验 schema 与紧凑 UTF-8 JSON 64 KiB 上限；密文保存失败降级 read_batch=null，
不保存明文、不重新读取。重启前未落库的子项状态不从输入推断；read_batch=null 显示逐项结果未记录或仍等待结束。

批量读取卡仍由 Owner 主动展开，展开后显示按输入顺序的文件节点，每项可独立展开文本/hash/续读游标；
items 形式不显示原始输入/输出 JSON；可按 workspace_read 的连续只读规则参与探索归组，仍保留每个调用的文件节点，
也不把多次观察当成文件净变化。部分失败在外层标“部分完成”，
保留所有成功与失败节点。Guest 不获取文件清单/正文，七天保留、密文身份绑定、历史不重建规则不变。
