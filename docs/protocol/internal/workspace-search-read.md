# 工作区搜索与范围读取（T2）

| 元数据 | 值 |
|---|---|
| 受众 | 内部 Agent 工具与前后端维护者 |
| 状态 | 已实现，已通过人工验收 |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 事实来源 | `app/workspaces/scanning.py`、`scan_admission.py`、`batch_read.py`、`tools.py` |
| 关联测试 | `tests/test_workspace_search_read.py`、`test_workspace_read_many.py`；对应 Playwright 与独立真实 Provider 用例 |
| 复核日期 | 2026-09-14 |

## 权限与范围

只在当前 Owner 触发的 single 或 group 的 active managed_directory、角色显式启用对应工具、工作区原生文件能力开启时暴露。
workspace_search 新开关默认关闭；沿用 dangerous 分类。角色/成员/绑定在排队后、文件扫描前及结果返回前复核。
宿主根、execution 与权限均由服务端绑定；查询不是 Shell，不读取真实凭据，不绕过既有路径/链接保护。
写入、编辑仍限 1 MiB，停服保护不变。无 Git、后台索引、正则或跨会话搜索。

## 首版预算

首版参数输入最多 16 KiB；read 默认返回额度统一 64 KiB，items 最多 8 个，整批内容默认 64 KiB、完整 JSON 最多 64 KiB。
按请求顺序扣实际返回字节，状态/版本/游标预留输出空间；申请上限之和不作为拒绝条件。
主机 `WORKSPACE_READ_CONTENT_BYTES` 可在 1 KiB..64 KiB 内控制实际内容预算，模型不能提高主机上限。
完整 JSON 计入转义后的真实大小，只缩正文，不丢结构字段；旧单文件仍返回原 text/bytes/eof/next_offset/sha256 五字段。

`WORKSPACE_SCAN_FILE_BYTES` 默认 16 MiB、硬上限 64 MiB（最小 1 MiB）；`WORKSPACE_SCAN_TOTAL_BYTES` 默认 64 MiB、
硬上限 256 MiB（最小 1 MiB）。`WORKSPACE_SCAN_SECONDS` 默认 3 秒，范围 0.05..30 秒。均是扫描保护，不是写入额度。
64 KiB 分块扫描，拒绝无效 UTF-8 与含 NUL 的二进制样式内容，增量校验全文件 SHA-256；保留片段和有限行缓冲，不加载整个文件。扫描前后复核文件身份/大小/时间；
检测到变化返回版本冲突，不承诺抵抗外部进程刻意伪造元数据或 OS 强快照。截止在扫描检查点生效，不声称能强制打断内核磁盘读取。

每进程共享 2 个执行槽、最多 8 个 FIFO 等待者，排队输入最多 128 KiB；等待 3 秒，满时立即拒绝。
同一宿主任务的内部扫描借用已持有的槽位，批次按序处理，不再嵌套申请或无界创建任务。search/read 的所有参数形式共用准入。
关闭取消当前扫描并拒绝等待者；异常、超时和取消移除等待者并释放槽位；不改变 Shell/命令的独立策略。

## workspace_read

旧 path/offset_bytes/max_bytes 或 items 保持兼容。兼容增加 expected_sha256，给定时必须核验全文件版本，否则是新的观察。
items 的每个节点默认 max_bytes=65536，上限同值；与旧 4 KiB 默认值变化应同步工具策略版本。
单节点行模式使用 start_line（必填，从 1 开始）、end_line（可省略，默认读取 200 行，含两端），最大区间 2000 行。
行模式不得与 offset_bytes/max_bytes 混传；items 不与顶层单文件字段混传；不接受 null 表示另一种模式。

行结果包含 mode=lines、text、bytes、请求 start_line/实际 end_line（空范围时 end_line=null）、start_offset（可转字节模式）、next_line、eof、sha256、scanned_bytes、limited_reason。
LF/CRLF 均以 LF 为行分隔，原换行字节保留；无末尾 LF 的非空尾段为一行；空文件零行；越过 EOF 返回空内容、eof=true。
只返回完整行，遇到单行超出本次额度时停止并标 line_too_long，建议使用字节模式；不越过该行或伪造完整行。
字节读取不截坏 UTF-8，批次剩余不足一个字符时返回 READ_BUDGET_EXHAUSTED；旧 path 微小 max_bytes 仍允许补齐一个字符，
但不能超越主机/完整 JSON 预算。每个实际成功结果带全文件 hash；扫描不完整不能返回伪造的 hash。

批次 version=1，沿用原逐项结果，并兼容增加 budget_exhausted 状态。实际读取失败保留其他结果；内容预算耗尽的后续节点
保留身份并明确未读取。单文件/批次均在扫描后复核权限；撤权结果不得再把原文返回模型。

## workspace_search

参数：query（1..256 字符，UTF-8 最多 1024 字节）、mode=text/files（默认 text）、path（默认 .，绑定根内目录或文件）、
limit（默认 100、最大 200）、context_lines（默认 1、最大 3）。文本为区分大小写的 Unicode 字面匹配，每匹配行一条；
text 模式可用 queries 数组（1..8 个词，每词沿用 query 字符/字节上限）替代 query，两者互斥。match 默认 any（OR），all（AND）要求同一行含全部词；不解析 |、& 或正则。每行仅返回一次，matched_queries 为输入词数组的零基索引（旧 query 对应 [0]，旧记录可缺省）。多个词共用一次文件扫描与原有输出上限。files 模式只接受 query 与 match=any。
files 模式为区分大小写的 fnmatch 文件路径模式，含 / 时匹配相对路径，否则匹配文件名。不提供大小写/正则开关。
固定排除 node_modules、.venv、venv、__pycache__、.cache、dist、build、coverage、.next，首版不能通过模型参数扩大；
系统敏感拒绝集独立执行，链接及非普通文件跳过，显式指定敏感/被排除根仍拒绝。

搜索最多访问 10000 个目录条目、检查 500 个文本候选文件；路径集合的累计 UTF-8 字节最多 1 MiB；字节和时间预算同上。确定性路径/行号顺序，行片段最多 512 字符，
超长行只作有界处理并标未覆盖，不能冒充全范围无匹配。命中上限、输出预算、文件预算、错误分别说明。
结果 version=1、status=complete/partial、matches、issues（有界原因及作用域）、truncated、scanned_files/scanned_bytes、visited_entries。
text 命中含 path/line_number/text/context_before/context_after/sha256/version_confirmed；完整扫描并核验后才给全文件 hash。
files 命中不伪造行号或 hash（均为 null，version_confirmed=false）。搜索后按行读取时用已知 hash 约束，不拼接不同版本。
扫描到限制后停止，不自动续扫；partial 空结果只说明未发现已覆盖范围的匹配，恢复建议为缩小路径/查询。

## 详情、日志与兼容

公开卡片仅有原工具名、状态及白名单错误码；查询、路径、匹配正文/行号和版本只供模型及 Owner 加密详情。
Owner 搜索详情展示查询条件与范围，按文件归组命中；批量行读取标题显示实际范围，不改变调用身份或次数。
Owner 详情兼容新增 search/read_range/budget_error（仅安全预算数字与阶段），批次节点 result 可为旧字节结果或行结果；旧批次原字段继续兼容。
密文、过期、归属和失败降级沿用工具详情协议，不补读历史。不增加回合统计摘要。
原位连续 search/read/list 归入探索组，失败与未覆盖仍明确；角色工具设置按文件/命令/服务分组，不改变权限开关含义。
预检拒绝或排队未执行在详情明确“未开始读取/搜索”，不能把它混同为记录丢失。

日志在现有 Trace 下记录固定工具名、输入数量/长度和错误码，tool.scan_completed 记录排队/扫描耗时与实际扫描字节；结构化模型/Owner 结果可带扫描计量，正文与查询不进入机器日志。
无新表/索引。领域协议、旧记录、权限、竞态、基准与真实 Provider 结果在完成后同步 README 与测试指南。

数值依据与分层验收结果见[T2 测试记录](../../testing/tool-reliability-t2.md)。基准仅证明受控文件场景，不保证所有硬件/目录的时延。
