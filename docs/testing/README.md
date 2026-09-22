# Roleplex 测试指南

| 元数据 | 值 |
|---|---|
| 受众 | 开发者、测试维护者和本机人工验收人员 |
| 状态 | 已实现测试体系的使用指南 |
| 维护者 | Roleplex |
| 事实来源 | `backend/tests/`、`frontend/tests/`、Playwright 配置、pytest 配置与日志 v2 |
| 复核日期 | 2026-09-16 |

本文是 Roleplex 测试分层、命令、端口、数据、账号、日志和人工排查方式的统一入口。接口断言仍以对应
协议文档为权威，日志字段以 [日志 v2](../design/logging-v2.md) 为权威；本文不复制完整 wire schema。

## 一、快速选择

| 层级 | 命令 | Provider | 存储模式 | 联网/计费 | 主要证明 |
|---|---|---|---|---|---|
| 后端 pytest | `pytest -q` | fake | 每轮独立 SQLite DB | 否 | 服务、权限、状态机、迁移相关业务行为 |
| Provider contract | `pytest tests/contract -m contract -q` | real | 不走产品会话 DB | 是 | 厂商流式、工具、取消、usage 和错误格式 |
| 普通浏览器 E2E | `npm run test:e2e` | fake | 每轮独立 SQLite DB | 否 | 单聊、M4a 群聊与其他浏览器用户流程 |
| 世界切换 E2E | `npm run test:e2e:worlds` | fake | 临时 alpha/beta 及界面新建世界 + 外部工作区 | 否 | 界面创建、W1a/C2/M4a、包装器重启、世界与工作区隔离 |
| 工作区/工具 E2E | `npm run test:e2e:commands` | fake | 临时 default 世界 + 外部工作区 | 否 | 文件/搜索/编辑、审批、后台服务、预算及相关界面 |
| 真实 Provider E2E | `npm run test:e2e:real` | real | 每轮独立 SQLite DB | 是 | 真实浏览器到 Provider，隔离世界基础设施干扰 |
| 真实世界 E2E | `npm run test:e2e:real-world` | real | 临时 default 世界 + 外部工作区 | 是 | W1a 文件、W1b 命令、C2、M4a 与正常世界全链路 |

按本次改动选择足以证明行为的层级；纯文档核对链接、命令和实现，低风险展示调整可复用现有相关测试。常用基础离线回归为：

```bash
cd backend
pytest -q

cd ../frontend
npm run build
npm run test:e2e
```

工作区/工具与 World 流程分别使用 `test:e2e:commands` 和 `test:e2e:worlds`，不包含在普通 `test:e2e` 中；按受影响流程补充。

涉及迁移时额外执行：

```bash
cd backend
python scripts/check_migrations.py
```

真实命令使用独立配置，不属于普通 CI 或默认回归。用户明确要求真实验证，或本次采用的验收范围包含它时，Agent 在说明联网计费后执行，无需再要求用户亲自运行。旧计划和历史验收记录本身不触发新的收费测试；缺凭据、环境受限或未覆盖的分支如实记录。

浏览器测试前安装前后端依赖，在 `frontend/` 执行 `npx playwright install chromium`。启动测试的终端中，`python` 需指向已安装后端依赖的环境，Conda 或 venv 均可。

## 二、测试层级与边界

常用定向入口如下。后端命令在 `backend/` 执行；表中浏览器文件名在 `frontend/tests/` 对应目录，按所列 npm 脚本加 `-- <文件名>` 运行，不表示每项改动都运行整表。

| 改动范围 | 后端主要入口 | 浏览器主要入口 |
|---|---|---|
| 认证/资源边界 | `test_owner_bootstrap.py`、`test_password_policy.py`、`test_delete_semantics.py` | `test:e2e -- password-policy.spec.ts recycle-and-tombstone.spec.ts` |
| 消息/群聊/上下文 | `test_chat_flow.py`、`test_context_builder.py`、`test_group_chat.py` | `test:e2e -- m2-chat.spec.ts m4-group-chat.spec.ts multiline-composer.spec.ts` |
| 连接/历史窗口 | `test_ws_session.py`、`test_ws_recovery.py`、`test_history_window.py` | `test:e2e -- connection-session.spec.ts history-window.spec.ts` |
| 工作流编排/节点控制 | `test_workflows.py`、`test_context_builder.py`、`test_interruption_context.py` | `test:e2e:commands -- workflow-canvas.spec.ts workflow-reactflow.spec.ts workflow-node-editing.spec.ts`；真实验收 `test:e2e:real-world -- workflow-provider.spec.ts` |
| 会话工作区/群聊文件 | `test_group_workspaces.py`、`test_workspaces.py`、`test_group_chat.py` | `test:e2e:commands -- group-workspace.spec.ts` |
| 搜索/读取 | `test_workspace_search_read.py`、`test_workspace_read_many.py` | `test:e2e:commands -- search-read.spec.ts read-many.spec.ts exploration.spec.ts` |
| 写入/编辑/批次/diff | `test_workspaces.py`、`test_workspace_edit.py`、`test_workspace_replacements.py`、`test_workspace_batch_mutation.py`、`test_write_diff.py` | `test:e2e:commands -- workspace-edit.spec.ts replacements.spec.ts batch-mutation.spec.ts batch-unlimited.spec.ts write-diff.spec.ts` |
| 写入等待/审批 | `test_write_admission.py`、`test_shell_approvals.py`、`test_shell_details.py` | `test:e2e:commands -- write-wait.spec.ts shell-approvals.spec.ts` |
| 服务/诊断/World | [服务专项](runtime-services.md)、`test_service_discovery.py`、`test_write_diagnostics.py` | `test:e2e:commands -- runtime-services.spec.ts service-discovery.spec.ts write-diagnostics.spec.ts`；World 操作使用 `test:e2e:worlds` |
| 决策预算/用量 | `test_agent_decision_budget.py`、`test_workflow_budget.py`、`test_execution_usage.py` | `test:e2e:commands -- agent-budget-settings.spec.ts role-usage.spec.ts` |
| 参数拒绝/中断事实 | `test_tool_argument_recovery.py`、`test_interruption_context.py` | `test:e2e:commands -- argument-recovery.spec.ts interruption-context.spec.ts` |
| 侧栏/角色/会话详情 | 按受影响 API 选择 | `test:e2e -- sidebar-tabs.spec.ts role-output-settings.spec.ts conversation-details.spec.ts` |

并发、取消、未知副作用、权限撤销和故障恢复以确定性测试为主；真实模型成功不能代替这些边界。新增或实质改变用户操作流程时覆盖成功及关键失败/权限路径。历史测试结果入口见文末。

### 2.1 后端 pytest

W1d 的故障矩阵、World 协调操作、专项命令和人工步骤统一见
[后台服务收尾验证指南](runtime-services.md)。测试复用现有 World/工作区轮次布局；真实 npm 固件只使用 Node 内置模块，不安装依赖。

工作目录：`backend/`

```bash
pytest -q
```

普通 pytest 包含当前仓库的后端单元和集成测试：

- httpx/ASGI API 测试；
- Owner/Guest 权限与资源隐藏；
- 消息、生成、ContextBuilder 和 WebSocket 恢复；
- Agent 防腐层、工具权限和 MCP 生命周期；
- 日志、归档、测试 reporter；
- 世界目录、迁移兼容与并发风险测试。

`backend/pytest.ini` 默认排除 `contract` marker，即使开发机 `.env` 已有真实 Key，普通 pytest 也不会
悄悄联网或计费。测试强制 `AGENT_USE_FAKE_PROVIDER=true`。

这里的日志目录名称使用 `unit`，但实际包含单元和后端集成测试；它不表示所有用例都是纯函数测试。

### 2.2 Provider 契约测试

工作目录：`backend/`

```bash
pytest tests/contract -m contract -q
```

凭据来自 shell 环境或被 Git 忽略的 `backend/.env`：

```text
ROLEPLEX_CONTRACT_OPENAI_KEY
ROLEPLEX_CONTRACT_OPENAI_MODEL
ROLEPLEX_CONTRACT_OPENAI_BASE_URL

ROLEPLEX_CONTRACT_ANTHROPIC_KEY
ROLEPLEX_CONTRACT_ANTHROPIC_MODEL
```

未配置某厂商时对应参数组 skip。契约测试直接经产品 Provider 工厂构造模型，证明厂商 API 行为，不证明
前端、产品数据库或世界包装器。

### 2.3 普通浏览器 E2E

工作目录：`frontend/`

```bash
npm run test:e2e
```

Playwright 自动启动：

- Vite 前端；
- 真实 FastAPI 后端；
- 独立测试数据库；
- 确定性 fake Provider；
- Chromium，单 worker 串行。

它验证真实 HTTP、WebSocket、React 状态和用户操作，但不证明真实厂商输出。整轮 E2E 共用一个数据库，
所以测试不得依赖全局数量或执行顺序；应断言目标对象存在/不存在。

### 2.4 世界切换 E2E

```bash
npm run test:e2e:worlds
```

创建：

```text
data/roleplex-world-e2e-<时间戳>/
├── alpha/
├── beta/
└── 新世界-<时间戳>/
```

后端通过正常 `run_world_server.py` 包装器启动。测试验证 alpha → beta 重启、Token 失效、物理数据库与
双密钥隔离，以及切换后重新注册 Owner。切换前先在 alpha 中完成 C2 两轮 fake 单聊和 M4a 两角色串行
群聊，并直接核对 JSONL 的稳定层、history、chain/execution 与 fake usage。该测试因此同时证明 C2/M4a
在正常世界包装器中的确定性路径，不产生模型费用。

`world-creation.spec.ts` 验证界面创建、非法名称、重名、取消切换、新世界首次 Owner 注册及空配置，结束时切回来源世界。
后端 `tests/test_world_creation.py` 覆盖创建权限、兼容模式、同名并发、切换互斥和磁盘失败；普通 E2E 的 `worlds.spec.ts` 覆盖 Guest 隐藏入口与接口拒绝。

### 2.5 真实 Provider E2E（兼容数据库）

```bash
npm run test:e2e:real
```

存储：

```text
data/roleplex-real-e2e-<时间戳>.db
```

该层有意设置 `DATABASE_URL`，因此 `world_managed=false`。它把 Context/Provider 问题与世界包装器问题
分开，适合快速定位真实 API、流式、落库、两轮历史和缓存 usage。

### 2.6 真实世界 E2E

需要证明正常 World 包装器、世界密钥、前后端和真实 Provider 的完整产品链路时使用本层，并保留标准 World 供查看。只核对 Provider 格式可选择 contract；隔离 Provider/Context 问题可选择 real 兼容库。不能用其中一层声称覆盖另一层。

```bash
npm run test:e2e:real-world
```

存储：

```text
data/roleplex-real-world-e2e-<时间戳>/
└── default/
    ├── world.json
    ├── roleplex.db
    ├── .jwt-secret
    ├── .api-key-secret
    └── files/
```

该层使用正常世界包装器，健康检查必须为 `world_managed=true`。它执行 W1a 真实 Provider
`workspace_list/read/write` 闭环、W1b 真实 `pwd/list/read/count`、C2 两轮验证码单聊，以及 M4a
两个真实角色的同 chain 串行群聊；协作码只放进 A 的 system prompt，B 必须从 A 已提交回复中复述。测试
同时核对真实 usage、稳定层、history、独立 execution 和 Prompt 不落日志。它不切换世界；切换语义由
fake 世界 E2E 负责，避免一次失败混入两个高风险变量。

W1a 工作区与 World 使用同一 stamp，但位于两个根：World 在项目 `data/`，工作区在
`<外部测试根>/roleplex-real-world-e2e-<时间戳>/default`。测试必须断言两者没有落入
同一物理目录，真实 Key 只进入 World 加密数据库，不得进入浏览器或工作区。

W1b 的真实验收可单独运行 `npm run test:e2e:real-world -- commands-provider.spec.ts`，仍复用本层配置、
正常世界包装器和真实 Provider，不调用命令故障注入入口。用例工作区位于上述 `default/w1b-commands/`，
校验值只写入本轮文件，不写入提示词；模型必须通过真实命令读取并回答。该用例关闭截图/trace/video，
失败先清空页面，再保存安全阶段标签，避免把模型正文带入失败产物。
该用例同时验证真实工具位于最终回答之前，以及 Owner 展开、刷新后重新读取加密详情。

### 2.6.1 真实文档长会话

显式设置 `ROLEPLEX_LONG_CONVERSATION_SOURCE` 为获准发送给供应商的本地 UTF-8 文档路径，再运行
`npm run test:e2e:real-world -- long-conversation.spec.ts`。未设置时该用例 skip；普通回归不读取用户文件。
该测试会联网计费，复用 real-world 独立世界和后端加密凭据，六轮实际生成验证历史上下文、切换与刷新。
文档正文及回答只保存在本轮产品会话数据库，不保存截图/trace/video；报告只含轮数、消息字节数、
厂商 usage 和是否超过分页预算。模型产出不稳定，须按实测体积判断覆盖，不能用轮数冒充体积边界。
该用例现在要求实际内容超过 64 KiB，并验证刷新后最近窗口、向上补齐、缓存切回与阅读位置。
确定性边界入口为 `npm run test:e2e -- history-window.spec.ts` 和 `pytest tests/test_history_window.py -q`，
覆盖超大单条、体积/条数、连续游标、局部失败、快照失效、版本竞态、LRU 与布局变化，不用真实生成替代这些边界测试。

### 2.7 工作区、命令与审批 E2E

W1c 的独立入口是 `npm run test:e2e:commands -- shell-approvals.spec.ts`，复用本层 fake World、端口与轮次，
用例位于该轮外部工作区的 `default/shell-approvals/`。覆盖 pending 前无执行、刷新恢复、批准、拒绝、停止和到期；
仅测试服务器对精确到期脚本将等待缩为 1 秒，正常产品仍固定 5 分钟，不提供缩短审批的产品参数。
后端 `pytest tests/test_shell_approvals.py -q` 每个并发/故障用例使用全新迁移库；工作区复用
`roleplex-command/test-<时间戳>/case-<随机标识>` 分层，不向测试根平铺目录。

真实入口为 `npm run test:e2e:real-world -- shell-provider.spec.ts`：会联网计费，在该轮 `default/shell-approval/`
创建未知校验值，只批准预先限定的只读脚本；模型提出额外命令时拒绝并失败，不扩大测试授权。
使用正常世界包装器，脚本/输出/根不进正式日志，关闭截图/trace/video，失败只保存安全阶段标签。
Linux 真实进程测试另验证 setsid 后代在正常退出/取消时的回收；PowerShell 参数有确定性测试，Windows
原生 Job Object/stdio 实机运行仍是待人工或 Windows CI 补齐的覆盖缺口，不能用 Linux 通过结果代替。
Shell 详情修正增加 `pytest tests/test_shell_details.py -q`，验证审批关联、空输出与未记录的区别、双流限额、
过期/密文篡改和 Guest 拒绝。审批浏览器用例同时检查展开/刷新、旧卡缺少能力标记、双流正文和 Guest 不请求详情；
真实 Shell smoke 还会核对 Owner 展开与刷新后的真实 stdout，依旧关闭真实内容截图和 trace。

在 `frontend/` 执行 `npm run test:e2e:commands`。该命令固定 fake Provider，启动真实前后端和独立 World，
通过浏览器登记工作区、开启命令、绑定单聊，验证 pwd/list/read/count、截断、非零退出、超时、停止和刷新恢复。
超时/取消后还读取测试 fixture 的父子 PID，确认进程不再运行。

`backend/tests/command_e2e_server.py` 是专用测试入口，只在本轮精确目录中将固定测试文件映射到受控进程
profile；它不会被正常产品启动导入，产品 command allowlist 始终只有四种命令。测试采用 3 秒/1 KiB 限制，
不改变正常启动默认值；单元/集成测试入口是 `pytest tests/test_workspace_commands.py -q`。
本层关闭 trace/video；截图必须不含认证输入或真实模型输出。Linux 浏览器检查需安装中文字体，例如文泉驿微米黑。
工具时间线用例额外在本轮 `default/timeline/` 子目录使用固定占位文本，验证文字/工具顺序、主动断线重连、
Owner 展开与刷新恢复、Guest 登录后的仅摘要展示及详情 API 403。Guest 成员由独立测试脚本在精确的 fake
测试库播种，不新增产品入群旁路。后端 `tests/test_tool_timeline.py` 覆盖密文绑定、过期清理、权限和 WS 重放。

## 三、端口矩阵

| 场景 | 前端 | 后端 |
|---|---:|---:|
| 正常开发 | 51173 | 8000 |
| 普通 fake E2E | 51174 | 8001 |
| real E2E | 51175 | 8002 |
| fake worlds E2E | 51176 | 8003 |
| real-world E2E | 51177 | 8004 |
| fake commands E2E | 51178 | 8005 |

测试配置使用 `127.0.0.1`，避免 Windows 上 `localhost` 解析到 IPv6 而后端只监听 IPv4。Playwright 设置
`reuseExistingServer=false`；端口冲突时先确认占用进程的归属，只清理本次测试拥有的服务，或调整独立测试配置，不终止未知/用户服务。

## 四、数据、账号与保留

| 层级 | 数据位置 | Owner | 密码 | 保留 |
|---|---|---|---|---:|
| pytest | `data/roleplex-test-<时间戳>.db` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| fake E2E | `data/roleplex-e2e-<时间戳>.db` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| real E2E | `data/roleplex-real-e2e-<时间戳>.db` | `realtest<时间戳>` | `Roleplex-Real-E2E-1` | 5 轮 |
| fake worlds | `data/roleplex-world-e2e-<时间戳>/{alpha,beta}` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| real-world | `data/roleplex-real-world-e2e-<时间戳>/default` | `realtest<时间戳>` | `Roleplex-Real-E2E-1` | 5 轮 |
| fake commands | `data/roleplex-command-e2e-<时间戳>/default` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |

fake worlds、real-world 和 commands 的外部工作区使用 `ROLEPLEX_E2E_WORKSPACE_ROOT`，与 World 共享时间戳，按“类别/轮次 → World → 用例”组织。当前配置的默认值仍是 `/home/chen/workspace/testworkspace`，这是现有测试环境的兼容值，不是项目目录要求。其他机器运行前应指定可写的仓库外测试目录，例如从 `frontend/` 执行：

```bash
mkdir -p ../../testworkspace
ROLEPLEX_E2E_WORKSPACE_ROOT="$(realpath ../../testworkspace)" npm run test:e2e:commands
```

示例创建仓库外的专用 `testworkspace` 目录；PowerShell 可用 `$env:ROLEPLEX_E2E_WORKSPACE_ROOT` 设置绝对路径。该变量仅覆盖对应 Playwright 配置，不能声称已覆盖所有 pytest 夹具：`backend/tests/test_workspace_commands.py` 的 Linux 根仍硬编码为上述默认路径，Windows 使用 pytest 临时根。受该限制的机器需调整夹具后再运行相关测试，本次文档整理未改动测试实现。

保留策略按整轮执行，当前目标为最近 5 轮。pytest 与 commands setup 在新轮开始时清理；普通 fake/real E2E 及 fake worlds/real-world 的 teardown 在结束时清理，不再统一描述成“全部在下一轮开始”。异常退出可能留下待下轮处理的数据。

命令后端工作区为 `<测试根>/roleplex-command/test-<时间戳>/case-<随机标识>/`。旧平铺数据的 `legacy/` 与重定位记录属于历史保留资料，不参与新轮自动淘汰；修改目录时不能改写历史 execution 快照。

要查看某轮 commands E2E，可从 `backend/` 运行
`python scripts/run_world_server.py --world default --worlds-dir ../data/roleplex-command-e2e-<时间戳> --port 8000`，
再正常启动前端。这个人工查看入口使用正常产品 worker，不会启用测试专属进程 profile。

contract 测试不依赖产品会话数据，也不播种测试账号；父级 pytest 基础设施仍会为该轮分配隔离数据库名。
测试数据库/世界都被 `.gitignore` 排除。real/real-world 包含加密后的真实 Key；世界目录还包含解密所需
的 `.api-key-secret`，因此只能留在本机，不得分享。

清理按各测试入口的时间戳目录/文件执行，失败不覆盖测试结论。正在运行的 SQLite 可能暂时保留 WAL/SHM；
下轮清理会再次处理。世界测试被 Playwright 强制终止时可能留下 `.active.json`，下次由 WorldManager 验证
PID 已消失后安全回收，不能仅按文件存在判断世界仍活跃。

## 五、日志与报告

### 5.1 pytest

```text
logs/tests/unit/YYYY-MM-DD/
├── summary.jsonl
└── failures/HH-MM-SS_<run-id>/*.json
```

- 每轮 pytest 只追加一条 summary；
- 全绿不创建 failure 目录；
- 失败文件只保留有界、脱敏的 stdout/stderr/logging/traceback 尾部；
- summary 的 `test_scope` 区分 full 与 partial；
- `captured_stderr` 是输出流，不等于 ERROR 日志级别。

### 5.2 Playwright

```text
logs/tests/e2e/fake/YYYY-MM-DD/HH-MM-SS_<run-id>/
logs/tests/e2e/real/YYYY-MM-DD/HH-MM-SS_<run-id>/
```

每轮包含 `events.jsonl`、`errors.jsonl`、`summary.json` 和 `artifacts.json`。fake worlds 仍进入 fake；real 与
real-world 都进入 real。区分真实测试存储模式：

- `summary.database`：显式数据库 real smoke；
- `summary.worlds`：real-world 世界路径。

现有 E2E 配置关闭 trace/video。截图策略并不统一：配置基线为 `only-on-failure`，多数真实工具专项通过 `test.use` 关闭截图；旧两轮 Provider 通用流程还会主动截图。因此不能把 real/real-world 整套描述为“截图全部关闭”。运行具体真实用例前核对其配置、主动截图和失败附件路径；真实内容、认证输入和凭据不应进入浏览器产物。fake 的受控占位页面可截图检查布局。

## 六、人工查看保留结果

### 6.1 显式测试数据库

从 `backend/` 启动：

```bash
DATABASE_URL="sqlite+aiosqlite:///../data/roleplex-test-<时间戳>.db" \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

把文件名替换为 `roleplex-e2e-*` 或 `roleplex-real-e2e-*` 即可查看对应轮次。显式数据库模式会禁用世界
切换，这属于预期行为。

### 6.2 fake 世界切换结果

```bash
cd backend
python scripts/run_world_server.py \
  --world alpha \
  --worlds-dir ../data/roleplex-world-e2e-<时间戳> \
  --host 0.0.0.0 --port 8000
```

### 6.3 real-world 结果

```bash
cd backend
python scripts/run_world_server.py \
  --world default \
  --worlds-dir ../data/roleplex-real-world-e2e-<时间戳> \
  --host 0.0.0.0 --port 8000
```

real-world 账号是 `realtest<时间戳>`，密码是本页第四节的固定测试占位值。目录包含真实 Key 和解密密钥，
人工查看后仍需按敏感数据保管。

## 七、每层证明什么、不证明什么

| 层级 | 证明 | 不证明 |
|---|---|---|
| pytest | 后端确定性业务和安全边界 | 浏览器布局、真实 Provider |
| contract | Provider SDK/API 真实差异 | 产品数据库、前端、世界包装器 |
| fake E2E | 浏览器与真实前后端集成 | 真实厂商行为 |
| fake worlds | 世界切换、隔离、重启 | 真实厂商调用 |
| real E2E | 浏览器、Context、Provider、落库 | 正常世界密钥/包装器路径 |
| real-world | 正常产品世界与真实 Provider | 多世界切换 |

不能用“某一层全绿”替代其他层。尤其是：

- pytest fake 不证明真实 usage 字段；
- real Provider 成功不证明世界切换；
- 布局与可访问性需要相应浏览器断言或受控截图，人工验收是否为交付关口由本次任务决定；
- 编译成功不代表权限、迁移或用户流程正确。

## 八、常见失败排查

### 8.1 登录 401

Owner 是数据库/世界级单例。确认账号时间戳与当前测试 DB/世界一致；不要拿某轮账号登录另一轮数据库。
E2E 准备阶段首次登录不存在账号会出现一次预期 401，随后注册，不应单凭这行日志判断用例失败。

### 8.2 浏览器 Failed to fetch / CORS

核对本页端口矩阵、Playwright 配置中的 `CORS_ORIGINS` 和 `VITE_API_URL`。测试禁止硬编码 8000，应使用
配置下发的 `ROLEPLEX_E2E_API_ORIGIN`。

### 8.3 summary 保持 running

`summary.json` 在开始时写为 running，正常结束后原子替换。进程消失但 summary 仍为 running 表示测试被
中断或异常终止，不应猜成 failed/passed。

### 8.4 生成一直转圈

先按 conversation/generation/request ID 查询 E2E `events.jsonl`：

```bash
rg 'generation.created|provider.call_started|provider.call_completed|generation.failed' \
  logs/tests/e2e/<fake-or-real>/<日期>/<run>/events.jsonl
```

预算错误 `CONTEXT_BUDGET_EXCEEDED` 会在 Provider 调用前终止；这时没有 `provider.call_started` 是正确证据。

### 8.5 真实测试没有运行

确认使用了显式 real/real-world 命令，并配置 Key、模型和 base URL。普通 pytest 与普通 E2E 无论 `.env`
是否存在都固定 fake；contract 未配置某厂商时会显示 skip。

真实 Provider 路由可从 `provider.built` 或 `provider.call_started/completed/failed` 的 `base_url` 查看；该值已
移除 URL 凭据和 query。E2E `summary.json` 同时汇总 `base_url` 与 `base_url_source`。若返回
`PROVIDER_AUTH_FAILED`，先核对该脱敏路由对应的 Key 是否仍有效，不要把 Key 打印到终端或日志。

## 九、新增测试时的规则

- 按改动风险选层级，先复用相关测试；纯文档或低风险展示调整不强制新增测试。
- 使用隔离数据库/World 和受控文件；并发用例隔离共享状态，E2E 不依赖其他 spec 的数据数量或顺序。
- 对安全与一致性缺陷保留能捕获回归的确定性断言；平台缺口、skip 和真实模型未触发路径单独说明。
- 新增或改变用户流程时验证成功与关键失败路径；视觉检查使用可安全保存的页面。
- fake 与真实 Provider 使用各自命令/配置，真实 Key 只在后端进入加密边界，不保存敏感截图、trace、Prompt 或输出原文。
- schema 变化运行迁移检查；测试结束清理本轮启动的服务，按现有入口保留结果。
- 完成报告说明实际运行范围。人工验收、提交与发布按当前任务授权处理，不从历史测试记录继承额外关口。

工程规则见 [AGENTS.md](../../AGENTS.md)，接口断言以领域协议为准。

## 十、历史验证记录索引

以下记录保留当时环境、命令、结果和缺口，不代表本次已重新运行，也不产生新的收费测试要求。当前行为与旧记录冲突时，核对现行领域协议和对应实现。

| 主题 | 记录 |
|---|---|
| 工具取证与可信收尾 | [T0](tool-reliability-t0.md)、[T1](tool-reliability-t1.md)、[停止事实](budget-stop-facts.md) |
| 文件搜索、编辑与写入 | [T2](tool-reliability-t2.md)、[T3](tool-reliability-t3.md)、[父目录](workspace-write-parents.md)、[写入等待](workspace-write-wait.md)、[批次限制调整](batch-mutation-limits.md) |
| 服务与共同授权 | [后台服务](runtime-services.md)、[G 验证](tool-execution-g.md) |
| 预算与用量 | [T4.1](agent-budget-t41.md)、[T4.2](agent-budget-t42.md)、[T4.3a](agent-budget-t43a.md)、[T4.3c](agent-budget-t43c.md)、[角色用量](role-execution-usage.md) |
| 参数、说明与中断事实 | [参数恢复](tool-argument-recovery.md)、[工具说明](workspace-tool-guidance.md)、[中断交接](interruption-context.md) |
| 界面 | [输出设置](role-output-settings.md)、[侧栏主题](sidebar-theme.md)、[会话详情](conversation-details.md) |
| Provider 排障 | [Token 核查](token-usage-audit.md)、[中转接口核验](tokenrhythm-contract-check.md) |

## 十一、群协调与并行循环

后端确定性入口（在 backend/）：`pytest tests/test_orchestrator.py tests/test_resource_admission.py -q`。覆盖实际协调工具分工、两轮文件开发/并行审查/判断/汇总、同角色不同工具分配、执行时撤权、全部分支停止、取消任命、人工条件与跳过汇合、局部重试保留独立分支/旧轮次，以及资源别名、公平读写、取消释放与版本冲突。并发重叠由事件屏障证明，不以总耗时猜测。旧工作流、群聊、原生文件和 Shell/服务回归仍复用原入口。

浏览器入口（在 frontend/）：`npm run test:e2e:commands -- orchestration.spec.ts`，真实前后端 + fake Provider 验证群任命、右侧工具/循环配置、协调返工、刷新选择历史，以及未任命入口和人工等待期间撤销。受控工作区位于本轮 commands 根下的 orchestration / orchestration-revoke，不碰用户文件。

真实入口：`npm run test:e2e:real-world -- orchestration-provider.spec.ts`。此命令联网计费，复用既有专用凭据和正常 World 包装器，独立群由开发者、两名审查者和协调者组成；实际文件第一轮不通过、第二轮返工后通过，验证结构化结果、独立 execution、真实 usage 和刷新历史。使用 64 次共享测试预算与本轮根下的 orchestration 文件目录，关闭截图/trace/video；失败只保存阶段标签，正文留在产品 World。没有配置真实凭据时不能用 fake 结果替代。

实际运行记录与保留 World 见[群协调 v2 验证](orchestrator-v2.md)。

以上入口当前覆盖预设图上的固定分配/上报和运行，并不证明模型创建/修改图、独立规划授权或运行图重规划已经实现。新增用例按[整改验收](../plan/orchestrator-graph-control-v1.md#7-实施顺序与验收)补齐；实现后再登记实际命令与结果，不把历史通过作为整改完成。

### 工作流连线避障

`npm run test:e2e:commands -- workflow-routing.spec.ts workflow-reactflow.spec.ts` 使用真实前后端与 fake Provider，验证普通边绕过中间节点、外侧循环回边、自环、拖动后重算、保存恢复及键盘删除。通过 SVG 路径采样与屏幕节点边界检查穿越，而非只断言路径字符串变化；受控截图用于检查走线形态。此项不需要真实 Provider，也不改变工作流执行协议。

2026-09-21 实测：上述组合 3 passed；外侧拐角整理后单独复跑 workflow-routing 用例 1 passed，TypeScript/Vite 构建通过。检查了受控截图，测试服务已退出。本轮未调用真实模型。

## 十二、独立协调与图管理

后端入口（backend/）：`pytest tests/test_graph_control.py -q --tb=short`。与既有 test_orchestrator/test_workflows/test_group_chat/resource_admission 联合回归，覆盖明确管理授权、整体写入与 ID 编辑、原子版本/幂等、运行新增节点、未来循环修订、历史激活、撤销与恢复及结果修订 CAS。迁移检查包含 0021/0022。

浏览器入口（frontend/）：`npm run test:e2e:commands -- graph-control.spec.ts`。从聊天 `/plan@协调者` 开始，实际模型工具写图，验证冲突保留、共享预算启动、运行追加审查与历史版本。工作区使用本轮 commands 根下的独立用例子目录。

真实入口：`npm run test:e2e:real-world -- graph-control-provider.spec.ts`，会联网计费，复用既有真实 World/凭据准备入口，工作区位于本轮 default/graph-control。测试只预置角色、工作区和空群，最终图必须由真实协调者通过工具创建/调整，并执行文件验证。截图/trace/video 关闭；失败仅记录安全阶段。实测、保留 World 和覆盖边界见[本次图管理验收](orchestrator-graph-control.md)。


### 工作流本地草稿

后端：在 `backend/` 运行 `python -m pytest tests/test_workflow_draft_scope.py -q`，检查分区稳定性、会话隔离、无缓存及 Owner/Guest 权限。
浏览器：在 `frontend/` 运行 `npm run test:e2e:commands -- workflow-drafts.spec.ts workflow-node-editing.spec.ts`，使用隔离 World 与 fake Provider，覆盖输入后立即刷新、节点/颜色/补充要求恢复、远端版本冲突、显式保存后的干净状态、多标签页独立副本、存储不可用与下载备份、运行图目标恢复、损坏副本降级、手动恢复时保留当前编辑、同一 Owner 重新登录恢复及原节点编辑操作。此命令不验证操作系统强杀浏览器时的写入完成保证，也不验证真实 Windows 浏览器的崩溃恢复。

### 工作流反馈与局部处置

后端（backend/）：`python -m pytest tests/test_workflow_feedback.py tests/test_graph_control.py tests/test_orchestrator.py -q --tb=short --show-capture=no`。反馈用例覆盖来源及结果原子提交、重复请求/并发版本、Guest/跨运行拒绝、无关分支继续、契约裁定/实现修复/能力缺口/未验证分类、循环内局部补图、真实完成后复核、停止/撤权/取消竞态及重启后人工继续。迁移检查 `python scripts/check_migrations.py` 包含 0023。

浏览器（frontend/）：`npm run test:e2e:commands -- workflow-feedback.spec.ts graph-control.spec.ts`，实际前后端和 fake Provider 验证从节点反馈来源到人工委托、局部改图、验证关闭、补充反馈、版本竞争，以及启动时的明确自动授权。受控截图通过既有日志 reporter 保留。

真实入口：`npm run test:e2e:real-world -- workflow-feedback-provider.spec.ts`，会联网计费。复用既有真实 World、后端凭据和保留规则；用例工作区位于本轮 `workflow-feedback` 子目录。只预置受控冲突文件、角色与来源流程，反馈、图修改、处理执行和复核都必须走真实模型工具。截图/trace/video 关闭，失败只记录安全阶段；实际记录和覆盖边界见[反馈验收](workflow-feedback.md)。

### 工作流图可读性

frontend 的 `npm run test:workflow-logic` 使用现有 Playwright runner 验证纯布局/投影，无须启动浏览器或后端。覆盖节点数组乱序、回边与未声明环路、手动位置、测量尺寸、循环区域、多个循环、展开/折叠还原、异常摘要与业务标签。

backend 的 `python -m pytest tests/test_workflow_presentation.py tests/test_graph_control.py tests/test_orchestrator.py tests/test_workflow_feedback.py -q --tb=short --show-capture=no` 覆盖展示信息保存、旧客户端省略字段、历史版本、非法引用、运行循环即时展示，以及正在规划/汇总时纯展示修订不改变 phase、分工或已有尝试。持久化复用既有 JSON 字段，没有新增表或迁移。

frontend 的 `npm run test:e2e:commands -- workflow-readability.spec.ts workflow-node-editing.spec.ts workflow-drafts.spec.ts workflow-reactflow.spec.ts workflow-routing.spec.ts workflow-feedback.spec.ts graph-control.spec.ts` 使用真实前后端和 fake Provider，验证总览、展开、整理/撤销/保存、业务标签、实际运行反馈、小屏定位及原交互回归。截图通过既有 reporter 存入标准日志目录。视觉检查和本轮结果见[可读性验收](workflow-readability.md)；本批不重复运行收费 Provider 验收。

### 工作流操作面板

frontend：`npm run test:e2e:commands -- workflow-workbench.spec.ts workflow-feedback.spec.ts workflow-drafts.spec.ts workflow-node-editing.spec.ts workflow-canvas.spec.ts workflow-reactflow.spec.ts workflow-routing.spec.ts workflow-readability.spec.ts graph-control.spec.ts orchestration.spec.ts`。真实前后端和 fake Provider 覆盖主工具栏与唯一上下文、模板/运行分别提交、历史刷新与读取失败只读、草稿冲突和恢复、反馈处置与局部调整、取消协调保留图与原执行、重新运行的新请求身份、Guest 隔离、窄屏/键盘及原图交互。

相邻右栏回归：`npm run test:e2e -- conversation-details.spec.ts`；布局算法：`npm run test:workflow-logic`；编译构建：`npm run build`。与浏览器相关的配置使用各自隔离测试库；依次运行，避免测试产物目录互相覆盖。取消协调的固件只在 fake Provider 的指定标记下停留于真实图提交之后，取消通过产品控制路径触发，不直接改写运行状态。

本批维护现有真实 Provider 脚本的 UI 入口，但不因界面调整重复调用收费模型。实际结果、截图与覆盖缺口见[操作面板验收](workflow-workbench.md)。

### 提示词与能力配置（总体计划 A）

后端在 `backend/`：`python -m pytest tests/test_prompt_settings.py tests/test_context_builder.py tests/test_chat_flow.py tests/test_orchestrator.py tests/test_graph_control.py tests/test_tool_execution_policy.py tests/test_agent_pipeline.py tests/test_execution_usage.py tests/test_service_discovery.py tests/test_group_workspaces.py tests/test_workflow_feedback.py tests/test_delete_semantics.py -q --tb=short --show-capture=no`。覆盖配置默认/空覆盖/CAS、Owner/Guest/成员、角色配置保留及版本、实际输入与采用记录、存储异常隐私，以及原生成、工具和工作流路径。迁移检查 `python scripts/check_migrations.py` 包含 0024。

浏览器在 `frontend/` 依次运行，避免测试产物目录互相覆盖：

- `npm run test:e2e:commands -- prompt-settings.spec.ts graph-control.spec.ts orchestration.spec.ts workflow-workbench.spec.ts group-workspace.spec.ts`：实际配置进入 fake 模型、响应丢失后核对、版本冲突/草稿、窄屏/Guest 与群聊、工作流回归。
- `npm run test:e2e -- conversation-details.spec.ts role-output-settings.spec.ts`：原详情模块与角色参数/工具操作。
- `npm run test:e2e:worlds -- prompt-worlds.spec.ts`：物理 World 切换、重新认证、配置隔离和重启后保留。
- `npm run build`：类型检查与生产构建。

验收使用受控提示词和 fake Provider；`PROMPT_LAYERS_PROBE` 固件仅回报特定受控标记，避免直接回显完整系统输入。预览本身不触发模型；本批未运行收费 Provider 验证。结果与边界见[提示词配置验收](prompt-settings.md)。
