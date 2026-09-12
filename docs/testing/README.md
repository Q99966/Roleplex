# Roleplex 测试指南

| 元数据 | 值 |
|---|---|
| 受众 | 开发者、测试维护者和本机人工验收人员 |
| 状态 | 已实现测试体系的使用指南 |
| 维护者 | Roleplex |
| 事实来源 | `backend/tests/`、`frontend/tests/`、Playwright 配置、pytest 配置与日志 v2 |
| 复核日期 | 2026-09-10 |

本文是 Roleplex 测试分层、命令、端口、数据、账号、日志和人工排查方式的统一入口。接口断言仍以对应
协议文档为权威，日志字段以 [日志 v2](../design/logging-v2.md) 为权威；本文不复制完整 wire schema。

## 一、快速选择

| 层级 | 命令 | Provider | 存储模式 | 联网/计费 | 主要证明 |
|---|---|---|---|---|---|
| 后端 pytest | `pytest -q` | fake | 每轮独立 SQLite DB | 否 | 服务、权限、状态机、迁移相关业务行为 |
| Provider contract | `pytest tests/contract -m contract -q` | real | 不走产品会话 DB | 是 | 厂商流式、工具、取消、usage 和错误格式 |
| 普通浏览器 E2E | `npm run test:e2e` | fake | 每轮独立 SQLite DB | 否 | 单聊、M4a 群聊与其他浏览器用户流程 |
| 世界切换 E2E | `npm run test:e2e:worlds` | fake | 临时 alpha/beta 世界 + 外部工作区 | 否 | W1a/C2/M4a、包装器重启、世界与工作区隔离 |
| 结构化命令 E2E | `npm run test:e2e:commands` | fake | 临时 default 世界 + 外部工作区 | 否 | W1b 真实进程、输出/退出码、超时/停止与刷新恢复 |
| 真实 Provider E2E | `npm run test:e2e:real` | real | 每轮独立 SQLite DB | 是 | 真实浏览器到 Provider，隔离世界基础设施干扰 |
| 真实世界 E2E | `npm run test:e2e:real-world` | real | 临时 default 世界 + 外部工作区 | 是 | W1a 文件、W1b 命令、C2、M4a 与正常世界全链路 |

默认开发回归只需要：

```bash
cd backend
pytest -q

cd ../frontend
npm run build
npm run test:e2e
```

涉及迁移时额外执行：

```bash
cd backend
python scripts/check_migrations.py
```

真实命令必须通过上表中的独立命令显式运行，不属于普通 CI 或默认回归。“显式”描述的是命令和配置隔离，
不是要求必须由用户本人在终端执行：当用户已授权推进或完成一个验收标准包含真实 Provider 的阶段时，Agent
应在说明联网与计费后主动运行对应真实命令。只有凭据缺失、用户明确禁止或该阶段尚未要求真实验收时才跳过，
并必须明确报告未覆盖项。

## 二、测试层级与边界

E2 批量写入/编辑：`pytest tests/test_workspace_batch_mutation.py -q`，覆盖整批预检、硬链接/重复目标、
旧参数兼容及互斥、输入预算、顺序提交、部分成功、只重试失败项、未知 OS 写入、取消、重新授权、
服务占用且不擅停、锁等待/两批准入、整批 diff 预算、采集/加密失败与崩溃结果缺口。
`npm run test:e2e:commands -- batch-mutation.spec.ts` 覆盖实际文件/diff、逐文件折叠、预检失败不重写、
刷新、Guest 隔离及 390px 窄屏；目录沿用命令类别/本轮时间戳/default/batch-mutation。
真实入口仍为 `npm run test:e2e:real-world -- workspace-provider.spec.ts runtime-service-provider.spec.ts`，
完整场景两轮创建/修改 index.html 与 note.txt，独立验证文件、页面和保留内容，并按实际私有 write_batch 观察采用路径。
不强制 items；未触发的多文件编辑仍由确定性测试覆盖，真实报告分别列出实际路径，不为取得路径重跑计费。
跨调用 exactly-once、自动回滚、未知结果自动重放不在本功能范围内。

E2 批量读取切片：`pytest tests/test_workspace_read_many.py -q` 验证逐项失败、全文件 hash/续读、JSON 转义后预算、
最大文件批次、严格参数、权限撤销、两批准入/每批两项、取消收尾、路径/敏感文件/链接、重复观察、
加密失败降级和崩溃结果缺口。`npm run test:e2e:commands -- read-many.spec.ts` 验证一次调用的多文件节点、
独立展开、部分失败、刷新仍看原版本及 Guest 不请求私有详情；目录为命令类别/本轮时间戳/default/read-many。
真实入口仍复用 `npm run test:e2e:real-world -- runtime-service-provider.spec.ts`，包含多文件读取目标并记录实际工具选择，
触发批量读取时核验详情结构；不为取得该路径重复计费。该切片不覆盖尚未实现的批量写入/编辑及其重放语义。
入口已统一为 workspace_read(items=...)；同一专项同时验证旧 path 形式的五字段响应、混合参数拒绝、原 read 权限撤销
与实验名不再暴露。浏览器同时展开两种形式，确保单项旧记录不会被误显示为批量未记录；真实观察按私有 read_batch
判定是否触发 items，不将所有 workspace_read 调用都算成批量。历史测试文件名保留，不代表仍暴露实验工具。

E2 探索归组切片：`npm run test:e2e -- exploration-grouping.spec.ts` 验证顺序、边界、旧版/未知版本、
重复身份与异常摘要；`npm run test:e2e:commands -- exploration.spec.ts` 使用真实前后端和确定性文件工具，
验证连续 Read/List、重复读取计数、缺失文件、流式折叠选择、断线/刷新、正文与跨轮边界、Owner/Guest 详情隔离，
以及隐藏子项阅读锚点和收起侧栏后的 390px 窄屏。测试目录为命令类别/本轮时间戳/default/exploration，沿用整轮清理。
真实回归复用 `npm run test:e2e:real-world -- runtime-service-provider.spec.ts`，不新增或强迫特定读取路径的收费场景；
此切片尚未实现批量文件执行，不据此声称覆盖 E2 的批量预算、部分提交、重放与子项状态。

E1 局部编辑专项：`pytest tests/test_workspace_edit.py -q` 与
`npm run test:e2e:commands -- workspace-edit.spec.ts`。验证唯一字面匹配（含重叠拒绝）、全文件 hash、
UTF-8 片段预算、edit/write 共用提交与并发锁、服务占用拒绝、取消后已应用事实、详情保存降级及 Owner/Guest 隔离。
测试目录复用命令类别/本轮时间戳/用例布局；浏览器仅新增单项编辑成功/冲突展示，不复制完整开发场景。
真实入口仍为 `npm run test:e2e:real-world -- workspace-provider.spec.ts runtime-service-provider.spec.ts`：
前者验证明确的 read → edit 工具契约；后者启用完整工具集并记录实际选择，不强制 edit，分别报告尝试数、成功数与 diff 核验。
按阶段要求运行前告知联网计费，关闭真实截图/trace/video，结束时正常回收本轮服务；未触发能力不声称覆盖。

D 写入 diff：后端 `pytest tests/test_write_diff.py -q`；浏览器
`npm run test:e2e:commands -- write-diff.spec.ts`，覆盖实际文件写入、时间线位置、写入卡默认展开与单层折叠、长行软换行、刷新、Guest 隔离及展示降级。
后端另验证队列/输入/输出预算、取消/关闭回收、密文身份、过期、历史不重建，以及计算/保存失败不重写文件。
复用命令测试类别下本轮 `write-diff/` 用例目录，不平铺随机工作区；结束回收测试服务。
真实入口复用 `npm run test:e2e:real-world -- workspace-provider.spec.ts runtime-service-provider.spec.ts`，会联网计费，
验证原生创建/修改的私有差异和既有完整工具集两轮开发。未触发工具路径不冒充覆盖，真实截图/trace/video 关闭。
差异预算、状态与隐私契约见[工具详情](../protocol/public/messaging/tool-details.md)，D 未包含局部编辑或批量文件执行。

G 共同执行授权、read 全文件 hash 闭环、diff 候选试验与真实两轮场景的结果和未覆盖项见
[G 验证记录](tool-execution-g.md)。工具开关/服务生命周期不因 diff 选型而改为固定启动入口；候选试验不属于产品功能。

M 多行输入专项：`npm run test:e2e -- multiline-composer.spec.ts`；后端正文与模型投影验证：
`pytest tests/test_context_builder.py tests/test_chat_flow.py -q`。浏览器覆盖剪贴板多行粘贴、Enter/Shift+Enter、
组合事件与 229 确认键、光标中间 @ 补全、HTTP 失败/迟到响应、加载草稿、Owner/Guest `/ps`、自动增高及阅读锚点。
复用普通 fake 前后端与隔离数据库；Guest 成员通过 `tests/seed_tool_viewer.py` 在严格匹配的本轮测试库播种，
不增加产品成员管理旁路。桌面/窄屏截图仅使用无实际价值的占位代码。
触屏软键盘验证是浏览器模拟，真实中文输入法和手机键盘仍需人工检查；M 不要求真实 Provider 计费验证。
人工验收前刷新前端并重启后端，使模型侧使用保留正文空白的新投影。切换会话或刷新不保留未提交草稿，
失败草稿保护仅作用于仍挂载的当前输入框，不是持久草稿功能。

连接解耦 A 的专项浏览器命令是 `npm run test:e2e -- connection-session.spec.ts`，包含同一物理连接切换、
组件重挂载、延迟同步确认、旧订阅错误/快照、真实掉线恢复、历史超时重试、Token 撤销与旧身份配置隔离。
这些确定性场景复用普通 fake 测试服务和数据库，不新增测试数据目录类型。后端控制契约见
`tests/test_ws_session.py`。`test:e2e:worlds` 继续覆盖世界切换，`test:e2e:real-world -- commands-provider.spec.ts`
验证真实消息/工具链中切换会话仍复用连接；后者会联网计费。

A 验收前需重启后端并刷新前端，使双方都支持 subscription_control_v1；旧后端会显示明确的协议能力错误。
界面出现“正在同步会话”表示尚未收到同步完成确认，不是按固定延时推断连接失败。

### 2.1 后端 pytest

W1d 的故障矩阵、World 协调操作、专项命令和人工步骤统一见
[后台服务收尾验证指南](runtime-services.md)。测试复用现有 World/工作区轮次布局；真实 npm 固件只使用 Node 内置模块，不安装依赖。

工作目录：`backend/`

```bash
conda activate roleplex
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
└── beta/
```

后端通过正常 `run_world_server.py` 包装器启动。测试验证 alpha → beta 重启、Token 失效、物理数据库与
双密钥隔离，以及切换后重新注册 Owner。切换前先在 alpha 中完成 C2 两轮 fake 单聊和 M4a 两角色串行
群聊，并直接核对 JSONL 的稳定层、history、chain/execution 与 fake usage。该测试因此同时证明 C2/M4a
在正常世界包装器中的确定性路径，不产生模型费用。

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
`/home/chen/workspace/testworkspace/roleplex-real-world-e2e-<时间戳>/default`。测试必须断言两者没有落入
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

### 2.7 W1b 结构化命令与 W1c 审批 E2E

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
`reuseExistingServer=false`，端口被其他进程占用时应先停止冲突进程，而不是让测试复用未知服务。

## 四、数据、账号与保留

| 层级 | 数据位置 | Owner | 密码 | 保留 |
|---|---|---|---|---:|
| pytest | `data/roleplex-test-<时间戳>.db` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| fake E2E | `data/roleplex-e2e-<时间戳>.db` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| real E2E | `data/roleplex-real-e2e-<时间戳>.db` | `realtest<时间戳>` | `Roleplex-Real-E2E-1` | 5 轮 |
| fake worlds | `data/roleplex-world-e2e-<时间戳>/{alpha,beta}` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |
| real-world | `data/roleplex-real-world-e2e-<时间戳>/default` | `realtest<时间戳>` | `Roleplex-Real-E2E-1` | 5 轮 |
| fake commands | `data/roleplex-command-e2e-<时间戳>/default` | `test<时间戳>` | `Roleplex-Test-1234` | 5 轮 |

fake worlds 与 real-world 的外部工作区按相同 stamp 保存在
`/home/chen/workspace/testworkspace/{roleplex-world-e2e-,roleplex-real-world-e2e-}<时间戳>/`，同样保留最近五轮。
commands 的外部目录为 `/home/chen/workspace/testworkspace/roleplex-command-e2e-<时间戳>/default/`，与 World
共用 stamp；在下一轮开始时保留最近五轮。停止/落库竞态额外使用用例独占、经迁移创建的新数据库。

工作区目录按测试类别、本轮时间戳、用例分层；不得把每个用例的随机目录直接堆在 `testworkspace` 根下。
命令后端测试统一使用 `testworkspace/roleplex-command/test-<时间戳>/case-<随机标识>/`，清理以整轮为单位，
保留最近五轮。旧布局归整时保留时间戳/用例对应关系，仍保留的测试库绑定同步指向新根；历史 execution
快照保持当时路径，不改写执行历史。
此前平铺的命令用例已归档到 `roleplex-command/legacy/test-<原时间戳>/<原随机标识>/`；没有时间戳的
早期夹具放入 `legacy/undated/`，不伪造时间。`legacy/relocations-20260909.json` 保存相对路径映射，
历史归档未删除且不参与新测试的自动淘汰；已按轮分组的旧目录迁入类别目录，沿用按轮保留规则。

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

所有 E2E 默认关闭 trace/video，避免认证字段、DOM 和真实回复泄露；失败截图与经过脱敏的文本诊断可以
进入 artifacts。

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
- E2E 全绿不替代新页面截图和人工查看；
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

- 先选最低且足够证明风险的层级，不把所有逻辑都塞进 Playwright；
- 每轮使用隔离数据库/世界，不能依赖本机开发库；
- 数量断言避免依赖其他 spec 造的数据；
- 安全相关测试做变异验证，确认坏实现会使测试变红；
- 新页面除断言外还要截图查看；
- Provider fake 与真实测试物理、命令和日志分开；
- 真实 Key 只能从环境或 `.env` 进入后端加密边界；
- 不保存 trace/video、完整 Prompt、完整模型输出或原始工具参数；
- 表结构变化必须运行迁移检查；
- 测试完成后先人工验收，再提交。

工程级强约束见 [AGENTS.md](../../AGENTS.md)，各领域的具体关联测试见对应协议文档元数据。
