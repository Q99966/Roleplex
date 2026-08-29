# Roleplex — IM 式多 Agent 群聊协作平台实施计划（架构修订版）

## 当前追加任务：上下文、Prompt Cache、群聊与 Orchestrator（C0-C2 已完成，等待 M4a 指令）

用户于 2026-08-28 决定延期工作项四“会话导出与导入”：必须先补齐统一历史上下文、优化 Prompt Prefix
Cache、实现群聊串行调度和 Orchestrator，并完成阶段式 Checkpoint 与真实缓存验收，再冻结导出格式。

详细范围、依赖、数据边界、测试矩阵和分阶段验收统一见：
[上下文、Prompt Cache、群聊与 Orchestrator 实施计划 v1](context-cache-orchestration-v1.md)。

当前建议主线：

```text
C0 基线 → C1 ContextBuilder → C2 缓存观测 → M4a 群聊 → M4b Orchestrator
→ C3 Checkpoint → C4 真实缓存验收 → 会话导出/导入 → 世界分发
```

Shared Memory 不作为主线前置。未来 Memory v0 只从普通 ORM + Owner 显式确认 + LIKE + 持久注入关系
开始；FTS5、sqlite-vec、Embedding 和自动记忆提取必须另行修订计划。独立计划已于 2026-08-28 获得
用户批准。上下文窗口确定为 Role 级 Owner 可配置能力，默认 200K，前端提供 128K/200K/1M 预设和
自定义；仍按阶段等待明确实施与人工验收指令。

C0/C1 已于 2026-08-29 实现：ContextBuilder 终态历史、角色视角投影、硬预算、稳定 hash、Role 上下文
窗口配置、迁移和预算拒绝均已落地。后端 85 passed、普通 E2E 17 passed、世界 E2E 1 passed、真实
DeepSeek 显式数据库与正常世界两轮历史/缓存验证、前端 build 均通过；已于 2026-08-29 经用户人工验收并
提交 `4ea6312`。

C2 已于 2026-08-29 实现：ContextBuilder schema、稳定层/tool policy hash、历史/裁剪计数和预算诊断已绑定
到 `context.loaded`、Provider 与 generation 日志；Provider usage 支持 cache write 和命中比，fake 不伪造
usage。后端 86 passed、普通 fake E2E 17 passed、fake managed-world E2E 1 passed、真实 DeepSeek
managed-world E2E 1 passed、前端 build 通过。fake 与真实测试都在独立世界目录中完成两轮对话并自动核对
稳定 hash、history `0→2`、usage 和 Prompt 不落日志；已于 2026-08-30 经用户人工验收，M4a 尚未开始。

## 当前追加任务：日志与测试报告 v2（已完成，经人工验收）

### 范围与决策（2026-08-26）

- runtime 按日分 app/agent/access/errors，不因后端/世界重启创建目录；事件使用唯一身份、进程实例和
  进程内观察序号，错误文件保持原事实身份。
- pytest 每轮只追加 summary，全绿不保存应用日志；失败按测试聚合有界脱敏详情，不落 raw nodeid/参数。
- fake/real E2E 每轮独立目录，run ID 贯穿世界切换后的多个后端；summary 记录源码与非敏感环境元数据。
- 日志前一日即按日期/类别归档为 tar.gz，保留 30 天、总量目标 1 GiB；归档/删除必须校验、审计和互斥。
- 错误码建立跨领域注册表；日志只复用稳定大写码，正常取消/拒绝不伪装成 ERROR。

### 实现与验证

- runtime v2 handler、递增不可变轮转、尾部恢复、category/error 副本和进程生命周期已实现。
- pytest reporter、Playwright reporter、脱敏 screenshot/diagnostic 索引和源码 dirty 指纹已实现。
- tar.gz manifest/路径/成员类型/size/SHA-256 校验、30 天/容量淘汰、租约和删除前后审计已实现。
- 受控 pytest/Playwright 失败变异验证已执行并删除探针；发现 trace/错误上下文泄密风险后，所有 E2E
  关闭 trace/video，文本诊断附件改为脱敏后复制，旧探针敏感产物已清理。
- 自动验证：后端 78 passed、普通 E2E 15 passed、世界切换 E2E 1 passed、真实 API E2E 1 passed，
  前端 build 与迁移检查通过。日志目录、字段和清理范围已于 2026-08-27 经用户人工验收；提交仍等待用户
  明确指令。

## 当前追加任务：工作项三 A1 存档机制（已完成，提交 77745cf）

### 开始状态（2026-08-25）

- 工作项一密码策略已完成（`f1479fb`）；工作项二删除语义与测试基建已完成
  （`7ac6944`、`dda0886`、`5246221`）。
- 真实前端 → 后端 → 厂商的独立 E2E 已建立，正常运行默认使用真实 provider，普通回归固定 fake。
- 日志链路、分类轮转、真实/fake E2E 区分、TTFT 与 token usage 已完成
  （`8fe9c04`、`2ce00d1`）。
- 配置、安全、实时事件模块边界已重组（`d551b69`），A1 在 `app/config/` 基础上继续实现。

### 本轮实施切片

1. 先完成交接遗留的 SQLite 迁移外键验证，并在 Alembic 环境中锁定结论。
2. 世界目录约定为 `worlds/{世界名}/`，包含 `roleplex.db`、`.jwt-secret`、`.api-key-secret`、
   `files/` 与世界元数据；`ROLEPLEX_WORLD` 选择世界，显式 `DATABASE_URL` 保持最高优先级。
3. 提供世界 list/create/backup/delete/adopt CLI；备份使用 SQLite 一致性快照并携带密钥和文件。
4. 提供 Owner 世界列表/切换 API 与包装器；非包装器启动时默认拒绝切换。
5. 前端顶栏显示当前世界并支持切换；切换重启后清除 Token、等待健康检查并要求重新登录。
6. 对未知的新 Alembic revision 翻译为可读的“世界来自更新版本”错误，不改写世界数据。

### 验收与验证

- 多世界物理隔离；切换后旧 Token 失效；显式测试数据库路径不受影响。
- 备份可在新目录直接启动；旧世界自动迁移，新世界由旧软件打开时可读失败。
- `python scripts/check_migrations.py`、后端全量 pytest、前端 build、普通与真实 E2E 分层通过。
- 完成后先由用户人工验证，明确同意后才提交。

### 实现结果（已完成并经人工验收）

- 世界目录、独立双密钥、CLI 创建/列表/一致性备份/删除/旧库接管已实现。
- 包装器 alpha→beta 真实重启 smoke 已通过；健康检查世界名变化，alpha Token 在 beta 返回 401。
- 普通显式数据库模式拒绝切换；专用浏览器 E2E 已完成界面切换、后端重启、清 Token 和重新登录。
- 世界/数据库未来版本只读阻断；Alembic SQLite batch 外键前提已写入注释并加入迁移检查。
- 世界活动租约阻止删除正在使用的世界，异常退出后的过期租约可自动回收。
- 自动验证：后端、迁移检查、前端 build、普通 E2E、世界切换 E2E、真实 API E2E 全部通过。

## 当前追加任务：分发形态与账号安全（密码策略、删除语义、存档、导出导入）

### Context

M0 剩余风险验证已完成并提交（commit `c2f070a`）。随后明确了一个此前没有写进计划的**产品形态约束**：Roleplex 要做成**打包发给别人就能直接用的单机 + 局域网工具**，不要求使用者部署 Docker 或数据库服务。这个约束反过来锁定了几个架构选择：

- **继续用 SQLite，不切 PostgreSQL**。切 PG 等于要求每个使用者先装数据库，与"解压即用"冲突；而实测表明 PG 也解决不了我们真正的瓶颈。
- **多存档走"一个世界 = 一个目录"，不在单库里加 `world_id`**。文件分存档天然支持"打包一个世界发给别人"，隔离是物理的，删除和备份都是文件操作；单库多租户则要给根表加字段、所有查询加过滤，且导出单个世界极难做对。
- **写入性能暂不优化**。实测结论：裸 SQLite 每秒可写 1,850（同步刷盘）到 19,531 行，而我们这条链路只有约 100 次提交/秒，瓶颈在"每个流式增量单独提交一次事务"的异步往返开销上，不在 SQLite、不在刷盘、也不在单写者锁。当前单人使用无感，约 5–8 个并发生成会顶到天花板；等真的遇到瓶颈时，用"增量按时间窗口合并提交"即可获得约 10 倍余量。

### 已确认的决策

- 密码策略：**≥10 个字符，且同时包含字母、数字、符号**；符号取 ASCII 可见的非字母数字，空格允许出现但不计作符号。
- 分发出去的世界，Owner 用户名统一为 `owner`、密码为初始弱口令；**登录成功但检测到弱密码时强制跳转重置，不改不能用**（类比公司发新电脑的初始密码）。
- 开发阶段没有真实用户，**不做旧 Token 全员失效**；策略只在下次登录时生效，需要强制下线时再用现成的 `token_version` 机制。
- 删除角色/账号时采用**墓碑**：保留身份信息、清除配置资产，历史消息永远能显示"谁说的"。会话删除进**回收站**，7 天后清理。
- 会话导出为 JSON（单会话一个文件，多选为一个文件夹），**必须能完整导回**。
- 世界分发与会话导出**两条线并存**：前者粗粒度（整个世界），后者细粒度（某几个对话）。
- 分发包中的模型 Key **原样保留**（不做导出口令加密），是否保留由导出者选择。
- 非 Owner 账号在分发时提供三个选项：清掉 / 不清 / **转成 Agent 角色**（用其历史消息生成角色形象）。
- 隐私范围当前只关注 API Key，聊天内容脱敏推迟。

### 工作项一：密码策略与强制重置（已完成，提交 f1479fb）

1. 新增密码策略模块作为**唯一权威**：长度、字符类别、以及 bcrypt 的 72 字节上限（实测 bcrypt 4.2.1 对超长密码**静默截断**，80 字符与其前 72 字节等价，必须在策略层拦住）。字符数按字符计，字节上限按 UTF-8 字节计。
2. 注册与改密走同一套校验，失败返回稳定错误码而不是笼统的参数校验错。
3. 登录**不拒绝**弱密码（分发出去的世界必须能登进来才能改密），而是在签发的 Token 中打标记，并在登录响应体显式返回一个字段供前端使用（前端不解 JWT）。
4. 拦截采用**默认拒绝**：当前用户依赖内部直接拒绝带标记的 Token，仅"改密"和"查看本人资料"两个接口显式放行，新增路由自动受保护；WebSocket 首帧认证同样拒绝。
5. 新增改密接口：校验旧密码 → 校验新密码合规 → 落库 → 递增 Token 版本使旧 Token 失效 → 返回新 Token。新旧密码相同时拒绝（否则"改成同一个"可以绕过强制重置）。
6. 前端：登录后若带标记则强制进入重置页，进不了工作台；注册与重置表单实时展示四项要求（长度、字母、数字、符号）的达成情况，超出字节上限时额外提示；现有"至少 8 位"提示同步更新。
7. 连带：测试口令统一换成合规的固定占位值；README 中的测试账号说明同步。
8. 认证协议从 `docs/protocol.md` 拆出为独立领域文档（新增策略、新接口和三个错误码后，该领域已具备独立生命周期）。

**验收**：注册弱密码被拒且错误码稳定；弱密码账号登录后除改密外全部接口被拒（含 WebSocket）；改密后旧 Token 失效、标记消失；浏览器端弱密码注册被拦并给出可读提示。

### 工作项二：删除语义（墓碑与回收站）（已完成，提交 7ac6944）

1. 角色删除改为墓碑：保留 id、名称、头像与删除时间，清除系统提示词、模型绑定、技能与 MCP 配置；名称加删除标记以释放"同一 Owner 下角色名唯一"的约束，允许立刻新建同名角色。
2. 会话删除进回收站（软删除 + 删除时间），列表中不再出现；清理时才真正级联删除。
3. 前端对已删除的发送者做降级展示，成员列表不再出现指向已删除角色的孤儿项。
4. 需要迁移：角色与会话新增删除时间字段；同步更新数据模型文档。

**验收**：删除角色后其历史消息仍显示原名称并标注已删除；删除会话可在保留期内恢复；保留期后清理只删除未被引用的数据。

### 工作项三：A1 存档机制（一次一个世界，界面可切）（已完成，提交 77745cf）

1. 目录结构：每个世界一个目录，内含数据库与**该世界独立的两个密钥文件**。Token 密钥必须按世界隔离，否则 A 世界签发的 Token 会在 B 世界命中同 id 的用户造成越权；Key 加密密钥必须跟随世界，否则世界搬迁后模型配置全部解不开。
2. 配置解析：新增世界名环境变量，数据库地址与密钥路径由世界目录推导；直接指定数据库地址的方式保留最高优先级，测试与端到端不受影响。
3. CLI：列出、创建、备份（一致性快照 + 密钥一并打包）、删除，以及把现有开发库连同密钥迁入默认世界的接管命令。
4. 界面切换：新增启动包装器进程，后端收到切换请求后写入目标世界并优雅退出，由包装器用新世界重启；前端轮询健康检查直到世界名变更，然后要求重新登录（跨世界必然重新登录，这是隔离生效的表现）。**未通过包装器启动时该接口必须拒绝**，否则会把服务停掉起不来。
5. 可见性：启动日志、健康检查与前端顶栏都要显示当前世界名。
6. 世界与软件的版本兼容（2026-08-20 定）：采用**向前自动迁移、向后阻断**，不要求版本严格对应。世界版本 ≤ 软件版本时启动照常 `alembic upgrade head` 自动升级；世界版本 > 软件版本时拒绝启动并给出可读提示。当前行为已实测：用旧代码打开被标记为更新版本的库会抛 `CommandError: Can't locate revision identified by '…'`，失败是安全的（不动数据、不写坏世界），但这条信息对拿到分发包的人毫无意义，必须翻译成"这个世界来自更新版本的 Roleplex，请升级后再打开"。

**验收**：能创建多个世界并在界面上切换；切换后必须重新登录；A 世界的 Token 在 B 世界失效；备份出的目录可在另一台机器上直接启动；旧世界被新版本自动升级后可正常使用，新世界被旧版本打开时给出可读的版本提示而不是堆栈。

### 工作项四：会话导出与导入（延期，等待上下文/群聊/编排稳定）

本工作项由 2026-08-28 的计划修订延期。解锁条件和新的前置顺序见
[上下文、Prompt Cache、群聊与 Orchestrator 实施计划 v1](context-cache-orchestration-v1.md)。以下内容保留
作为导出目标，不代表当前可以开始编码。

1. 导出：单会话一个 JSON；多选导出为一个文件夹，内含多个 JSON 与一份清单（格式版本、导出时间、会话列表、来源实例标识）。
2. 内容：会话元信息、成员、消息（parts、状态、版本、链路标识、时间、发送者）。事件日志不导出，导入后重新编号。
3. 角色随会话导出为快照，**是否包含系统提示词由开关控制，默认包含**；模型配置不导出，导入后角色置为停用直到绑定配置。
4. 导入：一律新建会话（不支持合并进已有会话），重映射消息标识、角色引用与用户引用；原真人发言映射到不可登录的占位身份并保留原昵称；导入后的会话归导入者所有。

**验收**：导出再导入后，消息顺序、引用关系、角色归属与显示名称都与原会话一致；跨实例导入不产生悬空引用。

### 工作项五：世界分发

1. 导出世界时把 Owner 用户名统一为 `owner`、密码置为初始弱口令，并生成导入说明；接收方首次登录必被强制改密（复用工作项一）。
2. 模型 Key 两种模式：清空并标记待填写（社区分享）/ 原样保留（信任的人）。清空模式必须让接收方在界面上被明确引导补填，而不是等到发消息才失败。
3. 非 Owner 账号三选项：清掉 / 保留 / 转成 Agent 角色。
4. Agent 化选项的实现要点：历史过长需要摘要或采样（受上下文预算约束）；每个成员一次模型调用，必须走异步任务并有进度反馈；生成失败降级为占位角色；界面必须明确告知"将使用该成员的历史消息生成角色形象"且**默认不勾选**。

**验收**：分发出的世界在另一台机器上解压即可启动，首次登录被强制改密；选择清空 Key 时界面明确提示补填；Agent 化选项在告知与确认后才执行。

### 实施顺序

一（密码）→ 二（删除语义）→ 三（存档）已完成。后续顺序已修订为：上下文与缓存基础 → 群聊 →
Orchestrator → Checkpoint/真实缓存验收 → 四（导出导入）→ 五（世界分发，其中 Agent 化最后实施）。
工作项四不再视为与群聊/编排无关；第五仍依赖一、三、四。

### 验证命令

- `python -m compileall backend/app backend/alembic backend/scripts backend/tests`
- `pytest tests -q`（普通回归，不联网、不计费）
- `pytest tests/contract -m contract -q`（配置凭据后手动执行）
- `python scripts/check_migrations.py`（工作项二会新增迁移）
- `npm run build` 与 `npm run test:e2e`

### 未决问题（做到对应工作项时再定）

- 回收站覆盖范围：**已定**（2026-08-20）只覆盖会话；角色走墓碑已不丢数据，模型配置不纳入。
- 保留期清理的触发方式：**已定**（2026-08-20）只在启动时清理一次，不起常驻任务、不引入调度框架。理由：Roleplex 是随开随关的单机工具，启动清理已覆盖绝大多数场景；`asyncio.sleep` 走单调时钟，Windows 休眠时不推进，定时任务会持续漂移，为尚未出现的"长期不关机"场景付出的成本不划算。过期数据留在盘上不影响功能，下次启动即清。
- 会话导出是否包含产物内容：产物尚未接入产品链路，建议先预留字段不导出实际内容。

## 当前追加任务：补齐 M0 剩余风险验证（已完成，提交 c2f070a）

### Context

M2 单聊闭环已完成并提交（commit `48ea5e4`）：消息 REST、生成状态机、事件先落库再广播、WS 首帧认证与断线恢复、前端实时聊天、Alembic 迁移体系与迁移可重放性检查、后端 7 个 pytest 与 8 个 Playwright 用例全绿。

但 M2 是用"直接产出字符串片段的 fake provider"打通的，M0 计划中的四项风险验证仍未做：真实 provider 与 LangGraph 防腐层、取消传播、Windows stdio MCP 生命周期、iframe CSP 隔离。这些都是会反向影响架构的风险点——M3 的图片多模态、M4 的工具循环与 Orchestrator、M5 的 MCP 接入都建立在它们的结论上，越晚验证返工面越大。本阶段只做风险验证与最小垂直切片，不做产品功能。

已实测确认的前置事实：`requirements.txt` 已精确锁定 langgraph 0.2.74 / langchain-core 0.3.36 / langchain-anthropic 0.3.4 / langchain-openai 0.3.1 / mcp 1.2.1 / psutil 6.1.1，与本机环境一致；自写的 `BaseChatModel` 子类（实现 `bind_tools`、`_stream` 产出 `AIMessageChunk` 与 `tool_call_chunks`）能离线驱动 `create_react_agent` + `astream_events(version="v2")` 产出 `on_chat_model_stream` / `on_tool_start` / `on_tool_end`，因此事件映射可以不联网、不计费地测穿；`langchain_core` 自带的 `GenericFakeChatModel` 没有 `bind_tools`，不能直接用。mcp 1.2.1 的 `stdio_client` 直接 `anyio.open_process([command, *args])`，**不做任何 Windows `.cmd` 解析**，其 `get_default_environment()` 只继承 PATH/APPDATA/SYSTEMROOT 等 11 个变量，不含 `PYTHONIOENCODING`/`COMSPEC`/`PATHEXT`——这正是 spike 要解决的问题。

### 已确认的范围决策

- 真实厂商验证**现在就配 Key 实跑一次**：契约测试从环境变量读取凭据，`backend/.env`（已 gitignore）或 shell 环境变量二选一；普通 pytest 不受影响。
- MCP 验证用**离线 stub server + 生成的 `.cmd` 包装器**作为确定性回归测试，**另加一条默认跳过的真实 `npx` 用例**供手动验证首次拉包超时与真实 `npx.cmd`。
- **现在就把 `chat.py` 改造为消费统一领域事件**，fake provider 与真实 provider 走同一接口；M2 的 pytest 与 Playwright 用例作为回归护栏。
- iframe CSP 验证**只做 artifact 原始内容读取端点 + 安全头与授权测试**，不做 artifact 创建/更新接口和前端预览（那是 M3），文档状态标为原型。

### 推荐实施顺序

#### 一、Provider 与防腐层（最高优先，其余都依赖它）

1. 新增 `app/agent/domain.py`：领域事件不可变数据类 `TextDelta` / `ToolCallStarted` / `ToolCallFinished` / `MessageDone` / `ProviderError`，以及统一联合类型。这是业务层唯一可见的 Agent 事件词汇表。
2. 新增 `app/agent/providers.py`：按 `Role` + `ModelConfig` 构造 `ChatAnthropic` / `ChatOpenAI(base_url=...)`；内置能力表（vision / parallel_tools / usage 口径）+ `capability_overrides_json` 覆盖；参数按"模型+模式"白名单过滤，保存期校验与运行期剔除分开；API Key 只在本模块经 `security.decrypt_api_key` 进入 provider，日志只留元数据。
3. 新增 `app/agent/loop.py`：按角色动态构造 `create_react_agent`，`recursion_limit≈15`，消费 `astream_events(version="v2")` 并转换为领域事件。**框架私有事件名只允许出现在本文件**；触顶时追加一次禁用工具的收尾调用，保证有文本收尾、无未配对 tool_use。
4. 改造 `app/agent/fake_provider.py`：从"产出字符串"改为脚本化的 `BaseChatModel` 子类（`bind_tools` 返回自身，`_stream` 产出分片，可脚本化工具轮），走与真实 provider 完全相同的 `loop.py` 路径。**必须保持现有回复文案不变**，M2 的 Playwright 断言依赖它。
5. 改造 `app/services/chat.py`：`_run_generation` 从消费字符串改为消费领域事件——`TextDelta` 进现有增量路径，`MessageDone` → completed，`ProviderError` → failed + 稳定错误码，工具事件落审计表。**不新增公开 WS 事件类型**，本阶段协议不变。

#### 二、工具危险分级与执行层拦截

1. 新增 `app/agent/tools.py`：`safe` 是显式白名单常量；未知工具、MCP 工具、有副作用的内置工具一律 dangerous；分级判断与拦截发生在工具执行层，不依赖提示词或工具名自述。
2. Guest 触发链路调用 dangerous 工具时，返回结构化的"被拒绝"结果回传模型，而不是抛异常中断整轮。
3. 工具开始/结束写入 `tool_calls` 审计表（触发用户、角色、工具名、参数摘要、状态、耗时），参数摘要不得包含凭据或敏感原文。

#### 三、取消传播

1. 停止生成时取消 Agent 任务，验证：UI 状态立即停止、已缓冲内容以 `stopped` 落库、工具调用审计记录收尾、正常取消不记为 ERROR。
2. 记录 `stop_requested_at` 与 `ended_at` 的差值作为取消传播耗时的观测口径；上游模型流与工具调用的实际中断是尽力而为，不承诺计费即刻停止，结论写进 spike 文档。

#### 四、Windows stdio MCP 生命周期（不接产品页面）

1. 新增 `app/mcp/manager.py`：每个 server 一个常驻宿主 Task，在同一个 Task 内 `async with` 打开 `stdio_client`/`ClientSession` 并消费请求队列（Future 回传）；关闭即取消宿主 Task，保证 anyio cancel scope 同进同出。
2. 启动前用 `shutil.which()`（配合 `PATHEXT`）解析真实可执行路径，Windows 下不假设 `.cmd` 由底层自动解析；显式合并 `get_default_environment()` 与运行必需的编码/系统变量；启动日志不含凭据。
3. 超时分层：`initialize` 长超时（首次拉包分钟级）、`call_tool` 60 秒；超时、取消与关闭时用 psutil **杀进程树**，不能只释放 Python 对象。
4. 池 key = `(owner_id, 配置 hash)`，空闲回收，服务关闭时统一关停。
5. 保持 Windows 默认 Proactor 事件循环，严禁设置 `WindowsSelectorEventLoopPolicy`。

#### 五、iframe CSP 与 Sec-Fetch-Dest 隔离

1. 新增 `app/routers/artifacts.py`：`GET /api/artifacts/{id}/versions/{v}/raw`，先做会话成员级授权（非成员按 404），再按 `kind` 决定 Content-Type。
2. 响应固定携带 `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors 'self'` 与 `X-Content-Type-Options: nosniff`。
3. `Sec-Fetch-Dest != iframe` 时返回 `Content-Disposition: attachment`，防止顶层直开导致同源下的 Token 泄露。
4. 不新增表或字段（`artifacts` / `artifact_versions` 已在 0001 迁移中），因此本项不产生迁移。

#### 六、真实厂商契约测试（需要你提供 Key）

1. 新增 `backend/tests/contract/`：凭据从环境变量读取，缺失时整层 skip；`conftest.py` 额外解析 `backend/.env`（已 gitignore）并只在环境变量缺失时补齐，不引入新依赖。
2. 覆盖流式增量、工具调用往返、取消传播、错误格式与 usage 口径；每个厂商一组，失败信息脱敏，禁止打印 Key、Authorization 头或完整响应体。
3. 结论（能力差异、参数兼容性、错误码映射）回写到内部协议文档与能力表默认值，而不是散落在测试断言里。

#### 七、文档与状态同步

1. 新增 `docs/protocol/internal/agent-runtime.md`：领域事件目录、防腐层边界、工具危险分级与审计、MCP 生命周期与超时约定，状态标注为原型。
2. 新增 `docs/protocol/public/resources/artifact-raw.md`：raw 端点、安全头、授权与降级行为，状态标注为原型。
3. 更新 `docs/protocol.md` 索引、README 当前阶段（M0 风险验证结论）、`docs/protocol/internal/data-model.md` 中 `tool_calls` 的状态（从预留改为已写入）。

### 验收标准

- 离线确定性测试能证明：LangGraph 事件被完整映射为领域事件，业务层代码中不出现任何框架私有事件名；工具轮的开始/结束/结果都有对应领域事件与审计记录。
- 危险工具在执行层被拦截，且拦截不依赖系统提示词或工具名；Guest 触发链路的拒绝结果能回传模型并让本轮正常收尾。
- 取消一次生成后：消息落库为 `stopped`、审计记录收尾、日志按取消而非 ERROR 记录，且能从日志链路追到具体 run。
- MCP stub server 可启动、可调用、可超时、可取消；宿主 Task 取消后进程及其子进程树全部消失（psutil 校验），`.cmd` 包装器路径下同样成立。
- artifact raw 端点：成员可读且带完整 CSP 头，非成员 404，`Sec-Fetch-Dest` 非 iframe 时被强制下载。
- 配 Key 后契约测试可实跑并产出结论；不配 Key 时普通 pytest 全绿且不联网。
- M2 既有 7 个 pytest 与 8 个 Playwright 用例在改造后仍全绿（防腐层改造的回归护栏）。

### 关键文件

- 新增：`backend/app/agent/domain.py`、`providers.py`、`loop.py`、`tools.py`；`backend/app/mcp/manager.py`；`backend/app/routers/artifacts.py`
- 改造：`backend/app/agent/fake_provider.py`、`backend/app/services/chat.py`、`backend/app/main.py`（路由装配）
- 测试：`backend/tests/test_agent_loop.py`、`test_tool_permissions.py`、`test_agent_cancel.py`、`test_mcp_manager.py`、`test_artifact_raw.py`、`backend/tests/mcp_stub_server.py`、`backend/tests/contract/`
- 文档：`docs/protocol/internal/agent-runtime.md`、`docs/protocol/public/resources/artifact-raw.md`、`docs/protocol.md`、`README.md`、`docs/protocol/internal/data-model.md`

### 验证命令

- `python -m compileall backend/app backend/alembic backend/scripts backend/tests`
- `pytest tests -q`（普通回归，不联网、不计费）
- `pytest tests/contract -q`（配 Key 后手动执行）
- `python scripts/check_migrations.py`（确认本阶段未引入未迁移的 schema 变更）
- `npm run build` 与 `npm run test:e2e`（确认防腐层改造没有破坏 M2 用户可见行为）

### 风险点

- 防腐层改造动的是刚提交的 M2 生成链路，必须靠 M2 既有用例回归；fake 回复文案是 Playwright 断言依赖，不能改。
- `create_react_agent` 在 0.2.74 的默认 `version="v1"`，事件形态与 `v2` 不同，必须在防腐层内锁定并测穿，不能让业务层感知差异。
- Windows 下 `.cmd` 包装器会额外派生子进程，只 terminate 直接子进程会留下孤儿；进程树清理必须有测试覆盖。
- 契约测试涉及真实凭据，必须确保 `.env` 不进仓库、日志与失败输出全程脱敏。

## 当前追加任务：进入 M2 单聊闭环（已完成，提交 48ea5e4）

### Context
M0/M1 基础骨架、认证权限、模型配置/角色/会话基础管理和前端工作台已完成，Gemini 已进一步优化前端视觉与管理弹窗。当前 `ActiveWorkspace` 仍使用本地模拟消息和模拟成员，输入框是禁用占位；后端已有消息模型、事件中心原型和 LangGraph 依赖，但尚无消息路由、Agent 循环或 WebSocket 路由。下一阶段应先完成 M0 风险 spike 的最小验证，再分阶段实现 M2 单 Agent 单聊闭环，避免把群聊、MCP 和 Orchestrator 同时引入。

### 推荐实施顺序

#### M0 风险验证先行
1. 建立确定性 fake provider 和 provider 统一领域事件接口，验证文本流式、工具调用事件、错误映射、取消传播；真实厂商只做可选契约测试。
2. 验证 LangGraph 版本和 `astream_events` 的事件映射，明确防腐层只向业务输出 `text_delta`、`tool_call`、`message_done`、`error` 等领域事件。
3. 建立 Windows stdio MCP 最小 spike：保持 Proactor，解析 Windows 可执行路径，验证环境继承、初始化/调用超时、取消和进程树清理；不接入产品页面。
4. 将 EventHub 从“仅内存广播原型”收敛为最小垂直切片：事件先以会话 `event_seq` 持久化，再发布到内存订阅者；验证首帧认证、断线回放、epoch 变化快照、事件幂等和停止竞态。后续可再引入完整 `event_log` 表，但不能把内存环当作可靠恢复来源。
5. 形成可复现的 fake provider、MCP mock server 和 M0 pytest 集成测试；所有 spike 必须有明确通过标准，先通过风险验收再开始消息产品链路。

#### M2-A 消息持久化与 REST
1. 为 `messages` 增加消息创建、历史查询和生成状态所需的服务层；落实资源级会话成员授权、排序键、revision、chain_id 和 client idempotency。
2. 新增消息领域协议文档（按模块化协议规则放入 `docs/protocol/public/messaging/`），同步 Pydantic schema、前端类型和 pytest/httpx 测试。
3. 在消息生成前补充最小消息/生成/队列状态模型或等价持久化结构，明确 generation 的 stream epoch、状态、run 关联和会话 revision；是否单独建立 event_log/queue_jobs 表由 M0 spike 的垂直切片结果决定，但不得只依赖内存状态宣称可靠恢复。
4. 实现单会话串行调度器骨架：同一会话保证触发顺序，不把会话锁当作全库写锁；不同会话可以并行。
5. 加入结构化链路上下文和日志基础，使消息请求、会话、chain、run、父子 Agent 执行可以关联。

#### M2-B Provider 与 Agent 防腐层
1. 按角色模型配置构造 LangChain provider；参数能力按模型/模式校验，API Key 只从解密边界进入 provider，日志脱敏。
2. 动态构造最小单 Agent LangGraph React 循环；工具先只接显式 safe 内置工具，危险/未知工具执行层拒绝。
3. 将框架事件转换为项目领域事件，业务层不得直接处理 LangGraph 私有事件名；fake provider 与真实 provider 使用同一领域事件接口。
4. 实现文本流式消息的内存快照、节流持久化和最终 `done/error/stopped` 状态；不逐 token 写库。

#### M2-C WebSocket、停止与恢复
1. 新增 WebSocket 路由和首帧认证，不把 Token 放入查询参数；按会话订阅并检查资源权限。
2. 实现 `stream_epoch + event_seq + message revision + delta_seq` 的幂等事件协议：所有创建、增量、part 更新、完成、错误和状态变化都可恢复。
3. `subscribe(after_event_seq)` 先原子注册并返回 backlog；epoch 变化或 backlog 截断时发送完整会话快照。
4. 使用 `run_id → asyncio.Task` 管理停止生成：先让 UI 状态立即停止，再尽力取消上游模型流、工具调用和后续 chain；取消结果落库为 stopped，不把正常取消当 ERROR。
5. 增加 WS 事件、断线重连、重复/乱序/重启快照和取消传播的 pytest 集成测试。

#### M2-D 前端真实单聊
1. 将 `ActiveWorkspace` 的 `mockChatFeed` 和模拟成员逻辑替换为后端消息、真实会话成员和消息状态；保留 Gemini 的视觉布局，不保留模拟内容。
2. 将禁用输入框改为真实文本输入、发送、生成中状态、停止生成和错误提示；消息 part 渲染先覆盖文本/代码/工具状态占位。
3. 新增前端 WS client/store：首帧认证、订阅、事件幂等应用、断线重连和快照重建；会话切换时取消旧订阅。
4. API 与协议文档按领域拆分，README 只更新用户可见的 M2 当前阶段，不复制字段。

### M2 验收标准
- fake provider 驱动的单聊可以从发送到最终完成，文本按流式增量显示，消息最终落库。
- 真实前后端 Playwright 测试覆盖：打开单聊、发送消息、看到流式回复、停止生成、模型错误提示和页面无 console/page error；普通 E2E 不消耗真实模型 Key。
- pytest/httpx 覆盖消息成员授权、消息幂等、revision 冲突、会话串行和 fake provider；WS 集成覆盖断开重连后事件不丢不重、epoch 改变快照恢复、重复事件幂等和 stopped 状态。
- 日志能按链路从消息请求追查到 Agent run、模型事件、落库和最终状态，敏感字段不出现在日志。
- 只有 M2 验收通过后，才进入 M3 富媒体或 M4 群聊/Orchestrator；MCP 产品接入继续放在 M5。

### 关键文件
- 当前前端复用：`frontend/src/components/ActiveWorkspace.tsx`、`frontend/src/store/app.ts`、`frontend/src/api/client.ts`、`frontend/src/components/Modals.tsx`
- 后端新增/修改：`backend/app/models.py`、`schemas.py`、`routers/messages.py`、`ws.py`、`agent/providers.py`、`agent/loop.py`、`agent/fake_provider.py`、`agent/scheduler.py`、`events.py`
- 测试：`backend/tests/`、`frontend/tests/m2-chat.spec.ts`、`docs/protocol/public/messaging/`、`docs/protocol/public/websocket/`

### 验证命令
- `python -m compileall backend/app backend/alembic`
- `pytest backend/tests -q`
- `npm run build`
- `npm run test:e2e`（使用 fake provider 和全新测试数据库）

## 当前追加任务：模块化接口协议文档

### Context
当前 `docs/protocol.md` 同时承载认证、错误、消息、WebSocket、邀请、Artifact、分页和兼容性，随着工程增长会变成难以维护的单一大文件。用户担心 `CLAUDE.md` 将其描述为“固定唯一接口文档”后，后续 Agent 会误以为所有接口必须继续堆进同一个文件。

### 推荐实现
- 保留 `docs/protocol.md` 作为稳定入口，但将其职责收敛为协议索引、通用总则、版本/兼容性规则、状态标签和文档维护流程；不要求所有具体接口长期放在该文件。
- 领域协议按复杂度和稳定边界拆分到 `docs/protocol/`，优先按领域而不是按单个接口切碎。建议目录：
  - `public/rest/`：认证、模型配置、角色、会话等 REST 契约
  - `public/messaging/`：消息与消息幂等
  - `public/websocket/`：连接、订阅恢复、公开事件目录
  - `public/cross-cutting/`：错误、分页、兼容性等横切协议
  - `public/resources/`：邀请、Artifact 等资源契约
  - `internal/`：领域事件、Agent/Orchestrator、MCP、审计等仅服务内部约定
  - `testing/`：协议/厂商契约测试规则
- 领域文档统一包含元数据（受众 public/internal、状态 implemented/prototype/proposed/deprecated、协议版本、维护者、事实来源、复核日期）、范围、请求/响应或事件、鉴权/资源归属、幂等/版本/并发、错误、降级行为、实现状态、关联代码和测试。
- 版本按领域优先，不因兼容新增字段复制完整 v1/v2 文档；只有不兼容并行协议才建立版本目录或新协议入口，并提供迁移说明。
- 公开协议只记录客户端可依赖的 wire contract；内部协议不承诺客户端兼容性。框架私有事件必须先经过防腐层转换，不能直接成为公开 WS 事件。
- Agent 修改接口时先读取 `CLAUDE.md`、`README.md` 当前阶段、`docs/protocol.md` 索引，再从路由装配、schema、客户端类型、错误码和测试反查相关领域文档；只修改对应领域权威页，最后更新索引链接和实现状态。
- 新增公开 API 或事件必须有稳定 contract ID、对应 schema/客户端类型/测试和领域文档；README 只同步用户可见能力，不复制协议字段。
- 协议文档必须明确“已实现/原型/预留”；当前 `docs/protocol.md` 中尚未实现的消息、WebSocket、邀请、Artifact 和分页内容，在拆分时保留为预留状态，不能误标为可用。
- 禁止 `docs/protocol.md` 重新膨胀为完整 endpoint/event 清单；当索引超过可扫描范围或某领域出现独立生命周期时，必须拆出领域文档。

### Critical files
- `D:\chenzhihan\Code\AI\Roleplex\CLAUDE.md`
- `D:\chenzhihan\Code\AI\Roleplex\docs\protocol.md`
- 后续新增的 `docs/protocol/public/`、`docs/protocol/internal/` 和 `docs/protocol/testing/` 领域文档

### Verification
- 确认 `CLAUDE.md` 表述为“协议文档体系”，不再暗示单一文件承载全部接口。
- 确认 `docs/protocol.md` 保留入口和总则职责，具体协议可按领域拆分。
- 确认规则包含 public/internal、implemented/prototype/proposed/deprecated、版本兼容和 Agent 变更发现流程。
- 本次只调整文档维护规则，不迁移现有协议内容；领域文件在对应接口实现或下一次协议整理时按目录逐步拆分。

## 当前追加任务：补齐跨任务工程硬约束

### Context
外部评估确认当前 `CLAUDE.md` 对注释、日志和浏览器测试约束较完整，但遗漏了计划中最关键的长期安全与一致性规则。需要把不会因业务字段或实现替换而失效的约束补入 `CLAUDE.md`，让后续 Agent 在数据库迁移、并发写入、MCP 工具、Windows 运行时、日志链路和测试分层方面有明确边界。

### 推荐实现
- 补充文档优先级：`CLAUDE.md` 规则、`README.md` 项目事实、`docs/protocol.md` 对外协议、计划文档架构决策和验收；明确 P2 未经计划更新不得实现。
- 补充 SQLite→PostgreSQL 可移植纪律、Alembic 迁移要求和单进程 SQLite 边界。
- 补充分层测试要求：后端权限/集成/并发测试、mock Agent 调度测试、WS 事件恢复测试、Playwright 用户流程、可选的真实厂商契约测试；说明 M0 风险 spike 不要求浏览器流程，E2E 使用确定性 fake provider。
- 补充 Windows Proactor、MCP 子进程启动环境、`npx.cmd` 解析和进程树清理硬约束。
- 补充全库单 writer、短事务、锁重试、单 reducer、revision 乐观锁和流式内存快照纪律。
- 补充工具安全不变量：safe 只允许显式白名单，MCP/未知/有副作用工具默认 dangerous，必须在执行层拦截。
- 补充 LangGraph 事件到领域事件的防腐层边界。
- 扩展日志链路字段要求，覆盖事件 epoch/序号、消息 revision、增量序号和工具审计关联；明确日志级别、流式日志采样和受控 debug 例外。
- 保持中文注释要求和现有文档职责，不复制 README 或协议具体字段。

### Critical files
- `D:\chenzhihan\Code\AI\Roleplex\CLAUDE.md`
- `C:\Users\chenzhihan\.claude\plans\nested-watching-crown.md`

### Verification
- 检查 CLAUDE.md 不重复 README 的环境/启动事实或 protocol 的完整字段定义。
- 检查规则覆盖数据库、测试、运行时、并发、安全、架构边界和日志链路。
- 后续代码实现前，以新增规则作为 M1/M2 评审清单。

### Context
M0/M1 功能已经通过构建、API smoke test 和 Playwright 浏览器测试，但基础代码中部分函数、核心数据层和状态/协议边界缺少符合 `CLAUDE.md` 的职责与参数说明。用户要求在提交前补齐本版代码注释，同时避免给简单 JSX 和显而易见的函数添加噪声注释。

### 推荐实现
- 为 `backend/app` 的配置、数据库生命周期、认证安全、事件恢复、路由关键业务函数和核心 ORM/schema 类补充简洁 docstring，重点说明参数、返回值、所有权、并发、加密、事件序列和恢复边界；不逐字段解释普通 `mapped_column`。
- 为 `frontend/src/api/client.ts`、`frontend/src/store/app.ts` 和 `frontend/src/App.tsx` 的导出函数、状态编排函数及主要组件补充 JSDoc/TSDoc；不为简单类型、JSX 样式和单行回调重复注释。
- 保持实现行为不变，不把注释写成具体字段的长期工程规则；当前功能事实放 README，接口事实放 `docs/protocol.md`。
- 检查现有注释与实现一致，删除或改写过时注释；注释不得包含凭据、隐私或临时值。

### Critical files
- `backend/app/config.py`, `db.py`, `security.py`, `events.py`, `models.py`, `schemas.py`
- `backend/app/routers/auth.py`, `roles.py`, `model_configs.py`, `conversations.py`
- `frontend/src/api/client.ts`, `store/app.ts`, `App.tsx`

### Verification
- `python -m compileall backend/app backend/alembic`
- `frontend`: `npm run build`
- `frontend`: `npm run test:e2e`，确认注册、登录、错误和权限测试仍通过
- 检查 diff，确保只增加说明性注释，不改变 API payload、认证逻辑或 UI 行为。

## 一、背景与目标

**要解决的问题**：需要一个"像 IM 一样"的多 Agent 协作工具——用户自定义角色（各自绑定模型厂商+Key、System Prompt、技能、内置工具、MCP servers），角色像联系人一样展示（头像/名称/能力标签）；与角色 1v1 单聊，或建群让多个角色协作：@ 指定角色依次回复，或开启**可选的** Orchestrator 主 Agent 自动拆解任务并行分派、失败降级、聚合汇报；群聊可邀请真人加入。消息支持富媒体产物内联（代码、图片、文件、网页 iframe 预览卡、工具调用过程），完整历史作为上下文，支持 pin 关键消息、引用、重新生成。

**现状**：`D:\chenzhihan\Code\AI\Roleplex` 为空目录，全新项目。技术栈沿用同目录 **AgentHub** 的熟悉栈（React+Vite+Tailwind / FastAPI+SQLAlchemy+WebSocket+JWT），代码全部新写。

**已确认决策**：
1. **数据库**：SQLite 起步 + 可移植纪律（见 4.1）+ **明确的 PG 切换阈值**（见 5.3）
2. **部署发布**（agent 代码容器化运行+预览链接+状态卡）：**全部留 P2**，仅在 schema/parts 协议预留
3. **编排层**：**LangGraph 混合**——模型统一/react 循环/并行用 LangGraph 生态；IM 域逻辑（调度/上下文编码/WS 协议/MCP 生命周期）自研；框架事件与领域事件隔离（防腐层）
4. **信任模型**：**单机可信 Owner 平台**，非多租户 SaaS（见 §二，本次修订新增）

**本期不做（P2 预留）**：容器化部署、Diff 一键应用、版本历史 UI、对话式局部修改、PPT 预览、多文件产物项目。

**参考实现**（借鉴模式，不复制代码；AgentHub 的 WS 客户端为直接拼 token，不作为可靠续传范本）：
- `AgentHub/backend/app/core/db.py` — SQLAlchemy 2 声明风格、自增 id 排序键
- `AgentHub/backend/app/websockets/client.py` — 多方对话"[昵称]:"上下文拼接
- `AgentHub/frontend/src/contexts/ChatContext.jsx` — 前端单 WS 分发模式（仅结构参考）

## 二、信任与安全模型（M0 定稿，决定 M1 schema）

**定位**：Roleplex 是运行在 Owner 本机上的**个人协作服务**（类比：局域网《我的世界》联机——Owner 是房主，Guest 是受邀加入的玩家；不是多租户"大型网游"）。首个注册账号为 Owner，拥有角色、模型配置、API Key 和 MCP 的管理权限；其他真人通过邀请码作为 Guest 加入特定会话，只能访问该会话内的消息/附件/产物；**所有 Agent 推理消耗 Owner 的 Key**。配置 MCP 等价于授予本机代码执行权，**仅 Owner 可配置**。

**Owner 原子初始化（消除并发注册竞态）**：`instance_settings` 单行表持有 owner_user_id，注册事务内原子抢占（唯一/单行约束保证并发注册只产生一个 Owner）；`is_owner` 不接受任何客户端提交；Owner 账号不可删除；Owner 密码重置走本机 CLI 脚本（能物理接触机器=可信）。

**JWT 生命周期**：签名密钥首次启动生成并持久化到 data/；token 有效期 7 天；`users.token_version` 支持强制失效（改密/登出全部设备时自增，token 内携带版本比对）。

**权限矩阵**：

| 资源/操作 | Owner | Guest |
|---|---|---|
| 模型配置 / 角色 / MCP 配置 CRUD | ✓ | ✗（API 层不可见） |
| 所在会话消息读取 / 发言 / 产物与附件读取 | ✓ | ✓（仅所在会话，资源级校验） |
| 触发普通角色回复（@ / 单聊） | ✓ | ✓（受每日 chain 配额+速率限制） |
| 触发含**危险工具**角色的 chain | ✓ | **默认拒绝**；会话级白名单可放开为"需 owner 确认" |
| 邀请 / 成员管理 / 删群 / 会话设置 | ✓ | ✗ |

**工具分级与拦截（默认封闭）**：safe 是**显式白名单**——仅内置只读/生成类工具（create/update/read_artifact）；**所有 MCP 工具（stdio 与 HTTP）、未来新增及未知工具一律默认 dangerous**，分类不依赖工具名或模型自述，Owner 可在角色配置中将具体 MCP 工具显式标记为 safe。guest 触发的 chain 在**我方工具执行层**统一拦截 dangerous 调用，不依赖 prompt 约束；owner 确认流具有 `pending/approved/rejected/expired` 状态，超时（默认 5 分钟）自动 expired、该工具调用以"被拒绝"结果回传模型。文件系统类 MCP 的配置模板引导目录白名单。

**审计与配额**：`tool_calls` 审计表记录每次工具调用（触发用户、角色、工具名、参数摘要、状态、耗时）；guest 配额多维可配：每日 chain 数、单 chain 消息数 / 工具调用数 / token 预算、orchestrator fan-out 数、并发 chain 数。

**MCP 连接池隔离**：pool key = `(owner_id, server 配置 hash)`（单 owner 下退化为配置 hash，为多用户演进保留正确性）。

## 三、技术选型

| 层 | 选择 |
|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind CSS + zustand + react-router-dom + react-markdown + remark-gfm + @uiw/react-codemirror + lucide-react |
| 后端 | FastAPI + uvicorn（**P1 单进程单 worker**）+ SQLAlchemy 2(async + aiosqlite) + SQLite(WAL) + alembic + PyJWT + bcrypt + 原生 WebSocket |
| Agent | langgraph（create_react_agent）+ langchain-anthropic / langchain-openai（base_url 可配 → OpenAI/DeepSeek/通义/智谱/Kimi/Ollama/OpenRouter）+ langchain-mcp-adapters（仅 schema/工具转换）。**M0 spike 实测后锁定精确小版本**写入 requirements.txt |
| MCP | 官方 `mcp` Python SDK 连接层（stdio + streamable-http），生命周期自管（见 5.7） |
| 启动 | `start.ps1` 一键双进程（dev）；生产单端口=前端 build 后由 FastAPI 静态托管，便于局域网访问 |

## 四、数据模型（alembic 管理）

### 4.1 可移植纪律（为切 PostgreSQL，从 M1 起执行）
全程 ORM 不写方言 SQL；JSON 用通用 `JSON` 类型；主键标准自增 `Integer`；搜索用 LIKE（不用 FTS5）；PRAGMA 只在 db.py 连接事件层；`DATABASE_URL` 走环境变量；alembic 迁移两库可重放（SQLite 用 batch 模式）。迁移=换 `postgresql+asyncpg://` + alembic 重放 + pgloader 导数据。

### 4.2 表结构（含约束）
- **instance_settings**（单行）(id=1, owner_user_id, created_at) — Owner 原子初始化载体
- **users**(id, username **UNIQUE**, password_hash, nickname, avatar, **is_owner**, **token_version**, created_at) — is_owner 仅服务端在 bootstrap 事务内设置
- **model_configs**(id, **created_by→users**, name, provider_type: `anthropic|openai_compatible`, base_url, api_key_encrypted, **capability_overrides_json**, created_at) — API 返回打码尾号；被角色引用删除 RESTRICT
- **roles**(id, **created_by→users**, name, avatar, description, tags_json, system_prompt, model_config_id, model_name, params_json, skills_json, builtin_tools_json, mcp_servers_json, mcp_tools_cache_json, timestamps) — 保存时校验 model_config 属于同一 owner
- **conversations**(id, type: `single|group`, title, orchestrator_enabled, orchestrator_role_id, last_message_at, created_by) — 置顶/归档移至成员表（多人下是个人偏好）
- **conversation_members**(id, conversation_id, member_type: `role|user`, member_id, **UNIQUE(conversation_id, member_type, member_id)**, last_read_message_id, **pinned, archived**(仅 user 成员有意义), joined_at)
- **invites**(id, conversation_id, code **UNIQUE**, created_by, expires_at, max_uses, used_count, revoked) — 兑换=单条原子 `UPDATE … WHERE code=? AND NOT revoked AND used_count<max_uses AND expires_at>now`，影响行数=1 才成功
- **messages**(id **自增int=排序键**, conversation_id, sender_type: `user|role|orchestrator|system`, sender_id, reply_to_id, mentions_json `[role_id|"all"]`, parts_json, status: `pending|generating|done|error|stopped|interrupted`, **revision**(乐观锁), pinned, chain_id, meta_json(usage/旧版本), created_at)
- **artifacts**(id, conversation_id, kind: `html|markdown|code|svg`, title, language, current_version) + **artifact_versions**(id, artifact_id, version, content, created_by_message_id, **UNIQUE(artifact_id, version)**) — content 为单文件字符串（本期能力）；P2 多文件项目=届时新增 `artifact_files(version_id, path, content)` 表的**迁移路径**，非现有字段承载
- **attachments**(id, message_id nullable 先传后绑, uploader_id, filename, mime, size, path uuid) — 白名单+孤儿清理
- **tool_calls**（审计）(id, conversation_id, message_id, role_id, triggered_by_user_id, tool_name, args_summary, status, duration_ms, created_at)

**资源级授权（所有 API 一律执行）**：messages/artifacts/attachments/conversations 读写前校验"请求者是该会话 user 成员"；配置类 API 校验 is_owner。多态 member_id/sender_id 无库级外键，**在服务层统一校验存在性与归属**。

**parts_json 协议**（可扩展 registry，前端未知 type 占位兜底）：`text|code|image|file|artifact(引用 artifact_id+version 固定版本)|tool_call`；P2 预留 `deploy_status|diff`。

**通用约定**：排序 `ORDER BY id`；索引 `messages(conversation_id,id)`、`(conversation_id,pinned)`、`conversation_members(conversation_id)`、`tool_calls(conversation_id,id)`；删除会话级联清理含磁盘文件；启动时残留 `generating` → `interrupted`。

## 五、核心机制设计

### 5.1 模型层（providers.py）
- 模型工厂：角色配置 → `ChatAnthropic` / `ChatOpenAI(base_url=...)` + `bind_tools`
- **参数白名单+能力表**：参数兼容性按"模型+模式"校验（如部分 anthropic 模型/thinking 组合限制 temperature/top_p），**以能力表+契约测试为准，不写死全局规则**；保存时校验提示、运行时剔除不兼容参数；能力表（vision/parallel_tools/usage 口径/图片格式）内置默认值 + **model_configs.capability_overrides_json 按厂商覆盖**；`refusal`/429 翻译为用户可读状态；非视觉模型图片降级为 `[图片: 文件名]`
- **契约测试**：对官方支持厂商（DeepSeek/智谱/Kimi/通义/Ollama）各建一组带 key 可选运行的契约测试（流式/工具/图片/usage/错误格式）
- **日志脱敏**：默认仅记元数据（方法/URL/状态/模型/时延/usage），**永不记 Authorization/key**；body 仅显式临时 debug 开关记录，且不读流式响应体

### 5.2 上下文编码（context.py，自研）+ 硬预算
- 他人历史 → user 消息加 `[昵称]:` 前缀；本角色历史 → assistant；**仅当前角色最近一轮保留结构化 tool_use/tool_result**，更早降级为文本摘要；群聊 preamble（成员表/只以自己身份发言/@ 规则）；编码确定性（不重排）
- **硬预算**（消除"pin 无限膨胀必溢出"路径）：输入预算 = 模型上下文 − max_tokens 输出预留；system+技能 ≤20%（角色保存时校验超限报错）、pinned ≤30%（**超预算拒绝新增 pin 并提示**）、剩余给最近历史按消息边界截断

### 5.3 写入纪律与 SQLite 边界（修订：会话队列≠解决写并发）
- **P1 部署形态：单进程单 worker**（README/start.ps1 写死）；WAL 仍是全库单 writer
- 所有写=短事务 + locked 重试退避；**流式内容内存快照优先**（WS 从内存推送），落库节流 ≥1s 或 part 完成时；每条消息仅由其生成任务这一个 **reducer** 修改，`revision` 乐观锁防覆盖
- orchestrator 并行子角色的占位消息在会话队列内**串行预创建**（顺序稳定），子角色流式走内存、节流落库
- **PG 切换阈值**：需要多进程/多 worker、或常态化多人并发使用时**必须**切 PG；4.1 纪律保证切换成本可控

### 5.4 调度（scheduler.py，自研）
- 每会话一个串行 asyncio 队列（**作用=回复顺序与触发语义**，不承诺解决全库写并发）；不同会话并行
- 触发规则：单聊→唯一角色；群聊+编排关→按 @ 顺序串行回复（后者可见前者），无 @ 不触发（可配默认回复人）；群聊+编排开→orchestrator；**角色消息中的 @ 不触发**（结构性禁环）
- chain：共用 chain_id，Agent 消息上限 20；guest 触发的 chain 执行 §二 的工具拦截与配额
- **停止生成**：cancel 消费任务 + 清空该 chain 后续队列，已缓冲内容落库 `stopped`；UI 立即停，上游 HTTP 流/MCP 调用关闭与子进程清理为**异步尽力**（M0 实测取消传播行为，不承诺计费即刻停止）

### 5.5 Agent 循环（loop.py，LangGraph + 防腐层）
- 按角色动态构造 `create_react_agent`；`recursion_limit`≈15，触顶捕获后追加**禁用工具的收尾调用**（保证文本收尾、无未配对 tool_use）
- **防腐层（本项目高风险点）**：`astream_events` → 自定义领域事件的转换**收敛在本模块**，业务只消费领域事件；M2 用 mock 模型测穿映射

### 5.6 Orchestrator（orchestrator.py）
- 会话级开关 + orchestrator_role_id（默认内置模板）；本身是 react agent，专属工具 `dispatch(role_id, task, context_hint)`（提示词附成员 id↔名称/能力表；**服务端校验 role_id ∈ 本会话成员、属 owner、active**——不以名字分派避免同名歧义；context_hint 限长且不能覆盖工具策略/权限）→ `asyncio.gather(return_exceptions=True)` 并行执行子角色 run
- 子角色回复双写：完整内容作为群消息流式广播；tool_result 只回摘要。深度 1=子角色工具集不含 dispatch；失败→is_error→重试一次→仍败由汇总说明；结束输出汇总消息

### 5.7 MCP 管理（mcp_manager.py，自研生命周期）
- **宿主任务模型**：每 server 一个常驻 Task，同任务内 `async with` 打开 stdio_client/ClientSession 并消费请求队列（Future 回传）；关闭=cancel 宿主任务（anyio cancel scope 同任务进出）；不用 adapters 的每调用新开 session 模式（npx 启动秒级）
- 池 key=(owner_id, 配置 hash)，空闲 10 分钟回收，shutdown 统一关停
- Windows：`shutil.which()` 解析 npx→npx.cmd；env 显式合并 SystemRoot/APPDATA/PATH + `PYTHONIOENCODING=utf-8`；超时后**杀进程树**（psutil）；保持 Proactor 循环，**严禁 WindowsSelectorEventLoopPolicy**
- 超时分层：initialize 长超时（npx 首次拉包分钟级）、call_tool 60s、连续超时才重建；schema 清洗（补 type:object、去 $ref/$schema），坏工具跳过不毁整 server；工具名 `mcp_{server}_{tool}`，遵守 `^[a-zA-Z0-9_-]{1,64}$`
- 角色编辑「测试连接」：预热+缓存 tools 列表（兼作能力标签来源）

### 5.8 WebSocket 可靠续传协议（ws.py，自研，修订）
- 单连接、首帧鉴权（token 不进 query/日志）
- **`stream_epoch` + 每会话单调 `event_seq`**：epoch 每次服务启动重新生成；事件缓冲为内存环形（不持久化，明确此边界）；客户端发现 **epoch 变化**或 after_event_seq 超出缓冲 → 强制完整会话快照重建（覆盖后端重启/崩溃/--reload，区分"没有事件"与"事件已丢失"）；每消息 `revision`；流式追加带 `delta_seq`。**所有变更皆为事件**：message_created/delta/part_update(全量替换该 part)/done/regenerated/member_updated/conversation_updated/conversation_state(正在输入)/error
- 客户端**幂等应用**：按 event_seq 与 (message_id, revision, delta_seq) 去重
- 重连：`subscribe(conversation_id, after_event_seq)` **原子**返回 backlog 后无缝进入实时流；backlog 超出缓冲 → 下发完整会话快照（含生成中消息的累积快照）重建状态

### 5.9 产物与预览（工具修正 + iframe 加固）
- 工具拆分：`create_artifact(title, kind, language?, content) → artifact_id` 与 `update_artifact(artifact_id, expected_version, content)`（乐观锁，冲突报错让模型 read 后重试）；**不再以 title 匹配版本**；`read_artifact(artifact_id)`
- 预览 `GET /api/artifacts/{id}/versions/{v}/raw`：
  - iframe `sandbox="allow-scripts"`（无 same-origin/top-navigation/popups）
  - 响应带 CSP：`default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors 'self'`
  - **防顶层打开**：`Sec-Fetch-Dest != iframe` 时返回 `Content-Disposition: attachment`（生产单端口同源下防 localStorage JWT 泄露）
- markdown 前端渲染；code 用 CodeMirror；全屏 Dialog+复制/下载；外部 URL 仅链接卡不内嵌

### 5.10 regenerate（修正）
- 原位替换 + `revision` 乐观锁防并发覆盖；上下文取该消息之前历史；旧版本存 meta_json
- **不级联重算**：重生成后，同会话中 id 更大的消息前端显示"基于旧版本"stale 徽标（不删除、不重跑）

## 六、里程碑与验收

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 风险验证（先行阶段）** | ① 双厂商 spike：流式/工具调用/**取消传播实测**/错误映射 ② Windows stdio MCP spike：启动/复用/超时/取消/进程树退出 ③ stream_epoch+event_seq+revision 断线恢复原型（含 done/part 事件补齐与**后端重启场景**）④ iframe CSP+Sec-Fetch-Dest 隔离验证 ⑤ 信任模型/权限矩阵/工具分级/配额/Owner 初始化与 JWT 生命周期**定稿** ⑥ **`docs/protocol.md`**：REST/WS 契约（请求响应 JSON、稳定错误码、分页 cursor、幂等键、无权限/不存在/过期邀请的错误区分），先固定四条主链路——消息发送、订阅恢复、邀请兑换、artifact 更新 | 每项有**最小可运行验证**；协议文档成稿；langgraph/langchain 精确版本锁定进 requirements.txt |
| **M1 骨架+角色** | 脚手架、alembic、注册登录（**Owner 原子初始化**+JWT）、模型配置 CRUD（厂商预设+测试连接+能力覆盖）、角色 CRUD（头像/标签/prompt/技能/参数）、联系人页、资源级授权中间件 | 两类 provider 各建配置且测试连接通过；并发注册只产生一个 Owner；guest 账号访问配置类 API 被拒；重启数据不丢 |
| **M2 单聊闭环**（最高风险） | 会话 CRUD（成员级置顶/归档）、WS 可靠续传协议全量实现、模型工厂+防腐层、markdown/代码渲染、停止生成、chain 日志 | 两家 provider 流式单聊；**断网 10s 重连事件不丢不重**（含生成中消息续接与 done 补齐）；停止 1s 内 UI 生效落库 stopped；两会话并发互不干扰 |
| **M3 富媒体+消息操作** | 上传（图片/文件）、图片多模态、create/update_artifact、artifact 卡+加固 iframe+全屏、产物列表、引用/重新生成(stale 徽标)/pin(预算拦截)、搜索 | 视觉模型识图、非视觉降级；HTML 沙箱运行且 raw 直开被强制下载；regenerate 后旧依赖消息出现 stale 徽标；pin 超预算被拒并提示 |
| **M4a 群聊** | 建群/成员管理/删群、@补全、串行回复调度、工具过程卡片、tool_calls 审计落库 | @A 仅 A 回复；@A @B 严格串行且 B 可见 A；停止停整条链；chain 上限生效 |
| **M4b Orchestrator** | 开关+模板角色、dispatch 并行、失败重试/降级、汇总 | 2 子任务并行流式；人为致 1 个失败→重试→降级汇总；子角色确无 dispatch 工具 |
| **M5 MCP** | 宿主任务池、MCP 配置表单+测试连接、工具接入循环、工具分级标注 | stdio+streamable-http 各接一个真实 server 可调用；server 挂掉角色降级回复 |
| **M6 真人协作+打磨** | 邀请码全流程（原子兑换）、guest 配额与危险工具拦截、多人实时、在线/未读、错误文案、空态 | 双浏览器实时互见；同会话两人同时发消息按序处理；**guest 触发含 stdio MCP 角色被默认拒绝**；guest 看不到 owner 配置；邀请码超限/过期兑换失败 |

## 七、验证方式
- **自动化**：pytest+httpx 覆盖核心 API 与**权限矩阵**（owner/guest 双身份参数化）、邀请原子兑换并发测试；mock 模型测调度/循环/abort/orchestrator/事件映射；WS 续传用"断开-重连-比对事件序列"集成测试；厂商契约测试（带 key 可选）
- **手动端到端**（每 milestone 末）：真实 key（DeepSeek/智谱低价模型）→ 单聊流式 → 建群 @ 两角色 → 开 Orchestrator → 接 `@modelcontextprotocol/server-filesystem` → 第二浏览器 guest 入群发言并验证权限边界
- **Windows**：`start.ps1` 单进程启动；README 写明环境要求

## 八、关键风险与对策

| 风险 | 对策 |
|---|---|
| guest 经提示注入驱动危险工具 | 单机可信 owner 信任模型 + 工具分级 + 执行层拦截 + 配额 + tool_calls 审计（§二，M0 定稿） |
| WS 断线丢事件/重复/**后端重启** | stream_epoch+event_seq+revision 幂等协议 + 原子 backlog + 快照兜底（M0 原型，M2 全量） |
| SQLite 单 writer 锁 | 单进程单 worker + 短事务重试 + 内存优先节流落库 + 单 reducer/revision + 明确 PG 切换阈值 |
| 隔 langchain 层排错难 | 防腐层收敛 + 脱敏元数据日志（body 仅临时 debug）+ 契约测试 + M0 实测后锁精确版本 |
| MCP × Windows | 宿主任务模型、npx.cmd、env 合并、进程树清理、Proactor（M0 spike） |
| 群聊消息风暴 | chain 上限 + 角色 @ 不触发 + 编排深度 1 + 停止按钮 |
| Key/JWT 泄露 | key 加密落库+打码；日志不记敏感头；iframe 无 same-origin + CSP + 防顶层打开 |
| 历史未配对 tool_use 持续 400 | 触顶收尾调用 + 仅最近一轮保留结构化工具块 |
| pin 无限膨胀溢出上下文 | 分区硬预算，超限拒绝新增 pin |
