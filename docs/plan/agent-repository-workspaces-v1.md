# Roleplex Agent 仓库工作区、代码工具与 Worktree 实施计划 v1

| 元数据 | 值 |
|---|---|
| 受众 | Roleplex 架构、后端、前端、工具安全与测试维护者 |
| 状态 | W0、E0、W1a、W1b 已完成并经人工验收；下一阶段 W1c 尚未开始 |
| 计划版本 | 1 |
| 上游计划 | [上下文、Prompt Cache、群聊与 Orchestrator 实施计划 v1](context-cache-orchestration-v1.md) |
| 关联主计划 | [Roleplex 总体实施计划](nested-watching-crown.md) |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-09 |

本文定义 Orchestrator fan-out 之前必须具备的代码协作基础：持久 execution 身份、Owner 授权的本地 Git
仓库、受边界约束的文件/Git 工具、命令执行和每次写执行独占的 Git worktree。本文是这些能力的范围、
安全边界和验收权威；Orchestrator 的 planning、重试和汇总仍以
[上下文与编排计划](context-cache-orchestration-v1.md)第十节为权威。

## 一、调整原因与当前事实

当前 Agent 循环和工具领域事件已经存在，W1a 已把当前 World Workspace Binding 与原生文件工具接入单角色
链路；W1b 已增加固定结构化命令，MCP manager 仍只完成生命周期风险验证，仓库绑定与 Git worktree 尚未实现。此时先实现
M4b 仍只能并行生成有限文字/文件意见，不能形成“读取代码 →
修改 → 测试 → 比较 diff → 汇总”的真实协作闭环。

2026-09-01 经两轮评审后确认把实施顺序拆细为：

```text
W0 工作区与 Shell 设置契约（只定规则）
→ E0 持久 execution 身份
→ W1a 当前 World 工作区 + 原生文件读写
→ W1b 无任意 Shell 的结构化命令
→ W1c 任意 Shell + Owner 审批
→ W2a Repository Binding
→ W2b 只读文件与 Git 工具
→ W3 Git Worktree + 补丁写入
→ M4b Orchestrator fan-out/retry/final
```

W0 当前只固定设置、权限和运行契约；E0 提供命令审计与取消恢复的身份。W1a 先验证原生文件边界，W1b 再
验证无任意 Shell 的子进程，W1c 最后才允许 Owner 审批的任意 Shell；整个 W1 不接 Git、不创建 worktree。
三个切片都验收后才进入 Repository 和 worktree。
M4b 的 planning/final 与并行子任务在 W3 验收之后实现。不得把基础 Shell、仓库绑定、worktree 和 M4b
堆成一个故障时无法定位层级的大改动。

### W1b 实现与验证（2026-09-08）

已按本节计划实现 `workspace_run_command`、角色/工作区独立开关、固定 Python 隔离 worker、最小环境、
有界双流、退出码、超时/取消和进程树清理；复用现有表，无新增迁移。默认 30 秒/64 KiB、硬上限
300 秒/1 MiB 沿用 W0，通过主机配置调整。公开与内部字段统一见
[结构化命令协议](../protocol/internal/workspace-commands.md)，不在计划重复维护。

停止时等待进程回收后更新卡片/审计；若已经观察到命令退出结果，则先完成该事实的持久化，再停止生成，
避免漏记或同时记成功/取消。重启后的遗留卡片只标记执行中断，不猜测命令结果、不重放 Provider 或子进程。

验证覆盖：后端真实受控子进程、权限复核矩阵、启动交接取消、父进程先退出、输出截断、并行命令串行化、
停止/落库竞态及重启降级；普通浏览器回归、alpha/beta managed-world 命令与越界路径、独立 command E2E 的
输出/非零退出/超时/停止与刷新恢复。移除父进程路径校验、放开环境继承的两项内存变异均使对应安全测试变红。
SQLite 升降级、ORM metadata/外键检查和 PostgreSQL 离线 SQL 通过。

本轮后端全量回归 `121 passed, 3 skipped, 8 deselected`；普通 fake E2E 20 passed，fake managed-world 与
独立 command E2E 各 1 passed，前端 build 通过。命令 E2E 还校验九次工具开始/结束身份一一对应、
success/failed/timeout/rejected/cancelled 语义及输出原文不落日志；中文页面截图已检查。

当前 Linux 自动验证已通过；Windows Job Object 清理实现尚未在 Windows 实机验证，必须如实保留此覆盖缺口。
2026-09-08 用户补充确认：W1b 必须增加真实 Provider managed-world 验收；此前仅 fake 验证的结论不代表
该阶段验收完整。复用既有 `test:e2e:real-world` 与正常世界包装器，真实模型必须实际调用四种命令，读取
本轮文件中的未知校验值并根据真实结果回答；W1c 的 Shell smoke 不提前执行。
W1b 已于 2026-09-09 经用户人工验收，并获准独立提交；Windows 实机覆盖缺口继续保留，本轮不进入 W1c/W2a。

2026-09-09 已补验通过：`npm run test:e2e:real-world -- commands-provider.spec.ts`（1 passed），复用正常
世界包装器和原 real-world 配置，没有使用命令故障注入入口。DeepSeek V4 Flash 实际按顺序完成四种命令，
回答中的随机校验值、字节数和行数与本轮文件一致；四次工具完成事件共用同一 execution，工具卡已持久化。
5 次真实 Provider 调用报告 input/output/total=`5045/400/5445`、cache hit=`3584`；只记录厂商返回事实，
不将本轮数值视为性能门槛。日志脱敏断言通过，新用例关闭截图/trace/video，失败只保留安全阶段标签。
本轮记录：`logs/tests/e2e/real/2026-09-09/12-58-36_6850509d/summary.json`。

测试目录已按[测试指南](../testing/README.md#四数据账号与保留)分层；72 个旧目录完成非删除归整，保留的
普通测试库中 21 条工作区绑定已同步，execution 路径快照保留历史值。分层目录下的命令测试 14 passed，
前端 build 通过。

## 二、术语与边界

### 2.1 World、Workspace Binding、Repository 与 Execution Workspace

- **World**：现有物理数据目录、数据库和加密密钥边界。它继续承载用户、角色、会话和消息，不新增通用
  `workspace_id`，也不改变 ContextBuilder 的世界 scope。
- **Workspace Binding**：当前 World 的 Owner 在前端登记的一个主机绝对工作目录。列表和会话绑定保存在
  当前 World 数据库；一个 World 可以拥有多个根目录各不相同的 Workspace，切换 World 后必须重新读取。
  它不是跨 World 全局资源，也不受部署级目录白名单限制。
- **Repository Binding**：W2a 为某个 Workspace Binding 增加的 Git 身份与 base ref；不是另一套会话工作区。
- **Execution Workspace**：一次 Agent execution 对 Workspace Binding 的租用记录。W1 先直接使用已登记的
  `managed_directory`；W3 才增加该 Repository 的独立 `git_worktree`。两者不能回退到后端 cwd。

前端路由中的 `/workspace` 只是当前产品工作台名称，不自动获得 Repository 或 Execution Workspace 语义。
未来 Shared Memory 里的 Workspace/Project scope 也不能复用本文仓库表来偷渡未批准的 Memory 能力。

### 2.2 Worktree 不是安全沙箱

Git worktree 只解决并行修改、分支和 dirty 状态互相覆盖的问题。它不能阻止命令读取宿主机其他目录、访问
网络、启动后台进程或修改仓库外文件。因此：

- 文件工具必须在服务端逐次解析并验证路径；
- 命令必须使用独立权限和审批策略，不能因 cwd 在 worktree 中就标记为 safe；
- 没有容器、低权限系统账号或操作系统沙箱时，不得宣称任意 Bash 已被隔离；
- 首版保持单机、单进程、单 worker，不引入 Docker 作为使用前提。

### 2.3 目标模块边界

建议模块职责如下；具体文件名可以在编码时微调，但 subprocess、路径授权和业务调度不能重新混在
`services/chat.py`：

```text
backend/app/
├── executions/          # execution 创建、状态转换与重启降级
├── repositories/        # Repository Binding、Git 身份与会话绑定
├── workspaces/
│   ├── paths.py         # 相对路径解析、containment 与敏感路径规则
│   ├── git.py           # 固定 argv 的 Git 查询和 worktree 生命周期
│   ├── commands.py      # 环境、超时、输出上限、取消与进程树
│   └── tools.py         # LangChain 工具适配；不拥有授权决策
└── approvals/           # dangerous shell 审批状态机
```

服务端执行层统一组合“触发用户、角色配置、会话绑定、execution/workspace 状态和审批”；工具适配层不能
自行扩大权限。前端只通过 REST/WS 管理 binding、审批和 retained worktree；绝对根只向当前 World Owner
显示和提交，前端不直接读写宿主文件。

## 三、W0：工作区与 Shell 设置契约

W0 是当前计划确认阶段，只更新设计、配置契约和测试门槛，不创建数据库迁移或可调用工具。必须先固定：

| 设置 | 首版规则 |
|---|---|
| workspace root | 每个 Workspace 由当前 World Owner 在前端手动输入绝对目录；后端规范化并验证，不设部署级 allowed-root 白名单 |
| workspace kind | W1 只允许 `managed_directory`；`git_repository/git_worktree` 到 W2a/W3 才开放 |
| shell kind | 部署配置允许 `auto/bash/powershell`，后端解析实际可执行文件；Owner/模型不能保存任意 executable |
| approval mode | 任意 Shell 固定 `per_call`；不能由角色、Owner 身份、Orchestrator 或 Prompt 改成自动批准 |
| timeout | 默认 30 秒、Owner 可降级或提高，首版硬上限 300 秒 |
| output | stdout/stderr 各自保留顺序；合计默认 64 KiB、首版硬上限 1 MiB，超限明确标记截断 |
| environment | 系统最小白名单 + UTF-8；Provider Key、JWT、加密密钥和完整后端环境永不继承 |
| network | 首版不提供网络隔离；UI 明示审批不是 OS 沙箱，不能宣称“仅能访问工作区” |
| retention | Workspace Binding 的目录不随 execution 自动删除；只允许 Owner 显式清理准确的已登记目录 |

W0 同时冻结分层工具名：W1a 的 `workspace_list/read/write`、W1b 的 `workspace_run_command`、W1c 的
`workspace_run_shell`。每层只能在前一层人工验收后暴露；不能让一个“通用 executor”在 W1a 阶段暗中执行
后两层能力。Shell schema 只接受非空脚本文本，不接受 cwd、shell path、环境、身份或审批状态。服务端给调用
绑定 execution/workspace，并在审批前不创建子进程。Shell 文本属于 Owner-only 业务数据，只能进入审批记录
和实际 shell stdin，不能进入进程标题、日志、工具摘要、E2E summary 或测试失败文件。

部署配置拥有允许的 shell kind 和所有硬上限；Workspace 根由当前 World Owner 配置，角色配置只决定是否暴露工具，Owner 每次
审批只决定“这份 script 是否执行”，不能修改 cwd、环境、shell 或硬上限。前端能力接口可以显示解析后的
shell 类型和限制，但不返回可执行文件绝对路径。

`/home/chen/workspace/testworkspace` 只用于本项目开发和 E2E，目的是在验证 containment 失效时降低误改
Roleplex 源码的风险；它不是产品默认值或所有用户的工作区上界，只记录在测试配置与测试文档中。产品 Owner
可以登记多个彼此无关的绝对目录。测试仍禁止把 `/home/chen/workspace/Roleplex` 登记为工作区。

已按数据库测试目录风格创建第一组空工作区夹具：

```text
/home/chen/workspace/testworkspace/
├── roleplex-world-e2e-20260901182005/
│   ├── alpha/
│   └── beta/
└── roleplex-real-world-e2e-20260901182005/
    └── default/
```

它们不属于 Git 仓库，也不包含项目源码。后续测试每轮使用新 stamp 创建相同结构，World 名与测试工作区路径
一一对应；不能复用上一轮目录或把未带 stamp 的宽目录交给 Shell。

W0 经确认后，才允许开始 E0；后续若修改 workspace root、审批模式、环境继承或清理边界，必须先回到本文
更新验收，不得在实现中临时决定。

## 四、E0：持久 execution 身份

E0 使用[编排计划](context-cache-orchestration-v1.md)第 10.2 节定义的
`agent_executions` 表和 `0005_agent_executions` 迁移。虽然表最初由 M4b 提出，但从本次调整起它是所有
新 single/group_role/orchestrator/subagent generation 的统一执行身份，也是后续工作区、工具审批和命令
审计的外键来源。

E0 单独验收：

- 新 generation 与 execution 在同一短事务创建，`generation_id` 一对一且 execution ID 不再只存在于
  `queue_jobs.payload_json` 或日志；
- single/group_role 行为不变，只增加持久身份；
- 启动时把遗留 queued/running execution 降级为 interrupted，不恢复 Provider 调用；
- SQLite 升降级、外键检查和 PostgreSQL 离线 SQL 通过；
- E0 完成后先人工验收，不顺带开放任何文件或命令工具。

E0 已于 2026-09-02 完成实现并经用户人工验收：新增 `0005_agent_executions`，single/group_role 创建和
scheduler 生命周期已改为读取规范化 execution；queue payload 不再复制 execution/role/chain/kind。后端完整回归
`101 passed, 3 skipped, 8 deselected`，SQLite 升降级、ORM metadata、外键检查和 PostgreSQL 离线 SQL 均通过；
fake managed-world E2E 1 passed，world-switch 后 alpha 的 active execution 数为 0。
真实 DeepSeek managed-world E2E 2 passed，两个 group_role 和两个 single execution 全部 completed、active 为 0。
未创建 Workspace Binding，也未开放任何文件、命令或 Shell 工具。

## 五、W1a-W1c：单角色工作区、文件、命令与 Shell

### 5.1 当前 World 的工作区数据与目录生命周期

W1a 先新增 `workspace_bindings`、`execution_workspaces`，并给 conversations 增加可空的
`workspace_binding_id`，建议合并在迁移 `0006_world_workspaces` 中；此时不出现 Git 或审批表：

| `workspace_bindings` 字段 | 约束与含义 |
|---|---|
| `id` | 标准整数主键；只在当前 World 数据库内有意义 |
| `created_by` | 当前 World Owner；Owner 删除时级联 |
| `display_name` | Owner 设置的可读名称；同一 Owner 的 active 名称唯一 |
| `root_path` | Owner 从前端输入、经后端 canonicalize 的绝对工作目录；当前 World 内唯一，仅向 Owner 返回 |
| `workspace_kind` | W1 固定 `managed_directory` |
| `file_tools_enabled` | W1a 原生文件工具开关，默认 false |
| `basic_commands_enabled` | W1b 结构化命令开关，默认 false；W1b 完成前不可启用 |
| `shell_enabled` | W1c 任意 Shell 开关，默认 false；W1c 完成前不可启用 |
| `active` | 禁用后不能新绑定会话或创建 execution lease，历史记录保留 |
| `last_validated_at` | 后端最近一次确认规范绝对根仍存在且可用的时间 |
| `created_at/updated_at` | 生命周期时间 |

conversations 的 `workspace_binding_id` 使用 `ON DELETE SET NULL`，绑定/解绑使用 `expected_revision` 乐观锁。
一个工作区可以供多个会话选择，但 W1 的同一 workspace 同时只允许一个写入或命令 execution；进程内 workspace
锁负责排队，数据库 execution lease 负责状态与重启诊断，不能用锁替代权限复核。

| `execution_workspaces` 字段 | 约束与含义 |
|---|---|
| `id` | 标准整数主键 |
| `execution_id` | 非空、唯一，外键到 `agent_executions.execution_id` |
| `workspace_binding_id` | 非空，外键到当前 World 的 Workspace Binding |
| `workspace_kind` | W1 固定 `managed_directory`，表示直接租用已登记目录 |
| `root_path_snapshot` | execution 开始时捕获的规范绝对根；运行中更改绑定不能把 cwd 切到别处，也不得进入日志 |
| `status` | `creating/ready/retained/cleaned/failed` |
| `error_code` | 创建、命令或清理失败的稳定错误码 |
| `created_at/ended_at/cleaned_at` | 生命周期时间 |

Owner 可在前端设置中手动输入绝对路径登记现有目录，也可以请求创建一个新的空目录；后端 canonicalize
路径，创建时只在精确目标不存在且父目录存在时执行一次目录创建，不能覆盖已有目标。登记现有目录需要醒目确认其中内容
可能被外部模型读取或被批准的 Shell 修改。

进程重启时不恢复工具或 Provider 调用：运行中的 execution 变 interrupted，execution lease 标记 retained 供
Owner 检查，但 Workspace Binding 和物理目录继续存在，不猜测文件或命令操作是否完成。审批表到 W1c 才新增。

### 5.2 前端设置与当前 World 列表

现有设置中心已包含“大模型密钥 / 运行世界与存储 / 账号与安全”三个页签；W1a 在其中新增“工作区”页签，
继续复用当前深色、紧凑视觉体系，不引入新 UI 库或全局样式系统。工作区页的标志性交互是一条只读边界轨迹：

```text
当前 World → Workspace Binding → 绝对根目录
```

它让 Owner 在创建或批准 Shell 前始终看见命令属于哪个 World 和目录边界，不用依靠绝对路径猜测。

工作区页必须包含：

- 顶部显示当前 World；切换 World 后清空旧列表状态并重新请求，不能短暂展示前一 World 的工作区。
- 当前 World 可用工作区列表：显示名称、`managed_directory` 类型、available/unavailable/busy/disabled 状态、
  规范绝对根、文件/基础命令/Shell 三层能力状态、最近复核时间和会话绑定安全摘要；绝对根只向 Owner 显示。
- Owner-only 主操作“添加工作区”：输入显示名与绝对目录，可选择登记现有目录或创建空目录；提交前显示
  “文件内容可能发送给模型、获批 Shell 可以修改目录”的明确说明。
- 行操作包括复核、启用/禁用、当前已实现能力的开关和解除登记。W1a/W1b 不能提前显示可用的 Shell 开关；
  解除登记只删除数据库绑定并让会话 FK 置空，W1 不删除物理目录。
- Guest 不显示设置入口，也不能通过 API 枚举工作区、绝对路径、Shell 状态或绑定关系。
- 会话创建/设置只列出当前 World 中 active + available 的 Workspace Binding；W1 只允许 single 会话选择，
  群聊入口显示“后续阶段开放”而不发送预留字段。

列表需要 loading、空态和根目录复核失败状态；按钮使用真实 button 语义、可见焦点、完整
accessible name，状态不能只靠颜色。移动端保持单列信息层级，危险说明和 Owner 审批不能藏在 hover 中。

API 领域按资源拆分：Owner 管理 Workspace Binding；会话端只通过 revision 绑定已
登记 workspace。具体 JSON、错误码和删除语义在 W1 编码前写入公开 REST/WS 协议，不能从前端表单倒推契约。

### 5.3 W1a：原生目录、读取与写入

W1a 不启动任何子进程，先用服务端原生文件 API 证明 World 归属、Workspace Binding、路径 containment 和
写入并发语义正确。首批只暴露三个工具：

| 工具 | 输入 | 关键行为 |
|---|---|---|
| `workspace_list` | `path="."`、`after_name?`、`limit<=200` | 按 UTF-8 名称稳定排序；返回 name/type/size，symlink 只报告不跟随 |
| `workspace_read` | `path`、`offset_bytes>=0`、`max_bytes<=65536` | 只读 UTF-8 普通文件；返回 text/bytes/eof/next_offset/sha256，不拆坏字符 |
| `workspace_write` | `path`、`content`、`expected_sha256?` | 原子创建或替换文本文件；新建时 target 必须不存在，更新时 hash 必须完全匹配 |

`workspace_write` 首版不自动创建父目录，不提供 delete/move/chmod/symlink。内容编码后最大 1 MiB；更新使用
同目录临时文件、flush/fsync 和 atomic replace，失败保留旧文件并清理精确临时目标。创建新文件时
`expected_sha256=null` 并使用 exclusive create；目标已存在则返回 revision conflict，不能把省略 hash 当作
覆盖授权。

新建路径使用 exclusive create 语义，不能用会覆盖竞态目标的 replace；更新路径在 workspace 写锁内、最终
replace 前再次读取并核对 hash，两个携带同一旧 hash 的并发更新最多一个成功。宿主机其他进程不受本应用锁
约束，因此最终核对后仍被外部修改属于明确的本地文件系统限制；W3 worktree 才提供更强的协作隔离。

W1a 的共同路径规则立即生效，不能等 W2b：只接受 UTF-8 相对路径，拒绝绝对路径、空字节、`..`、盘符、
UNC、环境变量和 glob；每次操作都 canonicalize 最终父目录/目标并确认仍在 Workspace Binding 内。symlink、
junction 或 reparse point 可以被 list 标识，但 read/write 不跟随；`.git`、真实 `.env`、私钥/凭据常见路径和
Roleplex secret 目录进入系统拒绝集。路径组件、深度和列表条数都有固定上限，达到上限返回结构化错误或
`truncated=true`，不能悄悄漏数据。

文件正文、目录名和 write content 只进入当前工具输入/结果与模型上下文，不进入日志、工具审计摘要、测试
失败产物或 E2E summary。审计只保存 workspace/execution、操作、相对路径不可逆指纹、字节数、hash 是否匹配、
耗时和稳定错误码。崩溃残留临时文件必须同时匹配 W1a 固定前缀、workspace 与 execution 后才能清理。

三个工具都要求 single 会话、Owner 触发、active 角色显式启用、会话绑定 active/available workspace、
execution lease ready，并在每次操作前重新解析 Workspace 的规范绝对根。文件读写仍属于 dangerous：
Owner 对角色和 workspace 的显式启用是授权，W1a 不增加逐文件审批；Guest 和群聊均看不到工具。

W1a 的最小真实流程固定为：列根目录 → 新建 `hello.txt` → 读取并取得 hash → 携带 hash 更新 → 再读确认 →
拒绝无 hash 覆盖 → 拒绝 `../Roleplex/README.md`。完成后独立人工验收和提交，才能进入 W1b。

### 5.4 W1b：无任意 Shell 的结构化基础命令

W1b 不改数据库，新增 `workspace_run_command(command, args)`，只验证子进程 cwd、环境、输出、exit code、
timeout、取消和进程树。`command` 是服务端登记的稳定 ID，不是 executable/path；`args` 按该 command 的专用
schema 校验，不接受 `|`、`>`、`;`、换行、环境赋值、cwd、shell path 或任意 argv。

只有 single、Owner 触发、角色显式启用、Workspace Binding `basic_commands_enabled=true` 且 lease ready 时
才暴露该工具。涉及路径的 args 必须先经过 W1a 的同一 resolver 和敏感路径规则，再交给 adapter；command
adapter 不能自行解析用户路径。

首版命令 ID：

| command | 参数 | 用途 |
|---|---|---|
| `pwd` | 无 | 返回服务端绑定的实际 cwd，验证没有落入 Roleplex 源码 |
| `list` | 可选相对目录 | 用独立进程列出目录，结果有界；功能与原生 list 重叠是为了验证 process seam |
| `read` | 相对文件 | 用独立进程输出测试文本文件，不能读取敏感或根外路径 |
| `count` | 相对文件 | 返回字节/行数，用于验证参数映射和非文本结果 |

后端按操作系统把稳定 command ID 映射为自己拥有的固定 executable/argv 模板；模型看不到或选择不了实际
executable。POSIX 和 Windows 可以使用不同 adapter，但都必须通过 `create_subprocess_exec` 等无任意脚本
入口启动；不支持的 adapter 返回 `COMMAND_NOT_SUPPORTED`，不能偷偷回退到 `bash -c`/`cmd /c`。

W1b 的最小流程固定为：在 W1a 的 `hello.txt` 上运行 pwd/list/read/count → 验证非零 exit code 的受控测试
profile → 输出截断 → timeout → 停止 generation 并回收进程树。W1b 独立人工验收和提交后，才进入 W1c。

命令结果统一为：`status=exited/timed_out/cancelled`、可空 `exit_code`、有界 `stdout/stderr`、各流最终 seq、
bytes、truncated 和 duration。原始输出不出现在共享工具过程卡；W1b 后的[工具详情优化](tool-timeline-details-v1.md)
新增独立 Owner 受保护详情接口。共享卡片只展示 command ID、运行状态、
退出码、耗时和截断提示。

### 5.5 W1c：任意 Shell 与 Owner 审批

W1c 才新增 `tool_approval_requests`，迁移预留名顺延为 `0008_shell_approvals`，并开放
`workspace_run_shell(script)`。它只接受非空脚本文本，不接受 cwd、shell path、环境、身份或审批状态。

```text
tool_approval_requests
- id
- execution_id
- workspace_binding_id
- tool_call_id
- tool_name
- request_encrypted         # 当前 World 密钥加密的 Shell 业务数据，不进入日志
- request_digest
- status                    # pending/approved/rejected/expired
- requested_at/expires_at/resolved_at
- resolved_by
```

约束至少包括 `UNIQUE(execution_id, tool_call_id)`、`INDEX(status, expires_at)`；`resolved_by` 只允许当前 World
Owner。脚本只在生成 pending、Owner 展示和批准后执行三个受控边界解密，不能先写明文再异步加密。

一次调用的状态机固定为：

```text
模型请求 Shell
→ 服务端二次授权并持久化 pending（此时不得创建子进程）
→ WS/UI 向 Owner 展示完整脚本、World、workspace 和规范绝对根
→ Owner approve/reject，或 5 分钟 expired
→ approve 后启动唯一 shell 并有界返回 stdout/stderr/exit code
→ 工具结果回到同一 Agent 循环，角色基于真实结果收尾
```

审批绑定 execution、workspace、tool call、工具名和 request digest；脚本任何变化都必须重新批准。Guest、
Agent 和 Orchestrator 没有批准 API。拒绝/过期作为结构化工具结果返回模型，不伪装成异常，也不启动进程。
W1c 只在 W1a/W1b 已通过
的 single/Owner/active role/ready workspace 边界上增加 Shell，不重新实现路径或进程模块。

审批决策使用单条条件更新 `WHERE status='pending' AND expires_at>now`；只有第一个 approved transition 可以
唤醒等待中的工具任务。重复 approve/reject 返回已有终态，不重复创建子进程；过期与批准竞争由数据库结果
决定。前端刷新或 WS 重连通过 Owner-only pending 列表恢复审批卡，不从浏览器本地状态猜测。

### 5.6 W1b/W1c 共用的进程边界

- cwd 固定为 execution 捕获的 Workspace Binding，模型/API 不能传 cwd；W1 不挂载项目仓库、用户目录或源码。
- 使用最小环境白名单和 UTF-8；绝不继承 Provider Key、JWT、Cookie、数据库凭据、加密密钥或完整后端环境。
- stdout/stderr 分流、有界收集并各自带递增序号；不宣称还原两条管道的全局字节顺序。返回 exit code、
  duration、bytes、truncated，不能因截断把失败冒充成功。
- 有 wall timeout、取消和 finally 进程树清理；即使父进程正常退出，也检查并回收遗留子进程。Windows 保持
  Proactor，不设置 Selector policy。
- W1c 的 Bash 使用 `--noprofile --norc` stdin；PowerShell 使用 `NoProfile/NonInteractive` stdin；脚本不进入
  进程标题。Shell 仍可能访问绝对路径和网络，审批不是 OS 沙箱。
- 日志只记录 command ID 或 script digest、workspace/execution、审批、exit code、耗时、字节和截断状态；
  不记录路径原文、文件正文、脚本、原始输出、环境或绝对目录。

### 5.7 W1a/W1b/W1c 的 TDD 与验收门槛

每个切片严格 red → green → refactor：先从公开工具/API/WS 行为写失败测试并确认因“能力尚不存在”而红，
再写最小实现；不得先实现通用工具框架后补快照测试。三个切片各自保留 happy path 和关键权限/失败路径。

- **W1a Red**：当前 World 列表、路径逃逸、exclusive 新建、两个相同旧 hash 并发更新只有一个成功、外部改动
  后 revision 冲突、Guest/群聊不可见测试先失败；
  Green 后 fake managed-world 在 alpha/beta 各自工作区完成写读且互不可见；随后通过 W1a 专用真实 Provider
  managed-world 命令，在独立 default World 和对应外部工作区完成真实工具发现、调用、文件结果回读与回答。
- **W1b Red**：command allowlist、参数拒绝、cwd、最小环境、非零 exit、截断、timeout/取消/进程树先失败；
  Green 后不得出现任意 shell fallback。
- **W1c Red**：pending 前无进程、digest 绑定、reject/expire、修改后重批、重启不自动执行、Shell stdin/no-profile
  和停止传播先失败；Green 后 fake 浏览器完成完整审批闭环。

W1a 的真实 Provider managed-world 是本切片必过验收，不得以 fake 结果替代或推迟到 W1c。W1c 仍需使用独立
真实 Provider 命令显式验证一次“请求简单 Shell → Owner 审批 → 根据真实输出回答”；每次真实测试运行前
说明联网和费用，使用外部空测试目录并关闭 trace/video。W1a、W1b、W1c 各自人工验收和独立提交；W1c
通过前不得开始 Repository Binding、Git、补丁或 worktree。

W1a 实现验证（2026-09-02，2026-09-08 按 Owner 绝对根契约复核）：新增 `0006_world_workspaces`、Owner
工作区 REST、single 会话 revision 绑定、设置中心第四页签、execution lease 和 `workspace_list/read/write`。
后端全量回归 `107 passed, 3 skipped, 8 deselected`；迁移通过 SQLite 升降级、ORM metadata、外键完整性和 PostgreSQL 离线 SQL。普通浏览器
E2E `20 passed`；fake managed-world 在 alpha/beta 各自外部工作区完成五次工具调用、hash 更新和切换隔离；
真实 DeepSeek managed-world 专用 W1a smoke 通过，实际完成 list → write → read → write → read 与最终回答，
Provider 共 5 次调用，汇总 input/output/total/cache hit=`8811/954/9765/7168`，实际脱敏 base URL 为
`https://api.deepseek.com`。文件名、两版内容和宿主绝对路径未进入结构化日志。用户已于 2026-09-08
人工验收并提交 `c52775f`；后续变更不得顺带改变 W1a 的 Owner/Guest、路径 containment 或原子写语义。

## 六、W2a：Repository Binding

### 6.1 数据模型

新增与 Workspace Binding 一对一的 `repository_bindings`，并允许对应 `workspace_bindings.workspace_kind`
从 `managed_directory` 变为 `git_repository`。建议迁移为 `0009_repository_bindings`：

| 字段 | 约束与含义 |
|---|---|
| `id` | 标准整数主键 |
| `workspace_binding_id` | 非空、唯一，外键到当前 World 的 Workspace Binding；会话继续引用 workspace ID |
| `git_common_dir` | 规范 Git common dir，用于拒绝同仓库重复绑定和识别现有 worktree |
| `default_base_ref` | 首版默认 `HEAD`；执行时解析成不可变 commit 后再创建 worktree |
| `command_profiles_json` | Owner 明确登记的 test/build/lint argv 模板；W3 前只保存、不执行 |
| `created_at/updated_at` | 生命周期时间 |

约束至少包括 `UNIQUE(workspace_binding_id)` 和当前 World 内的 `UNIQUE(git_common_dir)`。实际 root path 每次
由 Workspace Binding 的 `root_path` 重新解析；未来 World 备份或分发到另一台机器时，根目录不存在即标记
unavailable，必须由接收方 Owner 显式重新配置，不得猜测或尝试其他路径，也不得
把外部仓库内容塞进 World 包。

会话继续只绑定一个 Workspace Binding，不新增第二个 repository FK。Owner 把已登记 workspace 复核为 Git
仓库后，它才获得 repository 能力；正在运行的 execution 继续使用其创建时捕获的 workspace/repository ID 和
base commit，不能被中途切换到另一目录或仓库。

### 6.2 注册与复核

Owner 把 Workspace Binding 启用为仓库时，服务端必须：

1. 只读取已登记 binding，不接受模型传入路径、`~`、环境变量、glob 或命令替换；
2. 重新 canonicalize `root_path`，确认目录存在、与登记根一致且是 Git worktree；
3. 使用无 shell 的 Git argv 读取 `--show-toplevel`、common dir、HEAD 和 worktree 状态；
4. 检测 bare repo、嵌套/重复绑定、Git 安全目录错误和当前 dirty 状态；
5. 只保存必要路径事实，日志仅记录 repository ID、路径不可逆指纹、状态和错误码。

dirty 仓库允许注册和只读查看，但 W3 写执行默认拒绝并返回稳定 `REPOSITORY_DIRTY`。首版不自动 commit、
stash、reset 或复制未提交改动；Owner 必须自行形成干净 base commit，避免 Agent 在不知情时漏掉或覆盖用户改动。

### 6.3 API 与界面

- Owner 在工作区设置中把现有 Workspace Binding 复核为仓库、查看 Git 状态或移除 repository 能力；Guest
  不能访问仓库路径、Git 元数据或命令配置。
- W2a 正式把 Workspace Binding 扩展到群聊；群聊/单聊都可选择当前 World 的 active Workspace Binding，
  绑定/更换/解绑继续使用会话 revision 乐观锁。W3 前群聊绑定只建立资源身份，不开放多个角色直接写同一
  managed directory；写协作必须等待独立 worktree。
- 所有单聊和群聊的标题区都显示“当前工作区”：未绑定时明确显示未绑定，已绑定时显示工作区名称、
  available/unavailable/busy/disabled 状态和当前已实现能力，不能要求用户从设置页或 ID 反推。
- 标题区为 Owner 提供更换/解绑入口；其他会话成员只能看到工作区显示名、状态和能力安全摘要，不能获得
  本机绝对路径。Owner 可以从标题区进入详情查看绝对根和 Repository 状态。
- 公开会话表示需要增加会话范围的安全 `workspace_summary`，或提供等价的成员可读摘要接口；不能让 Guest
  为显示名称而调用 Owner-only Workspace 列表，也不能把 `root_path` 复制进公开会话 payload。
- 获得 repository 能力的工作区行增加 Git/base/dirty 状态，不创建第二套选择器。
- 注册和绑定接口使用稳定错误码，不把 Git stderr 或宿主路径原样回显给 Guest。

## 七、W2b：只读文件与 Git 工具

### 7.1 首版工具集合

W2b 复用 W1a 已验收的 `workspace_list/read` 路径与输出契约，但绑定 Git Repository 时继续禁用直接
`workspace_write`，直到 W3 创建 worktree。W2b 新增 search 和 Git 只读能力，不依赖 MCP：

| 工具 | 行为 |
|---|---|
| `workspace_list` / `workspace_read` | 复用 W1a；工作目录改为绑定 Repository，schema 不变 |
| `workspace_search` | 使用服务端固定参数搜索文件名或文本，返回有界匹配 |
| `git_status` | 返回 porcelain 状态的结构化摘要 |
| `git_diff` | 返回指定路径范围的有界 diff；W2 主要用于人工已有改动检查 |

这些工具会把本机文件内容发送给角色绑定的模型厂商，因此默认仍是 dangerous。只有 Owner 为具体角色启用
工具、会话绑定 active repository 且本次触发主体通过执行层授权时才暴露；不能因工具“只读”就绕过数据
外发边界。Guest 默认不能触发，未来审批流可以按具体 execution 放开，但不得获得仓库路径。

### 7.2 路径与内容安全

- 工具只接受 UTF-8 相对路径；拒绝绝对路径、空字节、`..`、盘符、UNC 和未解析环境变量。
- 每次操作都对最终路径做 canonical containment 检查；符号链接、junction 或 reparse point 指向根外时拒绝。
- 通用文件工具禁止读取 `.git/`、真实 `.env`、私钥、凭据存储和 Roleplex secret 目录；Owner 可以扩展拒绝
  pattern，但不能通过模型参数缩小系统拒绝集。`.env.example` 等无凭据模板需使用精确规则单独允许。
- 首版拒绝二进制文件；单次读取最多 64 KiB、单文件最多 1 MiB，搜索最多 200 个匹配且总输出最多
  64 KiB。达到限制返回 `truncated=true`，不能静默当成完整结果。
- Git 工具固定 argv、禁用 pager/color/ext-diff 和外部 textconv，不加载模型提供的 Git 配置或 alias。

文件正文、diff 和搜索结果只能作为当前工具返回值进入模型上下文与会话工具流程；正式日志、E2E summary、
审计参数和测试失败文件只保存工具名、路径指纹、范围、大小、截断标记、耗时和稳定错误码。

## 八、W3：Git Worktree 与补丁写入

### 8.1 Execution Workspace 扩展

W3 通过迁移 `0010_git_execution_workspaces` 扩展 W1 已有的 `execution_workspaces`；不创建第二套 workspace
身份：

| 字段 | 约束与含义 |
|---|---|
| `repository_binding_id` | 外键到绑定仓库；禁用绑定不删除历史 workspace |
| `workspace_kind` | 在既有 `managed_directory` 外增加 `git_worktree` |
| `root_path_snapshot` | W3 对 worktree 捕获 Roleplex 管理目录下的规范绝对根；不进入日志，也不作为宽删除目标 |
| `base_commit` | 创建时解析的完整 commit ID |
| `branch_name` | 服务端生成的 `roleplex/<chain>/<execution>` 分支名；只用于本地工作树 |
| `status` | 在既有状态中增加 `dirty`；仍由同一 workspace 行记录生命周期 |

所有写 execution 必须先获得 ready worktree，再暴露写工具。worktree 统一建在 Roleplex 管理目录下，目标由
repository ID 与 execution ID 生成；不得使用用户字符串、环境变量或 glob 作为创建/清理路径。创建前再次
确认绑定仓库、base commit 和 clean 状态，并用仓库级异步锁串行执行 `git worktree add/list/remove/prune`；
该锁只保护 Git 管理操作，不充当数据库或 Agent 全局锁。

### 8.2 写工具与复用命令运行器

W3 只新增仓库写能力，并复用已经由 W1b/W1c 独立验收的结构化命令、Shell 审批和进程运行器：

- W1a `workspace_write`：只有 cwd 为 ready git_worktree 时重新开放；不能直接写 Repository Binding 根目录。
- `workspace_apply_patch`：只在 execution worktree 内按 expected file hash 应用结构化补丁；冲突返回稳定错误，
  不覆盖并发新内容。创建、修改和删除目标必须逐路径经过与 W2 相同的 containment/敏感路径检查。
- `workspace_run_command(command_id, args)`：沿用 W1b schema；W3 只把 Owner 预先保存并校验的仓库命令 profile
  注册为新的 command ID，不允许模型替换 executable、cwd、环境变量名或 profile argv。
- `workspace_run_shell(script)`：沿用 W1c 的 per-call Owner 审批，但 cwd 从绑定的 managed_directory 切为本 execution
  的 git_worktree；工具和审批 schema 不变，便于把故障定位为 workspace backend 而不是重新实现命令层。

首版不提供 `git add/commit/merge/rebase/reset/clean/push` 给 Agent，也不自动合并子任务。子 Agent 交付的是
保留在 worktree 中的变更、结构化 diff 和验证结果；Owner 人工检查后再决定如何整合。后续若增加提交或合并，
必须作为独立计划定义签名、作者身份、冲突、回滚和远端副作用。

### 8.3 清理与恢复

- execution 终结后检查 worktree 状态；dirty worktree 一律标记 retained，不自动删除。
- clean worktree 只有在其路径、repository ID、execution ID 和 Git worktree registry 全部匹配后，才允许使用
  `git worktree remove` 清理；禁止对宽目录运行递归删除。
- 取消、命令超时、后端崩溃或 Git 清理失败时保留目录并记录状态，下次启动只做 reconcile，不猜测清理成功。
- UI 提供 Owner-only 的 retained workspace 列表、diff 和显式清理入口；清理前再次检查 dirty，发生变化即拒绝。
- 未来容量/保留策略只处理 Roleplex 管理的 clean worktree；dirty 内容没有自动过期删除策略。

## 九、与 M4b Orchestrator 的衔接

W3 通过后，M4b 才能把一次 dispatch 绑定到 Repository Binding 和 Execution Workspace：

- Orchestrator planning 只能分派当前会话已经绑定的 repository，不接受模型传入任意路径或仓库 ID；
- 只读子任务可以使用独立 readonly workspace 记录，写任务必须各自创建 worktree；
- 每个子 execution 的工具集合根据角色配置、触发用户、repository 能力、workspace 状态和审批状态生成，
  任一变化都必须改变 `tool_policy_hash`；
- 子任务返回结构化状态、diff 摘要、验证命令结果和错误码，完整文件/命令输出不进入 Orchestrator 日志；
- 首版 Orchestrator 只汇总和比较多个 worktree，不自动 merge、commit 或 push；
- 停止父 execution 时取消子命令并回收进程树，dirty worktree 保留供 Owner 检查。

没有绑定 Repository 的普通单聊、M4a 群聊和未来 M4b 文本协作仍可工作，但不会暴露文件、Git、补丁或命令
工具。不能为“让模型尝试一下”而回退到后端进程工作目录。

## 十、协议、错误码与日志

每个切片编码前更新其权威协议，而不是在本计划复制完整 wire schema：

- W1a：当前 World Workspace Binding、会话绑定、原生 list/read/write 和 execution lease；
- W1b：稳定 command ID、参数 schema、子进程输出/超时/取消领域事件；
- W1c：Shell 工具、审批 API/WS 事件和重启过期状态；
- W2a：Repository 数据模型、Owner 仓库 API、会话绑定 API、`conversation_updated` WS 行为；
- W2b：内部文件/Git 工具协议、工具危险级别、公开工具过程卡的安全字段；
- W3：git worktree、补丁、命令 profile 和 retained/clean 清理状态；
- M4b：dispatch/final 和父子 execution 协议引用本文 workspace 身份。

新增错误码至少区分：workspace 不可用/busy、路径非法/越界/敏感、文件不存在/非文本/过大/revision 冲突、
command 不允许/平台不支持/超时、Shell 未批准/过期、repository 不可用/dirty、worktree 冲突和清理目标
不匹配。输出截断是结果字段，不伪装成错误码。错误码只以 `docs/protocol/error-codes.md` 为权威，不能从
Git stderr、异常类名或命令输出动态生成。

正式日志和审计遵守“允许字段优先”：绝对路径、文件正文、diff、命令/脚本、stdout/stderr、环境变量和 Git
远端 URL 不落日志。Git 远端 URL 可能含凭据，连脱敏前原文也不能先持久化。必要关联只使用 repository ID、
execution/workspace ID、相对路径指纹、base commit、分支安全标识、大小、耗时、状态和稳定错误码。

## 十一、分层测试与真实验收

### 11.1 后端与安全测试

- W1a/W1b/W1c 每轮只登记 `/home/chen/workspace/testworkspace` 下带 stamp 的空测试目录；W2a/W2b/W3 才增加临时 Git 仓库和 worktree，
  不读取真实用户文件或 Roleplex 源码；
- 覆盖绝对路径、`..`、符号链接/junction 逃逸、`.git`、敏感文件、二进制、大文件和输出截断；
- 覆盖 Owner/Guest、角色工具配置、会话 repository 绑定和执行时二次授权矩阵；
- 覆盖 dirty base 拒绝、两个 execution 并行 worktree 隔离、分支/路径碰撞、补丁 expected hash 冲突；
- 覆盖审批 digest、过期、修改后重批、重启不自动执行、命令超时/取消和子进程树回收；
- 覆盖 clean 清理、dirty retained、清理目标篡改拒绝和启动 reconcile；
- SQLite 迁移升降级与 PostgreSQL 离线 SQL 都必须通过。

安全变异验证至少包括：移除 canonical containment、允许继承完整环境、把 shell 标为 safe、跳过 dirty 检查或
对不匹配路径清理时，对应用例必须变红。

### 11.2 浏览器与人工验收

- W1a Playwright 覆盖设置页当前 World 列表、single 会话绑定和角色 list/read/write；W1b 再覆盖结构化命令的
  cwd/输出/超时/取消；W1c 最后覆盖 Shell pending/approve/reject/expire 和返回结果。三个切片均不创建 Git 仓库。
- W1a-W1c fake/real 浏览器测试分别使用带时间戳的独立测试 World 和独立 workspace 目录；World 路径从项目根
  解析，测试 workspace 绝对路径固定落在 `/home/chen/workspace/testworkspace`，但该目录不进入产品默认配置。
  禁止出现 `backend/..data`、workspace 落入项目目录或 canonical path 逃逸。测试账号、World 和 workspace
  使用同一 stamp 可追溯，最近若干轮保留供人工登录检查，更早轮次在下一轮开始时清理。
- W2a/W2b/W3 再使用测试创建的临时仓库，通过真实前后端完成 Owner 注册/绑定、读取、搜索、创建 worktree、应用
  补丁、运行已登记测试命令、查看 diff 和保留 dirty worktree。
- 关键失败路径分层覆盖：W1a 为 World/Guest/路径/hash 隔离，W1b 为 command/参数/进程边界，W1c 为审批与
  任意 Shell；W2a/W2b/W3 再覆盖宿主路径隐藏、敏感文件、dirty base 和 worktree 清理保护。
- 普通浏览器 E2E 使用 fake provider，不能让测试访问项目仓库或真实用户目录；
- W1a 同时要求 fake managed-world 和专用真实 Provider managed-world：fake 固定覆盖路径、权限、hash 并发与
  World 隔离，真实测试必须由模型实际调用 `workspace_list/read/write`，并从工具返回的真实文件内容形成回答。
  两层分别使用同 stamp 的独立 World 与外部 workspace，不能复用上一轮目录，也不能让测试接触 Roleplex 源码。
- W1b 必须同时完成 fake managed-world 和真实 Provider managed-world。真实用例复用原有 real-world
  测试入口、独立 World 和外部用例目录，使用正常产品命令 adapter，验证实际 pwd/list/read/count 调用、
  真实文件校验值、字节/行数、持久 execution/工具卡以及 usage/日志脱敏；不能用 command 专项故障注入替代。
  超时/取消/非零退出等确定性进程边界仍由 fake 专项测试覆盖。W1c 另用独立真实 Provider 命令验证一次简单 Shell 闭环。
  W2a/W2b/W3 的安全和 Git 生命周期以 fake/临时仓库为主。M4b 接入后再用 real-world 验证真实模型在临时
  仓库完成“读取 → 补丁 → 测试 → diff → 汇总”。所有
  真实测试运行前说明联网和费用，关闭 trace/video，凭据不进入浏览器或 workspace。

### 11.3 分阶段验收

- **W0**：设置、工具 schema、权限、日志和测试门槛完成文档确认，不产生产品提交。
- **E0**：持久 execution 身份和重启降级通过，人工验收后独立提交。
- **W1a（已完成，`c52775f`）**：当前 World 工作区列表、single 绑定、原生 list/read/write、原子写与 hash
  冲突通过；fake 与真实 Provider managed-world 均完成独立目录的工具闭环并经人工验收。
- **W1b**：结构化 command allowlist、cwd/环境/输出/超时/取消/进程树通过，且 fake 与真实 Provider
  managed-world 均完成命令闭环；人工验收后独立提交。
- **W1c**：Shell 审批、stdin/no-profile、拒绝/过期/重启和真实 Provider smoke 通过，独立提交。
- **W2a**：Owner 仓库注册/复核/绑定、群聊 Workspace Binding、所有聊天标题区的工作区摘要与 Owner
  更换/解绑入口、主机迁移 unavailable 和 dirty 提示通过，独立提交。
- **W2b**：只读文件/Git 工具及路径、敏感内容、Owner/Guest 边界通过，独立提交。
- **W3**：worktree 隔离、补丁、命令 profile、复用 Shell 运行器、retained/clean 清理通过，独立提交。
- **M4b**：最后验收并行代码协作，不把 W1a/W1b/W1c/W2a/W2b/W3 和 Orchestrator 堆成一个提交。

## 十二、明确不做

本计划当前不授权：

- 把 Git worktree 描述成容器或 OS 安全沙箱；
- 未绑定仓库时回退到 Roleplex 源码目录、后端 cwd、用户主目录或文件系统根；
- Agent 自动执行 git commit、merge、rebase、reset、clean、push 或修改远端；
- 自动 stash、提交或复制 Owner 的未提交改动作为 base；
- 自动删除 dirty、路径不匹配、Git registry 不一致或非 Roleplex 管理的目录；
- 把完整文件、diff、命令、输出、环境、远端 URL 或凭据写入日志和 E2E summary；
- 通过 filesystem MCP 绕过本文原生工具的 repository、路径、审批和审计边界；
- 为仓库工具引入通用 Project Memory、Embedding、索引服务或外部数据库依赖。

## 十三、已确认决策与编码前复核点

### 13.1 2026-09-01 已确认

1. W1 继续拆成 W1a 原生文件、W1b 结构化命令、W1c 任意 Shell；每层独立 red/green、人工验收和提交。
2. 任意 Bash/PowerShell 始终 dangerous 并需要 Owner 逐次审批；W1a/W1b 不提供任意 Shell，整个 W1 不接
   Git、不创建 worktree。
3. Git worktree 用于并行写隔离，不宣称是安全沙箱。
4. 首版不自动合并；Orchestrator 先汇总 diff、测试和冲突，Owner 决定整合。

### 13.2 编码前仍需按切片复核

1. W0/W1b/W1c：默认 30 秒/硬上限 300 秒、默认 64 KiB/硬上限 1 MiB，以及 Shell 审批 5 分钟过期。
2. W2a：主机本地绝对路径随 World 移动后标记 unavailable、由 Owner 重新绑定。
3. W2b：64 KiB 读取/结果、1 MiB 单文件、200 条搜索结果的首版上限及系统敏感路径集合。
4. W3：dirty base 一律禁止写执行；Agent 不获得 commit/merge/push，dirty worktree 永不自动删除。

用户确认本次拆分后，先完成 W0 文档门槛，再从 E0、W1a 逐阶段实施和人工验收；不得直接跳到
W1b/W1c、Repository、worktree 或 M4b。
