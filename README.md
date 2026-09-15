# Roleplex

Roleplex 是运行在 Owner 本机上的个人多 Agent 群聊协作服务：Owner 配置 Agent、模型 Key 和 MCP，Guest 通过邀请码加入指定会话。它不是多租户 SaaS；Guest 不可见 Owner 的配置，默认不能驱动有副作用的工具。

## 当前阶段

Shell 审批等待已与写锁分离；原生单项/批量写入和编辑支持有界排队，累计等待最多 30 秒（小阶段 3，已通过人工验收）。具体边界见[写入等待协议](docs/protocol/public/rest/workspaces.md#原生修改的有界等待)。

原生写入现支持自动创建授权工作区内缺失的父目录（含批量写入，已通过人工验收）；行为与失败边界见[工作区协议](docs/protocol/public/rest/workspaces.md#写入自动创建父目录)。

T2 人工验收补充：搜索支持多个字面关键词的 OR/同行 AND；Owner 结果按文件归组，批量行读取标题显示实际范围。见[搜索与范围读取协议](docs/protocol/internal/workspace-search-read.md)。

已完成 M0/M1 基础骨架、M2 单聊闭环、M4a 群聊、E0 持久 execution 身份和 W1a 当前 World 工作区与
单聊原生文件工具，以及已通过人工验收的 W1b 结构化命令：

- SQLite + SQLAlchemy 2 async，WAL、busy timeout、外键约束、单进程单 worker 边界
- Owner 原子初始化模型、JWT 7 天有效期与 token version 撤销
- 密码策略（至少 10 位且含字母、数字、符号）、弱口令登录后强制重置与改密接口
- API Key 加密落库，接口只返回 masked hint
- 用户认证、模型配置 CRUD、角色 CRUD、会话创建/列表/个人置顶归档
- 角色删除保留墓碑（历史消息仍显示原发送者），会话删除进回收站并可在 7 天内恢复
- 单聊消息发送、客户端幂等键、真实模型流式回复、停止生成；自动化测试使用确定性 fake provider
- 单聊通过统一 ContextBuilder 读取终态历史；角色上下文窗口默认 200K 并可由 Owner 配置
- 群聊支持角色成员管理、可访问的 @ 补全、无 @ 静默记录、mentions 串行回复、停止整条 chain 和工具过程卡片
- single/group_role generation 拥有一对一持久 execution；队列只保存唤醒参数，启动中断不重放 Provider
- Owner 可在前端为当前 World 登记多个绝对根各不相同的工作区，single execution 可按角色/工作区双开关使用
  `workspace_list/read/write`；路径、symlink、敏感文件、原子写和 hash 并发由服务端执行层约束
- 单聊可按角色/工作区独立开关使用 `workspace_run_command` 执行固定的 pwd/list/read/count；支持输出上限、
  超时、停止后的进程树回收，以及不包含原始输出的工具过程卡
- W1c Owner 单聊 Shell 已完成人工验收：每次展示完整脚本并单独批准，支持拒绝、过期、停止和刷新恢复；
  脚本加密保存，重启不自动执行。Shell 可访问宿主其他路径和网络，不是系统沙箱
  Owner 可展开 Shell 原审批脚本及有界输出，审批等待与执行耗时分开展示；旧调用未保存的输出不补录
- 新回复按实际顺序穿插文字与工具卡；Owner 可展开查看加密保存的有界输入/输出，Guest 只看摘要。
  详情保留 7 天，旧消息标注“位置未记录”；该优化已完成并通过人工验收
- 上下文稳定层、工具策略、历史裁剪和 Provider cache usage 可通过不含 Prompt 原文的结构化日志追溯
- WebSocket 首帧认证、按事件序号断线恢复，服务重启后重建当前历史窗口
- 登录会话持有 WebSocket，切换会话与组件重挂载复用连接；历史加载与订阅同步按真实响应分别显示。
  A 阶段连接解耦与 B 阶段最近内容按需加载、内存缓存及阅读位置恢复均已完成人工验收
- HTTP、后台生成与 WebSocket 共用关联 ID；终端可读日志与轮转 JSONL 日志统一输出
- 多世界物理存档、CLI 一致性备份与包装器热切换；每个世界独立数据库和密钥
- Alembic 迁移覆盖全部表结构，可在 SQLite 与 PostgreSQL 方言上重放
- React + TypeScript + Vite + Tailwind + zustand 的登录/工作台 UI 与实时聊天界面
- M 多行输入已实现并通过人工验收：Enter 发送、Shift+Enter 换行、按内容增高、光标位置 @ 补全；
  发送失败保留当前草稿，发送和模型输入保留代码缩进与换行。范围与验收见[输入与 diff 计划](docs/plan/code-diff-multiline-v1.md)；代码 diff 已实现并验收，详见下文 D/E 阶段

M0 风险验证已补齐（只做验证，未接入产品页面）：LangGraph 防腐层与统一领域事件、工具危险
分级与执行层拦截、工具调用审计、取消传播、Windows stdio MCP 生命周期与进程树清理、
产物原始内容的 iframe 隔离。结论记录在 `docs/protocol/internal/agent-runtime.md`。

W1b 已完成并通过人工验收；当前工具时间线与展开详情优化的范围和验收见
[实施计划](docs/plan/tool-timeline-details-v1.md)。下一项按[会话连接与按需加载计划](docs/plan/conversation-loading-v1.md)
A 连接解耦与 B 分页、缓存及滚动位置均已完成人工验收；单条超长消息分块延期。
W1c 任意 Shell、Owner 逐次审批及私有执行详情已完成人工验收；W1d Linux 首版与 M 多行输入已完成人工验收，
[代码 diff 阶段 D、E1 局部编辑与 E2 批量文件操作](docs/plan/code-diff-multiline-v1.md)已通过人工验收；继续 W2a 前先推进下述工具可靠性 T0。所有聊天标题区显示当前 Workspace 与 Owner
更换/解绑入口、群聊 Workspace Binding 和 Repository Binding 已纳入 W2a；富媒体产物、Orchestrator 与
MCP 产品接入仍在后续里程碑。

会话后台服务与 `/ps` 的 Linux 首版及故障/World 操作收尾已实现并通过人工验收，
进度以 [W1d 计划](docs/plan/conversation-services-v1.md)为准。Owner 可在世界设置中协调回收并下载当前世界备份；
备份包含密钥，不包含外部工作区。升级前请正常关闭旧后端，具体检查步骤见[服务验收指南](docs/testing/runtime-services.md)。
现有 Shell 仍是调用结束即清理的一次性命令；服务保活使用独立工具，不接管外部容器。Windows 暂不开放后台服务。
S1 服务发现已通过人工验收：角色启用原 `workspace_service_status` 后，不传 ID 可分页查询本会话尚未结束或待回收核查的服务，
传 ID 保持原指定查询。状态查询不占进程名额、不依赖可写工作区租用，Owner/角色/会话权限仍需满足。
S2 写入拒绝诊断已通过人工验收：原生 write/edit 可区分服务占用、停止中、回收未确认、范围清理及配置/绑定变化，
模型与 Owner 详情获得对应下一步；其他会话只给汇总，不泄露实例明细。写入限制未取消，S3 运行中编辑仍待单独评审，
见[服务发现计划](docs/plan/service-discovery-diagnostics-v1.md)。
服务运行时仍可申请 Owner 逐次审批的 Shell，用于检查、测试或其他明确批准的操作；它不是只读能力，仍受权限、配额与清理门槛约束。

G 共同执行规则补齐已实现并通过人工验收：增加工具调用后的会话成员撤销复核，以及服务宿主准备后、交付脚本前的权限复核；
仍保留通用脚本与独立工具开关，真实两轮开发流程已通过自动验收。G 验证及选型过程见
[G 验证记录](docs/testing/tool-execution-g.md)。

D 原生 write 差异已实现并通过人工验收：Owner 写入卡默认展开，在原位置直接显示文件名及增删行，进入可视范围后加载详情；刷新后仍读取该次写入的加密记录，
不从当前文件重建历史。差异超预算、排队/计算失败或取消会明确降级，不回滚已完成的写入；Shell/服务仍不采集文件 diff。
E1 局部编辑已实现并通过人工验收：在角色工具中显式开启 `workspace_edit`，工作区沿用“原生文件读写”开关；
读取全文件 hash 后可提交唯一旧片段及新片段，不再需要让模型重传整文件。编辑共用停服、版本冲突和私有 diff 规则，
大文件 diff 仍遵守 D 的预算。
E2 探索归组已通过人工验收：连续原生读取/列目录在原位置显示可折叠探索组，保留真实工具名、状态和详情入口，
不跨正文或写入归组，折叠摘要不隐藏失败。
E2 统一读取已通过人工验收：角色使用原 `workspace_read` 开关，通过 `items` 一次可读取最多 8 个文件，
Owner 展开工具卡可分别查看各文件结果、hash 与续读游标；部分失败不隐藏其他结果，整个 JSON 输出仍限 64 KiB。
旧 `path` 单文件参数和响应保持兼容，两种形式不能混传；不再提供独立批量读取工具开关。
E2 批量写入/编辑已通过人工验收：原 `workspace_write`／`workspace_edit` 支持 `items`，无需新工具开关，
写前预检全批、逐项提交，失败即停，不自动回滚或整批重试；Owner 可按文件折叠 diff，未确认结果明确提示先核查。
旧单文件参数和展示兼容，范围与验收见[实施计划](docs/plan/code-diff-multiline-v1.md)。

完整测试分层、命令、端口、数据留存、账号和日志排查见 [Roleplex 测试指南](docs/testing/README.md)。

近期工具可靠性修正及长期角色能力库路线见[完整方案](docs/plan/tool-reliability-capability-roadmap-v1.md)。
T0 已完成离线与真实 Provider 问题取证，见[T0 验证记录](docs/testing/tool-reliability-t0.md)。
T1 中断处理已实现并通过人工验收：保留工具执行证据并准确记录停止原因，图预算停止不再追加缺少事实的模型收尾；
不生成每轮统计摘要，不向历史注入摘要。原工具卡和 Owner 私有详情按原权限与期限保存，中断后恢复工作尚未实现。
验证与人工检查见[T1 验证记录](docs/testing/tool-reliability-t1.md)。T2 搜索定位、按行读取、实际预算与有界排队已实现并通过人工验收，见[T2 测试记录](docs/testing/tool-reliability-t2.md)。
多片段编辑与可配置执行预算尚未实现，
现行图上限、工具权限和停服规则不变。

T2 在角色“文件操作”中单独开启“搜索工作区文件”，沿用工作区原生文件开关；旧角色不会自动获得搜索。
支持先搜索文件/文本定位行号，再按行读取大文件的小范围。读取默认每次/每批分享 64 KiB 实际内容额度，
完整结果另有 64 KiB 上限；单文件扫描默认 16 MiB，write/edit 仍限 1 MiB。主机可通过
`WORKSPACE_READ_CONTENT_BYTES`、`WORKSPACE_SCAN_FILE_BYTES`、`WORKSPACE_SCAN_TOTAL_BYTES`、`WORKSPACE_SCAN_SECONDS`
调整范围内预算，配置边界见[读取契约](docs/protocol/internal/workspace-search-read.md)。

### 模型 provider 开关与契约测试

自动化测试固定使用确定性 fake provider：pytest 与 Playwright 都会显式设置
`AGENT_USE_FAKE_PROVIDER=true`，即使本地 `.env` 配了真实厂商也不会联网或产生费用。

正常启动默认使用角色绑定的真实模型配置，不需要额外设置 provider 开关。只有需要离线调试时，
才显式设置 `AGENT_USE_FAKE_PROVIDER=true`；不要把该值长期写进正常运行环境。

手动跑真实厂商时，先把凭据写进 `backend/.env`（已被 Git 忽略），再生成一份可用的模型配置与角色：

```powershell
cd backend
python scripts/seed_dev_provider.py
python scripts/run_world_server.py --world default --host 0.0.0.0 --port 8000
```

Workspace 根由当前 World Owner 在设置中心手动输入绝对路径；一个 World 可以登记多个彼此无关的目录，
不需要部署环境预先配置目录白名单。Owner 仍需为工作区和具体角色分别开启原生文件能力。文件内容可能
发送给角色绑定的模型厂商；当前文件与结构化命令工具只能访问会话绑定的 Workspace 根以内。
结构化命令需同时开启设置页“结构化命令”和角色“工作区结构化命令”；与文件工具开关独立。
W1c 需另行开启工作区“审批 Shell”和角色 Shell 工具，每次调用还须 Owner 明确批准。主机通过
`WORKSPACE_SHELL_KIND=auto|bash|powershell` 选择 Shell；auto 在 Linux 使用 Bash，Windows 使用 PowerShell。
当前 Linux 真实执行已验证；Windows 参数/Job 路径有代码及分层测试，原生 Windows 实机验收尚未覆盖。
其他 POSIX 平台暂不开放 Shell。当前仍不提供 Git 或 worktree。主机可通过 `WORKSPACE_COMMAND_TIMEOUT_SECONDS` 调整命令超时
（默认 30 秒，最大 300 秒），通过 `WORKSPACE_COMMAND_OUTPUT_BYTES` 调整合计输出保留量
（默认 64 KiB，最大 1 MiB）；模型不能覆盖这些限制。

脚本会把凭据按产品同一条路径加密落库，并创建/更新开发用的模型配置与角色；不打印任何 Key。
界面里选中该角色即可与真实模型对话。

真实厂商的流式、工具调用、取消和错误格式验证放在独立的契约测试层，默认从普通回归中排除，
需要时显式运行：

```powershell
cd backend
pytest tests/contract -m contract -q
```

凭据从环境变量读取，也可以写进 `backend/.env`：`ROLEPLEX_CONTRACT_OPENAI_KEY`、
`ROLEPLEX_CONTRACT_OPENAI_MODEL`、`ROLEPLEX_CONTRACT_OPENAI_BASE_URL`（OpenAI 兼容厂商），
以及 `ROLEPLEX_CONTRACT_ANTHROPIC_KEY`、`ROLEPLEX_CONTRACT_ANTHROPIC_MODEL`。

### 真实 API 浏览器测试

普通 `npm run test:e2e` 始终使用 fake provider。需要验证“真实前端 → 真实后端 → 真实厂商 →
流式回复落库”的完整链路时，在确认 `backend/.env` 已配置 OpenAI-compatible 的 Key、模型名和
Base URL 后显式运行：

```bash
cd frontend
npm run test:e2e:real
```

该命令会联网并产生少量模型费用，默认不属于普通回归或 CI。真实 Key 只在后端播种阶段进入
产品加密边界，不传给浏览器；真实测试关闭 trace/video。每轮使用并保留独立数据库
`data/roleplex-real-e2e-<时间戳>.db`，最近 5 轮之外的旧库在下一轮结束时清理。

上述命令有意使用显式数据库兼容模式，便于把 Provider/Context 问题与世界包装器问题分开定位。需要验证
“正常世界包装器 → 世界独立双密钥 → 真实前后端 → 真实 Provider”的完整产品路径时，显式运行：

```bash
cd frontend
npm run test:e2e:real-world
```

它会创建 `data/roleplex-real-world-e2e-<时间戳>/default/`，包含完整世界元数据、数据库、JWT/API Key
双密钥和 files 目录，并在 `/home/chen/workspace/testworkspace/roleplex-real-world-e2e-<时间戳>/default/`
创建独立外部工作区，最近 5 轮一并保留。该命令同样联网计费、关闭 trace/video，执行 C2 两轮真实单聊、
M4a 两角色真实串行群聊、W1a 真实文件工具和 W1b 真实结构化命令闭环，但不测试世界切换。世界切换由 fake Provider 的
`test:e2e:worlds` 确定性覆盖：它会在 alpha/beta 各自外部工作区完成 W1a/W1b，并验证 C2/M4a 与切换隔离。

真实 E2E 的 Owner 为 `realtest<时间戳>`，密码固定为 `Roleplex-Real-E2E-1`。数据库包含加密后的
真实 Key，只能用于本机核对，不要分享或提交；离开当前实例密钥后其中的模型配置无法解密。

### 测试数据库与测试账号

每轮测试使用带时间戳的独立数据库，跑完保留最近 5 轮，更早的在下一轮开始时自动清理：

- 后端：`data/roleplex-test-<时间戳>.db`（pytest 结束时会打印本轮路径）
- 端到端：`data/roleplex-e2e-<时间戳>.db`
- 真实 API 端到端：`data/roleplex-real-e2e-<时间戳>.db`（仅显式运行 `test:e2e:real` 时产生）
- 真实 API 世界端到端：`data/roleplex-real-world-e2e-<时间戳>/default/`（仅运行 `test:e2e:real-world`）

测试账号与本轮数据库同名可追溯：Owner 为 `test<时间戳>`，Guest 为 `test<时间戳>_<用途>`，
密码统一是 `Roleplex-Test-1234`。想查看某轮测试产生的数据，可以让后端显式连接那一个数据库：

WSL / Linux（Bash）：

```bash
cd backend
export DATABASE_URL="sqlite+aiosqlite:///../data/roleplex-test-<时间戳>.db"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`export` 不能省略：只执行 `DATABASE_URL="..."` 会得到一个未导出的 shell 变量，随后启动的
`uvicorn` 子进程看不到它并会连接默认数据库。也可以把赋值与启动写在同一条命令中：
`DATABASE_URL="..." uvicorn app.main:app --host 0.0.0.0 --port 8000`。
Bash 赋值时变量名前不加 `$`，且 `=` 两侧不能有空格。

Windows PowerShell：

```powershell
cd backend
$env:DATABASE_URL = "sqlite+aiosqlite:///../data/roleplex-test-<时间戳>.db"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

这些库只用于本地排查，密码是无实际价值的固定占位值，不要把该口令用于任何真实环境。
这里直接运行 Uvicorn 是有意的：显式 `DATABASE_URL` 会进入单库兼容模式，只用于查看该轮测试结果，
世界切换控件会禁用。正常开发和产品运行统一使用下文的世界包装器。

### 世界存档与切换

正常运行默认使用 `worlds/default/`，其中包含数据库、JWT 密钥、模型 Key 加密密钥和附件目录。
每个世界都是可以单独备份、移动的物理目录。首次启用前，如果已有旧的 `data/roleplex.db`，先在
后端停止状态下接管；接管使用一致性复制并保留原目录作为回退：

```bash
cd backend
python scripts/manage_worlds.py adopt default
```

常用命令：

```bash
python scripts/manage_worlds.py list
python scripts/manage_worlds.py create another-world
python scripts/manage_worlds.py backup default --output ../backups
python scripts/manage_worlds.py delete another-world --yes
```

备份 ZIP 包含可解密模型 Key 的世界密钥，必须按敏感数据保管。删除命令不可恢复，拒绝删除当前世界，
且必须显式传入 `--yes`。

要在界面中切换世界，后端必须通过包装器启动；直接运行 Uvicorn 时仍可使用当前世界，但切换控件禁用：

```bash
cd backend
python scripts/run_world_server.py --world default --host 0.0.0.0 --port 8000
```

前端仍按原方式另开终端运行。切换时包装器会重启后端；不同世界的 JWT 密钥彼此独立，因此前端会
清除旧 Token 并要求重新登录。`ROLEPLEX_WORLD` 可指定启动世界，`WORLDS_DIR` 可覆盖世界根目录。
显式 `DATABASE_URL` 的优先级最高，会进入兼容模式，pytest、普通 E2E 和人工检查测试库的命令不变。

普通 pytest、普通 E2E 和真实 API smoke 继续使用显式的独立数据库，避免短生命周期测试数据出现在正式
世界列表中。`test:e2e:worlds` 与 `test:e2e:real-world` 才创建物理世界，且都放在带时间戳的 `data/`
隔离目录，不写入正式 `worlds/`。其中世界切换测试创建 alpha/beta，并把最近五轮保存在
`data/roleplex-world-e2e-*/`。
要人工查看其中一轮，在 `backend` 目录把包装器的世界根目录指向该轮目录即可：

```bash
python scripts/run_world_server.py \
  --world alpha \
  --worlds-dir ../data/roleplex-world-e2e-<时间戳> \
  --host 0.0.0.0 --port 8000
```

启动后界面可在 alpha/beta 间切换。两个世界的测试 Owner 都是 `test<时间戳>`，密码为
`Roleplex-Test-1234`。这里的 `../data` 是相对于 `backend` 的仓库数据目录；写成 `data/...` 会错误地
指向 `backend/data/...`。

旧世界由新软件打开时自动执行 Alembic 向前迁移；世界的迁移 revision 或格式版本高于当前软件时，
启动会只读阻断并提示升级，不会 stamp、降级或修改世界数据。接口契约见
`docs/protocol/public/rest/worlds.md`。

### 日志与排障

后端终端输出便于阅读的单行日志，机器日志按运行/测试职责保存：

```text
logs/
├── runtime/YYYY-MM-DD/
│   ├── app.jsonl
│   ├── agent.jsonl
│   ├── access.jsonl
│   └── errors.jsonl
├── tests/unit/YYYY-MM-DD/
│   ├── summary.jsonl
│   └── failures/
├── tests/e2e/{fake|real}/YYYY-MM-DD/HH-MM-SS_<run-id>/
│   ├── events.jsonl
│   ├── errors.jsonl
│   ├── summary.json
│   └── artifacts.json
└── archive/YYYY-MM/*.tar.gz
```

runtime 同日重启继续追加 active 文件，用进程实例和序号区分；单文件达到 10 MiB、一小时或跨日时，
关闭为递增且不可再写的 `.001/.002/...` 片段。pytest 全绿只向 summary 追加一行，失败才保存脱敏详情；
fake/real E2E 每轮独立，世界切换重启仍保持同一 run ID。

当前自然月的每日日志保持原始目录可直接查看；进入新月份后，runtime 启动才归档上月及更早日志为 tar.gz
（gzip level 6）。日志保留 30 天，整个日志树目标上限 1 GiB，超限时从最旧正式归档开始淘汰。归档经过
manifest、路径、成员类型、大小和 SHA-256 校验，删除前后都有结构化审计；当前月、活跃、running 和未迁移
旧日志不会自动删除。JSONL `timestamp` 固定使用北京时间 `+08:00`。
可通过 `LOG_DIR`、`LOG_LEVEL`、`LOG_MAX_BYTES`、`LOG_MAX_SECONDS` 调整输出与轮转；归档开关和边界为
`LOG_ARCHIVE_ENABLED`、`LOG_RETENTION_DAYS`、`LOG_MAX_TOTAL_BYTES`、`LOG_ARCHIVE_COMPRESSLEVEL`。

每个 HTTP 响应都带 `X-Request-ID`；错误信封中的 `request_id` 与它相同。前端报错时可用该值
串起访问、认证/消息业务事件、后台 `generation.*` 和最终状态。WebSocket 使用
独立的 `ws_connection_id`，并在认证、订阅（含 `conversation_id`）、恢复方式和断开时记录生命周期。
模型调用日志会记录厂商类型、模型、脱敏后的实际 `base_url` 及其 configured/default/fake 来源，并包含
首分片耗时、调用总耗时、输入/输出/总 token 和缓存命中 token；厂商未提供的数据省略，不进行估算。
例如：

```bash
# 查看一次请求的完整链路
rg 'req-login-failed' logs/runtime/$(date +%Y-%m-%d)/{app,agent,access,errors}*.jsonl

# 只看生成任务的开始、结束或失败
rg '"event":"generation\.' logs/runtime/$(date +%Y-%m-%d)/agent*.jsonl
```

登录失败审计会记录提交的用户名和失败原因，便于人工验证；密码、Token、Authorization、API Key、
完整用户输入和完整模型输出不会写入持久化日志。JSONL 字段和事件含义见
`docs/protocol/internal/observability.md`。

## Windows 开发

本项目当前验证使用的环境是：

- **Conda 环境**：`roleplex`
- **Python**：3.12.13
- **Node.js**：22.22.2
- **npm**：10.9.7

后端必须保持 Windows 默认 Proactor 事件循环，**不要设置 `WindowsSelectorEventLoopPolicy`**，否则 stdio MCP 子进程不可用。

后端终端：

```powershell
conda activate roleplex
cd backend
python --version
pip install -r requirements.txt
python scripts/run_world_server.py --world default --host 0.0.0.0 --port 8000
```

如果本机还没有 `roleplex` Conda 环境，可以先创建与当前验证环境一致的 Python 3.12 环境：

```powershell
conda create -n roleplex python=3.12
conda activate roleplex
```

另开终端启动前端（Node.js 不需要安装到 Conda 环境中）：

```powershell
cd frontend
npm install
npm run dev
```

开发服务器和 `npm run preview` 默认将浏览器的同源 `/api` 请求代理到
`http://127.0.0.1:8000`；后端端口不同时可在启动前设置 `VITE_PROXY_TARGET`。部署纯静态
`dist/` 时应由同源网关转发 `/api`，或在构建时通过 `VITE_API_URL` 写入可公开访问的后端地址。

每个世界首次注册的账号是该世界 Owner。正常运行数据写入 `worlds/<世界名>/`；显式测试数据库仍写入
`data/`。API Key 使用所在世界的实例密钥加密，世界目录和密钥文件都不应提交到 Git。

## 数据库迁移边界

业务代码只使用 SQLAlchemy 通用类型和 ORM。正常数据库地址由当前世界目录推导，显式
`DATABASE_URL` 仅用于测试与兼容模式。SQLite 适合 P1 单进程开发；需要多 worker 或常态化多人并发时切 PostgreSQL。迁移前运行测试并使用 Alembic 重放 schema，再导入存量数据。

表结构只由 Alembic 迁移创建：后端启动时自动执行 `alembic upgrade head`，因此新世界会记录迁移版本，
旧世界会自动向前升级。开发阶段如果某个显式测试数据库早于迁移体系创建（没有 `alembic_version`），
可删除该测试数据库重跑；世界数据不得用删除重建代替正常迁移。

修改表结构后在 `backend` 目录运行迁移检查：

```powershell
python scripts/check_migrations.py
```

它在临时 SQLite 库上重放 `upgrade head`/`downgrade base`、比对迁移结果与 ORM 模型是否一致，并按 PostgreSQL 方言离线渲染 SQL（不需要本机安装 PostgreSQL 驱动）。
