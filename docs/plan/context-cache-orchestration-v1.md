# Roleplex 上下文、Prompt Cache、群聊与 Orchestrator 实施计划 v1

| 元数据 | 值 |
|---|---|
| 受众 | Roleplex 架构、后端、前端与测试维护者 |
| 状态 | C0-C2、M4a、W0、E0 已完成；W1a Owner 绝对根版本已实现待人工验收 |
| 计划版本 | 1 |
| 参考设计 | [多 Agent 群聊平台 Prompt Cache 优化计划](../design/cache-v1.md) |
| 关联主计划 | [Roleplex 总体实施计划](nested-watching-crown.md) |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-08 |

本文把 Prompt Cache 参考设计适配到 Roleplex 当前真实架构，并重排群聊、Orchestrator、Checkpoint、
Shared Memory、会话导出/导入和世界分发的实施顺序。本文是该阶段的范围与验收权威；参考设计用于解释
思路，不直接授权其中的 Memory Workspace、FTS5、sqlite-vec、Embedding 或自动长期记忆能力。M4b 前新增
的本地 Git 仓库与 execution worktree 只以[Agent 仓库工作区计划](agent-repository-workspaces-v1.md)为权威，
不得与 Memory scope 混用。

## 当前实施进度（2026-09-08）

- C0/C1 已实现并于 2026-08-29 经用户人工验收，提交 `4ea6312`；C2 已于 2026-08-30 经用户人工验收；
  M4a 已于 2026-09-01 经用户人工验收；2026-09-01 已确认先定义 W0 工作区/Shell 契约，再实施 E0，随后
  以 W1a 原生文件、W1b 结构化命令、W1c 审批 Shell 逐层跑通单角色闭环，最后才进入 Repository、worktree
  和 M4b fan-out。
  E0 已完成编码、自动验证和用户人工验收；W1a 已按用户确认修订为“每个 World Owner 在前端为多个
  Workspace 分别配置绝对根”，并已通过 fake/真实 managed-world 验证，当前等待人工验收；W1b 及后续阶段尚未开始。
- 新增唯一 ContextBuilder：按当前消息 ID 截止，读取终态历史、做角色视角投影、确定性前缀、硬预算、
  UTF-8 保守估算和分层 SHA-256；当前消息不重复进入 history。
- Role 新增 `context_window_tokens`，默认 200K；服务 ceiling 默认 2M；前端支持 128K/200K/1M 与自定义，
  展示有效窗口、输出预留和可用输入。
- 不可裁剪上下文超限以 `CONTEXT_BUDGET_EXCEEDED` 在 Provider 调用前拒绝，Owner/Guest 提示分层。
- 迁移 `0004_role_context_window` 已通过 SQLite 升降级、batch 外键检查和 PostgreSQL 离线 SQL。
- 自动验证：后端 85 passed、3 skipped；普通 E2E 17 passed；世界切换 E2E 1 passed；兼容数据库 real-E2E
  与正常世界 real-world E2E 各 1 passed；前端 build 通过。
- 真实 DeepSeek V4 Flash 浏览器 E2E 两轮通过：第二轮正确回忆第一轮验证码；第一轮 input/cache=229/0，
  第二轮=267/128。该数据仅为一次观测，不是性能承诺。
- 新增 `test:e2e:real-world`：临时 `default` 世界使用独立数据库和双密钥，经正常世界包装器运行相同两轮
  真实契约；与显式数据库 `test:e2e:real` 分层保留，便于定位 Provider 与世界基础设施问题。
- C2 新增 `context.loaded`，并把同一 ContextBuilder 快照的 schema、L0/L1/L2/tool policy SHA-256、历史
  计数、裁剪数和本地预算绑定到 Provider 与 generation 日志；C3 前不伪造 checkpoint hash，日志不保存
  Prompt 原文。
- Provider usage 新增厂商报告的 cache write 与命中比；Anthropic 原始输入恢复为包含 cache read/create
  的完整口径，DeepSeek cache miss 不冒充 cache write；fake usage 继续为空。
- C2 自动验证：后端 86 passed、3 skipped；普通 E2E 17 passed；fake managed-world E2E 1 passed；真实
  DeepSeek managed-world E2E 1 passed；前端 build 通过。验证中修正普通 Playwright 配置误收集独立
  real-world 用例的问题，默认回归重新固定为 fake 且不联网。
- fake managed-world 在独立 `alpha/beta` 世界中先完成两轮消息与 C2 日志断言，再切换世界；真实测试在
  独立 `default` 世界中完成相同两轮历史与日志安全断言。本轮 DeepSeek 第一轮 input/cache=`229/128`、
  ratio=`0.5589519651`，第二轮=`264/128`、ratio=`0.4848484848`；数字仍只属于本轮观测。
- M4a 已实现群聊成员 revision 管理、稳定 mentions/`all` 展开、每会话持久串行队列、跨会话并行、共享
  chain/独立 execution、停止整链、同 chain L6 上下文和工具过程卡片；无 mentions 只记录真人消息。
- M4a 自动验证：后端 93 passed、3 skipped；普通 fake E2E 18 passed；fake managed-world E2E 1 passed；
  真实 DeepSeek managed-world E2E 2 passed；前端 build 通过。真实群聊 A/B 同 chain 串行，B 成功复述只
  存在于 A system prompt 的协作码；A/B context message count=`0/1`，execution ID 不同。
- 本轮真实群聊 Provider 观测：A input/cache=`292/0`、B=`306/0`。命中受 Provider 策略影响，不作为门槛；
  串行顺序、上下文可见性、chain/execution 和 Prompt 不落日志才是确定性验收。

## 一、为什么先暂停会话导出/导入

本计划获批时，单聊链路虽然已经能持久化消息并流式回复，但尚未把数据库历史投影为模型上下文；该缺口
现已由 C1 修复，M4a 群聊调度也已实现；Orchestrator 仍未落地。如果现在冻结导出格式，会遗漏
或过早决定以下语义：

- 群聊成员、mentions、串行回复顺序和同一 chain 的停止边界；
- Orchestrator、子 Agent、父子执行、重试和降级汇总的持久化关系；
- Repository Binding、execution worktree 和代码变更哪些属于主机本地状态、不得进入会话导出；
- 消息 regenerate 后的 revision/旧版本语义；
- Context Checkpoint 是权威数据还是可重建派生数据；
- Shared Memory 是否属于单个会话导出范围。

因此工作项四“会话导出与导入”延期，直到本文 C1、C2、M4a、W0、E0、W1a/W1b/W1c、W2a/W2b、W3、M4b、
C3 和 C4 完成。
工作项五“世界分发”继续依赖会话导出/导入，不提前实施。

## 二、当前架构事实与术语映射

### 2.1 当前事实

- 当前实现中，一个世界是一个物理目录和数据库，并可保存多个 Owner 配置的主机本地 Workspace Binding；
  它们不是 Memory Workspace，也不改变世界物理 scope。W2 计划的 Repository Binding 继续建立在该资源上。
- 默认部署继续使用 SQLite、单进程、单 worker；不为了缓存或记忆引入外部数据库服务。
- 当前生成服务已通过唯一 ContextBuilder 向 `run_agent()` 传入终态历史和预算诊断。
- `messages` 已有 sender、reply、mentions、parts、status、revision、chain 和 meta 字段。
- `conversations` 已预留 group、Orchestrator 开关和角色字段，但行为仍属预留。
- Provider usage 已统一读取输入、输出、总量、缓存命中和缓存写入 token，并计算可追溯命中比；fake 不伪造 usage。
- 工具执行已有默认封闭、Owner/Guest 危险级别拦截和审计基础。

### 2.2 参考设计到 Roleplex 的映射

| 参考设计术语 | Roleplex v1 解释 |
|---|---|
| Workspace Stable Prefix | 当前世界内、由软件版本确定的稳定运行约束；不是代码仓库绑定，也不是把工程文档全文注入 Prompt |
| Role Stable Prefix | 角色 system prompt、稳定技能说明和允许暴露的工具策略 |
| Conversation Stable Prefix | 会话类型、稳定规则和按固定顺序编码的成员身份表 |
| Role × Conversation | ContextBuilder 的上下文隔离键；同一角色在不同会话不共享历史 |
| Conversation Checkpoint | 按明确消息边界生成的不可变上下文压缩版本，属于派生数据 |
| Shared Memory | 当前世界内、角色级跨会话的显式长期记忆；不等同于聊天历史 |

C1/C2 首版不新增 Memory Workspace 实体，也不把 `world_name` 当作数据库外键。物理世界隔离已经提供最高层
上下文边界；后续 Repository Binding 只负责本机代码工具授权，不能进入这一术语映射偷换 scope。

## 三、已批准的核心决策

### 3.1 唯一 ContextBuilder

所有单聊、群聊角色、Orchestrator 和子 Agent 必须通过同一个 ContextBuilder 构造模型输入。业务服务、
调度器和 Agent 不得各自拼装历史。ContextBuilder 输出：

- 规范化的 LangChain 消息序列；
- 当前上下文边界和预算结果；
- 各稳定层 SHA-256；
- `context_schema_version` 和 `tool_policy_hash`；
- 可安全写入日志的非敏感诊断元数据。

### 3.2 上下文层级

首版固定顺序：

```text
L0 Runtime Stable Policy
L1 Role Stable Prefix
L2 Conversation Stable Prefix
L3 Checkpoint（C3 前为空）
L4 Finalized Recent History
L5 Retrieved Shared Memory（Memory v0 前为空）
L6 Current Chain Events + Current Message
```

越稳定的层越靠前。request ID、execution ID、当前时间、在线状态、随机 UUID 和运行负载不进入稳定层。

### 3.3 Append-only 的适用范围

数据库 Message 不是绝对不可变对象。流式生成、停止、恢复和 regenerate 允许按既有状态机更新 status、
parts 和 revision。Append-only 仅约束已经进入模型上下文的终态历史投影：

> 相同 `message_id + revision + context_schema_version` 必须产生完全相同的规范化模型消息；不得因下一轮
> 请求重新排序、改变模板、重算昵称、加入动态字段或滚动重写摘要。

ContextBuilder 只消费一致性快照中的终态版本。`pending`、`generating`、空 assistant 占位和未配对工具轮
不得进入稳定历史。合法编辑或 regenerate 递增 revision，并允许从变化位置开始失去缓存。

历史发送者使用稳定类型和 ID 编码，例如 `[role:7]`；当前成员名称映射放在 L2。角色改名或成员变化会
显式改变 L2 hash，这是低频且正确的缓存失效，不反向重写每条旧消息。

### 3.4 工具稳定与权限优先

工具集合按“角色配置 + 调用主体类别 + 会话策略 + 工具可用状态”生成 `tool_policy_hash`。同一 policy 下
工具顺序、描述和 JSON Schema 必须确定性一致，不能按当前问题“看起来用不用得到”随意增删。

工具分三类：

1. 可见且可直接请求执行；
2. 可见但需要审批或可能由执行层返回 rejected；
3. 不属于当前 Owner/角色、schema 本身敏感或不可见，不能发给模型。

Prompt 构建只负责最小暴露；每次实际工具调用仍须重新校验 Owner 归属、角色配置、会话策略、危险级别、
审批、配额、参数资源边界和 MCP 存活状态。真实权限或可用性变化允许并且应该改变 hash、使缓存失效。

### 3.5 Prefix 首版不建版本表

C1/C2 不新增 workspace/role/conversation prefix version 表。首版使用：

- `context_schema_version`：代码中的稳定序列化版本；
- 最终规范化内容 SHA-256：事实身份；
- 现有 `roles.updated_at`、`conversations.revision`：辅助解释变化来源；
- `tool_policy_hash`：工具暴露策略身份。

只有 C3 Checkpoint 真正需要跨进程持久生命周期时才增加 checkpoint 表和 version。hash 只能识别内容，
不能替代业务 revision 或恢复原始 Prompt。

### 3.6 Provider Cache 只观测真实数据

平台不实现 KV Cache、Token Trie 或模型回复缓存。Provider 没有报告的 cache read/write token 保持为空，
不得按字符数估算或让 fake 伪造。命中率只在 Provider 同时报告 input 和 cached token 时计算。

首版不保存完整 Prompt，不实现 token-level longest common prefix；用各层 hash 判断变化位置，避免为诊断
引入敏感 Prompt 持久化和 Provider tokenizer 口径偏差。

## 四、首版范围应有多大

### 4.1 首个可实施版本：C1 + C2

首个版本只解决两个问题：

1. Agent 正确看到已有会话历史，所有运行路径使用同一 ContextBuilder；
2. 在不记录 Prompt 原文的前提下，证明稳定层是否真的稳定，并读取真实 Provider cache usage。

它不包含群聊、Orchestrator、Checkpoint、Shared Memory、FTS5、向量搜索、导出/导入或世界分发。这样
可以先把当前“只看见当前消息”的正确性缺口修好，再让群聊建立在已测试的上下文语义上。

### 4.2 Shared Memory v0：后续独立阶段

Memory v0 不阻塞 C1/C2、M4a、M4b 或 C3。等编排与 Checkpoint 稳定后，如仍确认需要跨会话长期记忆，
再实施以下最小骨架：

- 只支持当前世界内的 role scope，不新增 Workspace/Project scope；
- 只有 Owner 显式确认的内容可写入，不自动把聊天或工具输出提取成 Memory；
- Memory 的 content/type/source 不原地改写；修正时插入新记录并用 `supersedes_id` 关联，在同一短事务中
  把旧记录的 `active` 生命周期标志置为 false；
- 权威数据使用通用 ORM 普通表；搜索通过 `MemorySearchBackend` 接口；
- 第一版实现 `LikeMemorySearchBackend`，不使用 SQLite 方言 SQL；
- 记录 `(memory_id, role_id, conversation_id)` 注入关系；不能假设模型回答会复述 Memory；
- 已注入且仍有效的 Memory 由 ContextBuilder 按持久关系继续纳入已知上下文，直到折叠进 checkpoint；
- 不做 Embedding、自动抽取、Hybrid Retrieval、FTS5、sqlite-vec、pgvector 或外部向量数据库。

Memory v0 需要单独的产品入口和权限评审；计划中定义骨架不等于授权自动记忆功能。

### 4.3 LIKE 到 FTS5/sqlite-vec 的升级边界

LIKE 是初始适配器，不是永久技术承诺。只有满足以下条件后才提出新的计划修订：

- Memory v0 的业务语义和权限已经稳定；
- 使用代表性中文、英文、代码标识符和错误码数据完成基准；
- LIKE 在目标数据量下无法满足经确认的延迟或召回要求；
- FTS5 tokenizer、索引体积、写入开销、重建和完整性策略已有实测；
- sqlite-vec/Embedding 的模型、维度、升级、重建、离线分发和 Windows 打包方案已验证；
- Application 只依赖搜索接口，SQLite/PostgreSQL 方言全部留在 Infrastructure Adapter；
- 主计划先删除或修订“搜索用 LIKE（不用 FTS5）”的既定约束。

FTS5 与 sqlite-vec 是两个独立升级：关键词搜索不足可以只引入 FTS5；没有语义检索证据时不得顺带引入
Embedding 和 sqlite-vec。

## 五、目标模块边界

```text
backend/app/context/
├── domain.py          # ContextBuildRequest/Result、section 元数据
├── builder.py         # 唯一 ContextBuilder 与固定层级
├── projection.py      # ORM Message → 规范化 BaseMessage
├── budget.py          # 输入预算和按消息边界裁剪
├── fingerprint.py     # 规范化层级 SHA-256
└── checkpoints.py     # C3 才实现的持久 checkpoint 服务

backend/app/scheduling/
├── conversation.py    # M4a 每会话串行调度
└── orchestrator.py    # M4b dispatch、父子执行与汇总

backend/app/memory/     # Memory v0 前不创建
├── domain.py
├── service.py
├── repository.py
└── search.py          # 首版 LIKE，未来 adapter 可替换
```

模块名是计划目标，不要求一次提交全部创建。若实现中发现更自然的边界，必须先更新本计划，再移动代码。

## 六、C0：基线与契约先行

### 6.1 目标

在改造前锁定当前事实，避免 ContextBuilder 只在形式上存在：

- 先写一个“第二轮必须看见第一轮终态历史”的失败用例，确认红灯原因是业务层没有传入 `history`，而不是
  fake provider 旁路或测试环境问题；
- 记录当前真实 Provider 连续两轮的 input/cache usage 作为人工基线；
- 固定 ContextBuilder 的输入、输出、错误和安全边界；
- 明确当前消息的数据库 ID 是上下文截止边界，避免重复注入。

### 6.2 ContextBuilder 内部契约

建议接口：

```python
ContextBuildRequest(
    role_id,
    conversation_id,
    current_message_id,
    triggered_by_user_id,
    execution_kind,
)

ContextBuildResult(
    system_prompt,
    history,
    current_message,
    tools,
    budget,
    fingerprints,
)
```

`execution_kind` 首版支持 single；M4a/M4b 兼容新增 group_role、orchestrator、subagent。不得为每种执行类型
复制 ContextBuilder。

### 6.3 错误与降级

- 当前消息或会话不存在：按资源隐藏规则失败，不猜测上下文；
- role 不可用：复用角色不可用语义；
- 历史中未知 part：保留安全占位，不让整个上下文构建失败；
- 预算不足容纳不可裁剪的最小上下文：使用预留错误码 `CONTEXT_BUDGET_EXCEEDED`，不静默截断 system、
  工具安全约束或当前消息，也不发起 Provider 调用；
- ContextBuilder 内部异常：生成按 failed 收尾，记录 hash/计数等安全字段，不记录 Prompt 原文。

新增错误码必须先登记 `docs/protocol/error-codes.md`，不能从异常 message 动态生成。

`CONTEXT_BUDGET_EXCEEDED` 的语义固定为：在历史、checkpoint、Memory 和其他可裁剪内容全部移除后，
Runtime/Role 必要约束、当前可见工具 schema、当前消息与输出预留仍超过模型 context window。本错误属于
`status=rejected`、`retry=conditional`，生成记录和 assistant 占位按 error 终态收尾，Provider 调用次数为 0。

界面提示按权限分层：

- Owner：`当前消息与角色基础配置超过模型上下文上限。请缩短消息，或调整角色提示词、工具配置、模型
  上下文上限或最大输出长度后重试。`
- Guest：`本次请求超过模型可处理的上下文上限。请缩短消息后重试，或联系 Owner 调整角色配置。`

公开 payload 只返回稳定错误码，不向 Guest 返回 system/tool token 占比、具体配置内容或可推断 Owner
私有设置的诊断字段；详细预算只进入 Owner 本地的脱敏结构化日志。

## 七、C1：上下文正确性与稳定前缀

### 7.1 一致性读取边界

ContextBuilder 以 `current_message_id` 为截止点：

- 只读取同会话中 ID 小于当前消息的历史；
- 当前消息从指定 ID 单独读取并放在 L6，不能同时出现在 history；
- 只投影允许的终态 status；
- 按 message ID 升序，不按时间或当前成员顺序重排；
- 一轮 build 使用一个短读事务获得会话 revision、成员和消息边界；
- 构建完成后权限变化仍由发送/工具执行边界重新校验。

群聊串行时，后一个角色只能在前一个角色 `message_done` 提交后开始 build。Orchestrator 并行子任务使用
调度器预先固定的共同 base boundary；子任务之间默认不可见对方未完成输出。

建议首版终态投影规则：`done` 正常进入；`stopped` 仅在已有非空文本时以稳定停止标记进入，且不保留可能
未配对的结构化工具轮；`error/interrupted` 默认不进入模型历史，只在 UI 和审计中保留。该规则在 C1 编码
前由测试样例最终确认，不能由 ContextBuilder 临时猜测。

### 7.2 历史角色投影

针对目标角色：

- 该角色自己的终态回复映射为 assistant；
- 真人和其他角色消息映射为 user/context，并带稳定 `[user:id]` / `[role:id]` 身份；
- L2 保存按 `(member_type, member_id)` 排序的当前名称与能力映射；
- `orchestrator` 和 `system` 使用独立稳定类型，不伪装成普通 user；
- 墓碑角色的旧消息可以读取，墓碑角色不能成为新的回复目标；
- tool use/result 只有完整配对且符合当前防腐层协议时保留结构化形式，更早轮次可在 C3 折叠。

### 7.3 确定性规则

- sections 顺序固定；
- 消息顺序固定；
- JSON key 顺序和空值省略规则固定；
- 工具按稳定 tool identity 排序；
- 成员按类型和 ID 排序；
- 换行、标签和占位模板固定；
- 不读取或注入日志 request/chain/execution ID；chain 只用于关联与调度，不写入自然语言 Prompt；
- 所有 hash 对最终规范化内容计算，不对 ORM 对象 repr 计算。

### 7.4 输入预算

沿用总体计划的硬预算：

- 输入预算 = 模型上下文上限 − max output tokens；
- system + 稳定规则遵守既有占比上限；
- pinned 历史遵守既有独立上限；
- 其余预算给 checkpoint 和最近历史；
- 按完整消息边界从旧到新裁剪，当前消息不可被静默截断；
- token 预算通过统一 `TokenBudgetEstimator` 接口计算，不允许 ContextBuilder 各层使用不同经验公式；
- 日志字段区分 `estimated_context_tokens` 与厂商返回的 `input_tokens`。

C1 没有 Checkpoint 时，历史超预算只做确定性消息边界裁剪，不调用模型生成滚动摘要。

估算器按以下优先级选择：

1. `provider_tokenizer`：项目已验证且与具体 Provider/model 请求格式匹配的本地 tokenizer；
2. `langchain_model`：模型对象提供的 message token 计算能力，且已由真实契约测试校准；
3. `conservative_utf8_v1`：无法确认 tokenizer 时，对最终规范化文本、消息包装和 canonical tool schema 按
   UTF-8 字节上界计数，并增加固定结构开销。该方法会明显高估部分英文/中文输入，但不得低估后再让
   Provider 以 context overflow 拒绝。

能力表为已验证模型提供推荐窗口、默认输出预留和可选 estimator 类型。实际预算以角色显式保存的
`context_window_tokens` 为准，并与应用绝对安全上限取较小值。角色默认 200,000 tokens；这只是当前常见
长上下文模型的产品默认值，不代表 Roleplex 已自动探测到任意 OpenAI-compatible、本地或旧模型的真实
能力。缓存命中 token 不减少 context window 占用，不能因为“预计会命中缓存”放宽预算。

`TokenEstimate` 至少返回：

```text
estimated_tokens
estimator_kind
estimator_version
is_provider_exact
safety_margin_tokens
```

预算使用 `estimated_tokens + safety_margin_tokens`。结构化日志允许记录这些数值和 estimator 公共标识，
但字段名必须带 `estimated_`/`estimator_`；只有 Provider 响应中的 usage 才能写入 `input_tokens` 等真实用量。

真实契约测试比较 estimate 与 Provider `input_tokens`：任何低估都必须保留样本并收紧该 model/provider 的
margin；不能为了提高上下文利用率静默降低安全余量。只有持续实测证明稳定后，才能把某 estimator 标记为
`is_provider_exact=true`。

### 7.5 Role 级上下文窗口配置

上下文窗口属于“具体角色选择的模型”，不属于仅保存 Key/base URL 的 Provider 配置。C1 为 roles 增加显式
`context_window_tokens` 字段，不把它塞进会透传给 Provider 的 `params_json`：

```text
default = 200_000
minimum = 4_096
maximum = 2_000_000（受应用绝对安全上限再次约束）
```

应用级 `max_context_tokens` 从当前 100K 产品默认改为部署安全 ceiling，首版默认 2,000,000。有效窗口：

```text
effective_context_window = min(role.context_window_tokens, settings.max_context_tokens)
```

已验证模型的能力表可以给前端提供推荐值，但不能在模型名或 Provider 变化时静默覆盖 Owner 已保存的值。
任意 OpenAI-compatible 中转不保证提供可靠模型元数据；设置过大可能由 Provider 拒绝，设置过小只会让
Roleplex 更早裁剪或进入 Checkpoint。

前端角色编辑页增加“上下文窗口（tokens）”：

- 数字输入，默认 200,000；
- 快捷预设 128K、200K、1M 和自定义；
- 同时展示应用 ceiling、有效窗口、最大输出预留和估算可用输入；
- 帮助文案明确“填写模型 API 实际支持的输入+输出总窗口”；
- 当 `max_tokens >= effective_context_window` 时阻止保存；
- 模型名变化但窗口值未变化时给出复核提示，不自动重置；
- 仅 Owner 可修改，Guest 不读取内部预算诊断。

200K 是容量上限，不是每轮填充目标。ContextBuilder 仍只发送预算内的必要历史；更大的窗口不能成为跳过
裁剪、Checkpoint、相关性控制或成本观测的理由。

### 7.6 C1 数据库变化

C1 明确需要为 `roles.context_window_tokens` 提供 Alembic 迁移、ORM、请求/响应 schema、前端类型和角色协议
更新，已有开发角色回填 200,000。迁移必须通过 SQLite upgrade/downgrade、batch 外键检查和 PostgreSQL
离线 SQL 编译。

除该已批准字段外，C1 优先不新增表。若实现验证发现现有字段不能表达稳定边界，必须单独提出迁移评审，
不能把 sender snapshot、prefix version 或上下文缓存偷偷塞进无文档 JSON。

## 八、C2：缓存可观测性

### 8.1 日志字段

在现有 `provider.call_started/completed/failed` 和 generation 终态上增加可选安全字段：

```text
context_schema_version
runtime_prefix_hash
role_prefix_hash
conversation_prefix_hash
checkpoint_hash
tool_policy_hash
context_message_count
context_truncated_message_count
estimated_context_tokens
cache_hit_tokens
cache_write_tokens
cache_hit_ratio
```

约束：

- hash 使用 SHA-256，不保存层内容；
- `cache_hit_tokens/cache_write_tokens` 只来自 Provider；
- ratio 只从 Provider 报告的 token 相除；
- estimate 明确标记，不与 Provider usage 汇总；
- fake provider 不产生 cache usage；
- ContextBuilder 不把完整 Prompt、用户输入、模型输出或工具原始参数写日志。

新增字段前同步更新 `docs/design/logging-v2.md` 和内部观测协议。

### 8.2 首版诊断能力

首版能够回答：

- 同一角色/会话连续两轮，哪些层 hash 保持不变；
- 角色配置、成员表、工具 policy 或 checkpoint 哪一层发生变化；
- Prompt 结构稳定但 Provider cache token 较低时，问题是否更可能来自 TTL、账号、路由或厂商策略；
- 历史是否因预算裁剪导致 prefix 变化。

首版不计算 token-level prefix reuse ratio，不建立缓存指标数据库或监控服务。

## 九、M4a：群聊串行调度

### 9.1 行为

- 单聊继续触发唯一存活角色；
- 群聊无 Orchestrator 时只响应真人消息中的 mentions；
- 多个 mentions 按请求顺序串行执行，后一个角色能看见前一个角色的终态回复；
- `all` 展开使用稳定成员顺序，不能依赖数据库无 ORDER BY 的返回顺序；
- 角色消息中的 mentions 不触发新 chain，结构性阻止 Agent 环；
- 墓碑、停用或越权角色在入队前拒绝；
- 同一真人触发链共享 chain ID，但每个角色有独立 execution ID；
- 停止操作取消当前执行并清空该 chain 后续队列，不影响其他会话。

### 9.2 SQLite 写入边界

- 每会话队列只保证回复顺序，不充当数据库锁；
- 角色占位消息在队列 owner 中短事务预创建；
- 每条流式消息只有自己的 reducer 更新；
- 不同会话可以并行，但继续遵守单进程/单 worker；
- locked retry 必须有限、幂等，不能无限重试。

### 9.3 前端与协议

- 群成员和 mentions 使用稳定 ID，不用名字作为 wire identity；
- UI 提供可访问的 @ 补全、执行队列、停止整条 chain 和角色失败状态；
- WS 继续使用现有事件信封，新增事件先更新公开 WS 协议；
- 对未知事件保持降级，不让旧客户端崩溃。

## 十、E0 持久 execution 与 M4b Orchestrator

> **已确认实施顺序**：先按[Agent 仓库工作区计划](agent-repository-workspaces-v1.md)完成 W0 设置契约，再
> 独立完成 10.2 的 E0 单表持久执行树；W1a/W1b/W1c 依次完成原生文件、结构化命令和审批 Shell，验收后
> 才进入 W2a Repository Binding、W2b 只读文件/Git 和 W3 worktree/补丁。所有阶段分别
> 人工验收后，才实施
> 10.1、10.3-10.6 的 M4b fan-out。

### 10.1 产品入口与公开行为

- Orchestrator 只允许群聊显式开启；单聊不能开启。开启后，真人消息统一只创建一个 Orchestrator 父执行，
  不再按 mentions 走 M4a 串行队列；mentions 仍作为用户输入保存，可供 Orchestrator 理解，但不直接授予分派权。
- Orchestrator 角色必须是会话内当前 Owner 拥有的存活启用成员；它不属于可 dispatch 的子角色集合，避免
  把编排角色再次当成子 Agent。成员被移除、停用或墓碑后自动关闭编排，不能靠旧配置继续执行。
- 新增 Owner-only 的会话编排配置接口，使用 `expected_revision` 乐观锁启用、关闭或更换 Orchestrator；
  更新成功产生 `conversation_updated` 事件。创建群聊时的现有字段继续兼容，但 M4b 前的禁用 UI 才在本阶段开放。
- `POST /messages` 仍返回 `202`。开启编排时，初始 `generation_id/generation_ids` 只包含父执行；后续子执行
  通过既有消息事件出现，不要求发送接口等待模型规划完成。
- 子角色消息继续使用 `sender_type=role`；最终汇总使用 `sender_type=orchestrator` 和实际 Orchestrator 角色 ID。
  客户端必须把两者都计入活动 generation，并用现有消息状态展示失败、停止和重试结果。

### 10.2 E0：父子执行持久化

E0 采用一张新的 `agent_executions` 表作为执行身份、父子关系、dispatch 请求和重试终态的唯一事实源；不另建
一张复制 status/attempt 的 dispatch 状态表。一次子任务重试创建新的 execution/generation/消息，保留失败
尝试，不把已经广播的失败输出静默改写成成功。`generations` 继续负责消息流生命周期，二者在同一短事务更新，
但不能从日志反推或修复数据库状态。

| 字段 | 约束与含义 |
|---|---|
| `id` | 标准整数主键 |
| `execution_id` | `String(64)`，非空、唯一；日志链路标识 |
| `parent_execution_id` | 可空，自引用 `execution_id`；父执行删除时级联删除子树 |
| `conversation_id` | 非空，外键到会话并级联删除 |
| `generation_id` | 非空且唯一，外键到 generation；每次实际尝试拥有独立 generation |
| `chain_id` | 非空，等于本次真人触发链的 `generations.run_id` |
| `role_id` | 可空，角色墓碑保留 ID；真正硬删除时置空，不级联抹掉执行事实 |
| `execution_kind` | `single/group_role/orchestrator/subagent` |
| `dispatch_order` | 子执行在父执行中的稳定零基序号；父执行为空 |
| `attempt` | 从 1 开始；同一 `parent_execution_id + dispatch_order` 内递增 |
| `task_text` / `context_hint_text` | 仅 subagent 非空；受限的会话内容，不进入日志或测试失败摘要 |
| `status` | `queued/running/completed/failed/stopped/interrupted` |
| `error_code` | 失败或中断时的稳定错误码，其他状态为空 |
| `created_at/started_at/ended_at` | 生命周期时间；不能用日志时间猜测终态 |

约束与索引至少包括：`UNIQUE(generation_id)`、
`UNIQUE(parent_execution_id, dispatch_order, attempt)`、
`INDEX(conversation_id, status)`、`INDEX(parent_execution_id, dispatch_order, attempt)` 和 `INDEX(chain_id)`。
迁移命名为 `0005_agent_executions`，必须验证 SQLite 升降级、外键完整性和 PostgreSQL 离线 SQL。

E0 起，single/group_role 的新 generation 先创建 execution 行；M4b 才增加 orchestrator/subagent。旧数据库中
已经终结的 generation 不从 `queue_jobs.payload_json` 反向猜测回填。旧进程遗留任务仍按现有规则在启动时
降级，从迁移后的新请求开始保证执行表完整。数据模型协议必须明确：queue payload 只是唤醒参数，不再是
execution 身份的权威来源。

E0 实现验证（2026-09-02）：后端 `101 passed, 3 skipped, 8 deselected`；迁移完成 SQLite upgrade/downgrade、
ORM metadata、外键完整性和 PostgreSQL 离线 SQL；fake managed-world E2E 1 passed，world-switch 后 active
execution 为 0；真实 DeepSeek managed-world E2E 2 passed，两个 group_role 和两个 single execution 全部
completed。当前只实现 single/group_role、attempt=1 和中断收口；parent/dispatch/task/context hint 仍为空，
不能据此宣称 M4b 已实现。

### 10.3 两阶段编排与稳定顺序

一个 Orchestrator 父 job 仍由 M4a 的会话 worker 独占，内部按以下顺序执行；子任务不进入 M4a 串行
`queue_jobs`，而由父任务持有和取消，避免名义并行实际被同一 worker 串行化：

1. 父执行调用唯一 ContextBuilder，进入 planning 阶段；`dispatch(role_id, task, context_hint)` 只收集请求，
   框架 tool call 必须先经防腐层转为领域 dispatch 请求，业务层不能读取 LangGraph 私有事件。
2. 以模型 tool-call 数组中的原始顺序分配 `dispatch_order`。服务端批量校验全部请求后，在一个短事务中按
   稳定顺序预创建第一轮子 generation、execution 和角色占位消息；父汇总占位消息最后创建，因此消息 ID
   顺序固定为“真人 → 子任务 → Orchestrator 汇总”。
3. 提交占位和 `message_created` 事件后，再用有界 `asyncio.gather(return_exceptions=True)` 并行启动子任务。
   每个子 generation 是其消息唯一 reducer，跨子任务不能更新同一消息。
4. 可重试失败创建 `attempt=2` 的新 execution/generation/消息；第一轮失败消息保持 `error`。所有子任务
   到达终态后，父执行只选择每个 dispatch 最新的成功结果，或最后一次结构化失败摘要。
5. Orchestrator 使用相同 ContextBuilder 构造 final 阶段输入，流式写入最后的汇总占位；失败角色必须以
   明确失败状态提供给模型，汇总不能描述为已经成功。

Planning 文本不作为一条伪造完成消息展示；没有有效 dispatch 时仍进入 final 阶段，由 Orchestrator 直接
回答或说明无法分派。父 planning/final、各子尝试均保留独立 Provider usage 日志，并用 parent execution 串联。

### 10.4 ContextBuilder 与 dispatch 工具边界

- Orchestrator planning/final 和所有 subagent 都调用 `app/context/` 的唯一 ContextBuilder。ContextBuilder
  增加类型化 execution overlay，把 dispatch task、context hint 或结构化子结果计入当前输入和硬预算；业务
  服务不得在 Builder 之后拼接未计费的大段 Prompt。
- 并行子执行共享原始真人消息 ID 作为 base boundary，只读取 `message.id < current_message_id` 的同一历史
  快照；它们看不见同批兄弟的 generating、done 或 retry 消息。final 阶段只通过类型化子结果 overlay 获取
  本批结果，不依赖查询时机推断兄弟顺序。
- 首版 `task` 最长 4096 字符、`context_hint` 最长 2048 字符；空 task、未知字段、越限或非法 role ID 返回
  结构化 dispatch 拒绝，不截断后悄悄执行。task/hint 是受会话授权保护的数据，可以进入业务数据库和模型
  输入，但不得进入正式日志、工具摘要、浏览器测试失败文件或 E2E summary。
- Orchestrator 的 `tool_policy_hash` 包含 dispatch schema 版本、最大 fan-out、长度限制和排序后的可分派角色
  ID；任一项变化必须改变 hash。execution/request ID 仍不进入稳定自然语言前缀。
- dispatch 执行前以及每次 subagent attempt 开始前，都重新校验 conversation 未删除、开关仍启用、父执行
  未停止、目标角色仍是本会话成员且属于当前 Owner 并处于 active 状态。
- 深度固定为 1：只有 `execution_kind=orchestrator` 的 planning 阶段暴露 dispatch；subagent、final 阶段及
  普通 single/group_role 的工具集中都没有 dispatch，服务端工具注册表也必须按 execution kind 强制过滤。

### 10.5 并行、重试、停止与重启

- 首版每个父执行最多接受 4 个有效 dispatch；超限作为结构化工具拒绝交给 Orchestrator，不启动前 4 个后
  静默丢弃其余请求。并行数固定上限 4，不从模型参数或用户文本动态提高。
- 子任务最多重试一次。只有 `PROVIDER_TIMEOUT`、`PROVIDER_RATE_LIMITED`、暂态 `PROVIDER_ERROR` 和受控
  fake 暂态错误可重试；鉴权、bad request、上下文预算、权限/成员变化和用户停止不重试，避免重复计费和
  无意义调用。第二次失败以原稳定错误码进入 final 结构化摘要。
- 停止 chain 时先持久化父子 generation/execution 的停止请求，再取消父任务；取消必须传播到 gather 中的
  子任务。已缓冲文本按现有规则以 `stopped` 收尾，尚未开始的子任务不得调用 Provider，其他 chain 不受影响。
- 服务重启不恢复或重放 Provider 调用。遗留 `queued/running` execution 统一写为 `interrupted`，对应
  generation/message 继续走既有中断降级；不能猜测子任务成功，也不能自动生成一个没有发生的父汇总。
- Orchestrator 配置在消息接收时已经失效，必须在真人消息落库前返回稳定 422；dispatch 后续失效则只失败
  对应子执行并继续 final 降级，不回滚已经持久化的真人消息。

### 10.6 协议、日志与测试切片

E0 编码前先更新 execution 数据模型、内部协议和错误码；W0、W1a/W1b/W1c、W2a/W2b、W3 的协议、日志和测试只在
仓库工作区计划维护。
M4b 编码前再更新 Agent 内部协议、公开会话/消息/WS 协议和错误码；实现后才把 Orchestrator 状态从预留改为
已实现。日志只增加 execution/dispatch 序号、attempt、状态、耗时和稳定错误码，不保存 task、hint、子结果
或 Prompt。新的阶段顺序为：

1. **E0 持久执行树**：迁移、ORM、所有新 generation 的 execution 行、父子/attempt 状态和启动中断降级；
   独立人工验收和提交，不开放仓库工具。
2. **W0/W1a-W1c 单角色最小闭环**：先确认设置契约，再依次完成当前 World 工作区/原生读写、结构化命令和
   Owner 审批 Shell；不接 Git 或 worktree，每个切片独立人工验收和提交。W1a 必须同时通过 fake
   managed-world 与专用真实 Provider managed-world 工具闭环；测试工作区使用外部 testworkspace，但产品
   Workspace 根由各 World Owner 在前端独立配置，不设全局 allowed-root 限制。不得把真实 API 验证推迟到 W1c。
3. **W2a/W2b/W3 代码协作扩展**：按独立计划依次完成 Repository Binding、只读文件/Git 和 worktree/补丁；
   每个切片独立人工验收和提交。
4. **M4b-1 确定性 fan-out**：领域 dispatch 请求、防腐层映射、稳定占位顺序、共同 base boundary、最大 4 并行。
5. **M4b-2 重试与汇总**：按错误码重试一次、失败摘要、最终 Orchestrator 流式消息、停止整树。
6. **M4b-3 产品验收**：Owner 配置 UI、并行代码协作/失败可见性、断线恢复、fake managed-world 与独立真实 Provider
   managed-world smoke。真实测试在阶段推进授权下由 Agent 显式运行，运行前说明联网和费用；普通 E2E 固定 fake。

后端确定性测试必须覆盖两子任务真实重叠、父子 ID、稳定 dispatch 顺序、同角色多 dispatch、一次暂态失败后
成功、不可重试失败、两次失败降级、伪造/墓碑/越权 role ID、深度 1、最大 fan-out、停止和重启。Playwright
至少保留一个两子任务 happy path 和一个失败重试后降级路径，并断言旧客户端可忽略新增事件。迁移同时执行
SQLite 重放、PostgreSQL 离线编译；移除二次鉴权、并行上限或子角色 dispatch 过滤时，对应用例必须变红。

## 十一、C3：阶段式 Checkpoint

### 11.1 触发与生命周期

- 只有 recent history 超过明确预算阈值时触发；
- 不每轮滚动更新 summary；
- checkpoint 以 `through_message_id` 固定覆盖边界；
- 原始消息不因 checkpoint 删除或改写；
- 新 checkpoint 完成并校验前继续使用旧版本；
- 失败、取消或服务退出保留旧版本并回退到确定性裁剪；
- checkpoint 内容不得包含超出会话/角色授权的消息。

### 11.2 建议逻辑字段

最终 schema 在 C3 开始前复核，至少需要：

```text
id
conversation_id
role_id 或明确的 role-neutral scope
version
through_message_id
context_schema_version
content_json/text
content_hash
created_at
```

是否使用 role-specific checkpoint 必须用群聊投影测试决定：如果同一 transcript 对不同角色的 assistant/user
语义不可安全共用，则按 `(role_id, conversation_id)` 隔离；不能为了少存几份摘要破坏角色视角。

### 11.3 Checkpoint 是派生数据

- 可以从原始消息重建；
- 默认不进入单会话导出包；
- 导入后按新 ID 和本地模型重新生成；
- 世界备份包含数据库，因此自然包含，但不成为世界兼容性的权威事实；
- checkpoint schema 不得取代 messages 作为历史事实源。

## 十二、C4：缓存真实验收

### 12.1 离线验收

- 相同输入快照重复 build，各层 hash 和工具 schema 字节一致；
- 只改变当前消息时，稳定层 hash 不变；
- 角色配置、成员、合法 revision、工具 policy、checkpoint 变化只使预期层及其后缀失效；
- 无权限工具不因缓存目标进入 schema；
- ContextBuilder 不泄露 Prompt 到日志。

### 12.2 真实 Provider 验收

真实测试使用独立命令显式运行并可能计费。“显式”表示不得混入普通回归或 CI，不表示等待用户亲自执行；
用户指令要求推进到本阶段时，Agent 应先说明联网计费，再主动运行对应真实测试：

- 同一角色/会话连续请求，记录真实 input/cache read/cache write token；
- 单聊、M4a 串行群聊、M4b 并行编排分别留一组样本；
- hash 稳定是必须通过的确定性验收；
- Provider 命中受最小 token、TTL、账号、路由和厂商策略影响，不把某个固定命中率作为普通 CI 门槛；
- 若 Provider 声称支持缓存但实测 usage 口径不同，更新能力表和契约文档，不猜字段。

## 十三、Memory v0（非前置阶段）

### 13.1 最小数据模型

只有用户再次批准该产品能力后才实施。建议逻辑字段：

```text
memories
- id
- owner_role_id
- memory_type
- content
- supersedes_id
- active
- source_conversation_id
- source_message_id
- confirmed_by_user_id
- created_at

memory_injections
- id
- memory_id
- role_id
- conversation_id
- first_injected_for_message_id
- folded_into_checkpoint_id
- created_at
- UNIQUE(memory_id, role_id, conversation_id)
```

不增加 `workspace_id`；当前世界的数据库已经是物理 scope。`active` 与 supersede 更新必须在短事务中保持
一致，旧 Memory 保留用于追溯但不再进入新检索。

### 13.2 写入与检索

- 仅 Owner 显式确认写入；不自动监听每条聊天；
- 不保存完整工具输出、凭据或未确认模型推断；
- LIKE adapter 优先匹配稳定标识和简单关键词；
- 搜索结果必须再次校验 role 归属和 active/supersede 状态；
- Top-K、最大注入字节/token 有硬上限；
- 注入关系提交后才能把 Memory 视为该 role/conversation 已知内容；
- 不能依据模型是否在回答中复述来推断已知状态。

### 13.3 未来升级

FTS5、sqlite-vec 和 pgvector 不在本计划授权范围。升级必须保持：

```text
ContextBuilder / MemoryService
             ↓
MemorySearchBackend
       ├── Like
       ├── SQLiteFts（未来）
       ├── SQLiteVector（未来）
       └── PgSearch（未来）
```

Embedding 是可重建派生索引，不成为 Memory 权威数据。模型/维度/version 变化必须允许重建，不修改业务
Memory ID。

## 十四、会话导出/导入的解锁条件

以下条件全部满足后才恢复工作项四：

- ContextBuilder 投影规则和 `context_schema_version` 已稳定；
- M4a mentions、串行 chain 和停止语义已实现；
- W0/E0/W1a/W1b/W1c/W2a/W2b/W3 工作区、execution、Shell、仓库/worktree 生命周期已实现，主机本地绑定、命令和
  代码变更明确不进入会话包；
- M4b Orchestrator、子角色消息和父子执行的长期事实已确定；
- regenerate/revision 对历史和 stale 的语义已确定；
- Checkpoint 已明确为派生数据且默认不导出；
- 如果 Memory v0 已实施，明确单会话导出不携带角色全局 Memory，只可按未来显式选项导出引用快照；
- 导入、导出继续只允许 Owner；
- Artifact 实际内容仍按单独里程碑决定，不能因本计划默认进入导出包。

解锁后导出格式只保存业务事实，不保存 Provider cache usage、prefix hash、checkpoint、runtime execution 日志
或 WS backlog。导入后重新生成所有派生状态。

## 十五、分阶段测试矩阵

| 层级 | 必须覆盖 |
|---|---|
| Context 单元 | 角色视角投影、终态过滤、当前消息去重、确定性序列化、预算边界、unknown part 降级 |
| Cache 变异 | 打乱查询返回/JSON key/工具输入后仍稳定；修改合法 revision/policy 后必须改变预期 hash |
| 后端集成 | 第二轮真实读取第一轮历史；墓碑显示但不可回复；Owner/Guest 工具暴露和执行双层校验 |
| M4a 调度 | @A、@A@B、all、无 @、停用角色、停止整链、跨会话并行 |
| M4b 调度 | 两子任务并行、父子 ID、重试、失败降级、深度 1、伪造 role ID 拒绝 |
| WS 恢复 | 群聊多消息断开重连不丢不重；进程重启快照包含终态和队列降级 |
| Checkpoint | 边界原子性、失败回退、旧版本稳定、原消息保留、role-specific 隔离 |
| Memory v0 | Owner-only、LIKE adapter、supersede、注入记录、重复注入、越权隔离 |
| 真实契约 | cache usage 字段、重复请求样本、Provider 差异；默认 skip，不进入普通 CI |
| Playwright | 群聊 happy path、关键权限/停止失败路径、Orchestrator 降级可见性 |

安全相关测试完成后做变异验证：移除终态过滤、改变排序、绕过执行鉴权或重复注入时，对应用例必须变红。

## 十六、分阶段验收与提交边界

### C1 验收（已完成，提交 `4ea6312`）

- 第二轮 Agent 能看到第一轮终态历史；
- 相同快照产生完全相同消息序列和 hash；
- 不完整生成和当前消息不重复进入 history；
- 全量普通测试、真实页面单聊和可选真实 Provider smoke 通过。

自动验证和用户人工验收均已通过；后续变更不得在 C2 中顺带改变上述历史投影、预算拒绝或 Role 上下文
窗口语义。如确需改变，必须先回到 C1 契约更新计划与回归测试。

### C2 验收（已完成，2026-08-30 经用户人工验收）

- 日志能定位具体 context/tool 层变化；
- 不保存 Prompt 原文；
- fake usage 为空，真实 Provider usage 口径可追溯。

实现已生成可检索的 `context.loaded`、Provider 和 generation 日志。fake 与真实 Provider 均在正常世界
包装器和独立世界目录中完成两轮浏览器验证，自动断言稳定层跨轮保持、历史计数变化、Prompt 原文不落日志、
fake usage 为空以及真实 input/cache/ratio 可追溯。自动验证和用户人工验收均已通过；后续变更不得在 M4a
中顺带改变上述日志字段或 usage 口径，如确需改变必须先更新日志设计、内部协议与回归测试。

### M4a 验收（已完成，2026-09-01 经用户人工验收）

- @A 只 A 回复；@A@B 严格串行且 B 看见 A；停止取消整链；不同会话互不阻塞。

后端确定性测试覆盖无 mentions、非法/重复/超限 mentions、all 稳定顺序、成员 revision 冲突、同会话串行、
跨会话并行和停止整链；普通浏览器覆盖建群、成员管理、@ 补全和无 @ 静默；fake/真实 managed-world 均
通过。自动验证和用户人工验收均已完成；后续 M4b 不得顺带改变 M4a mentions、串行 chain、停止或成员
revision 语义，如确需改变必须先更新本计划、公开协议与回归测试。

### M4b 验收

- `0005_agent_executions` 在 SQLite 升降级、外键检查和 PostgreSQL 离线 SQL 中通过；新执行不再只把
  execution ID 藏在 queue payload 或日志里。该项作为 E0 先独立人工验收和提交。
- W0/E0/W1a/W1b/W1c 已按[仓库工作区计划](agent-repository-workspaces-v1.md)先完成设置、当前 World
  工作区、原生文件、结构化命令与审批 Shell；W2a/W2b/W3 随后分别验收并提交。子执行最终能在临时测试仓库的
  独立 worktree 中完成读取、
  补丁、测试和 diff，且没有自动 merge 或宿主路径越界旁路。
- 两个子任务真实重叠运行，消息按 dispatch 顺序稳定创建；同批子任务使用共同 base boundary，互相看不见
  兄弟执行的中间或终态输出。
- 一个暂态失败只重试一次并可成功；不可重试或第二次失败以结构化错误进入最终汇总，失败消息不被改写，
  Orchestrator 不伪造成功。
- 子角色没有 dispatch，非法/越权/墓碑角色和超过 4 个 fan-out 在服务端拒绝；停止和服务重启不会重放
  Provider 调用，父子 execution 可从数据库持久追溯。
- 普通 fake 回归、fake managed-world 和显式真实 Provider managed-world 均完成；真实 usage 只记录厂商事实，
  不以固定缓存命中率或时延作为验收门槛。

### C3/C4 验收

- 历史超预算时阶段式生成 checkpoint，不每轮滚动；失败安全回退；真实重复请求留下 hash/usage 证据。

### Memory v0 验收

- 只有 Owner 能确认记忆；LIKE 检索、supersede 和注入关系正确；没有 FTS/向量依赖。

每个阶段独立测试、人工验收、独立提交。不得把 C1、M4a、M4b、C3 和 Memory v0 堆成一个无法审阅的
大提交。未经用户人工验收不得提交。

## 十七、明确不做

本计划当前不授权：

- 自动从每条消息提取长期记忆；
- Workspace/Project Memory 多级 scope；
- FTS5、sqlite-vec、pgvector、Embedding、Hybrid Retrieval；
- 外部 Redis/Qdrant/Milvus/PostgreSQL 强依赖；
- 自建 KV Cache、Token Trie 或模型回复缓存；
- 每轮滚动摘要；
- 完整 Prompt、用户输入或模型输出持久化诊断；
- 为缓存命中绕过 Owner/Guest、工具审批或最小暴露；
- 在群聊/编排语义稳定前实现会话导出/导入或世界分发。

## 十八、已确认决策与后续阶段问题

### 18.1 2026-08-28 已确认

1. 最小不可裁剪上下文超限使用 `CONTEXT_BUDGET_EXCEEDED`，Owner/Guest 提示按 6.3 分层。
2. token 预算采用 7.4 的 estimator 优先级和保守 fallback；estimate 与 Provider usage 严格分栏。
3. 采用 7.1 的终态历史投影：done 正常进入；stopped 仅非空文本带稳定停止标记进入；error/interrupted
   默认不进入模型历史。
4. 上下文窗口改为 Role 级 Owner 可配置字段，默认 200K；前端提供 128K/200K/1M 预设和自定义，应用
   ceiling 默认 2M，具体预算始终使用有效窗口。
5. M4a 群聊无 mentions 时完全不触发角色回复，不增加默认回复角色配置；这样避免意外模型调用、费用和
   消息风暴。只有真人消息中的显式 mentions 或 `all` 才进入串行调度。

### 18.2 2026-09-01 已确认

1. M4b fan-out 前先定义 W0，完成 E0 后以 W1a 原生文件、W1b 结构化命令、W1c 审批 Shell 跑通单角色
   最小工具闭环；
   再进入 W2a Repository、W2b 只读工具和 W3 worktree/补丁，避免多个故障域一次落地。
2. Worktree 只解决并行写隔离，不是安全沙箱；任意 Bash/PowerShell 始终 dangerous 并需要 Owner 逐次审批。
3. 首版不自动 merge/commit/push，由 Orchestrator 汇总 diff、测试和冲突，Owner 决定整合。

### 18.3 到对应阶段前再确认

1. E0/M4b：采用 10.2 的单张 `agent_executions` 持久执行树；W0-W3 详细限制按独立计划 13.2 逐切片复核。
2. M4b：planning/final 两阶段、首版 fan-out 上限 4，以及“只重试暂态错误一次”的规则在 W3 完成后复核。
3. C3：checkpoint 使用目标角色模型、专用摘要模型还是确定性裁剪？首版建议角色无关摘要需单独配置。
4. Memory v0：Owner 通过独立 UI、消息操作还是工具调用确认 Memory？未确认前不实施写入入口。
5. 导出：未来是否允许显式携带“本会话引用过的 Memory 快照”？默认建议不携带。

## 十九、最终实施顺序

```text
C0 基线与内部契约
→ C1 ContextBuilder、历史正确性、稳定前缀与硬预算
→ C2 Prefix/Provider Cache 可观测性
→ M4a 群聊串行调度
→ W0 工作区与 Shell 设置契约
→ E0 持久 execution 身份
→ W1a 当前 World 工作区列表与原生文件读写
→ W1b 无任意 Shell 的结构化命令
→ W1c 任意 Shell 与 Owner 审批
→ W2a Repository Binding
→ W2b 只读文件与 Git 工具
→ W3 Git Worktree 与补丁写入
→ M4b Orchestrator 并行编排
→ C3 阶段式 Checkpoint
→ C4 真实缓存验收
→ 会话导出/导入
→ 世界分发
```

Memory v0 是 C3 之后的可选独立阶段，不阻塞上述主线；若决定让 Memory 成为导出语义的一部分，则必须
在会话导出/导入之前单独批准并完成，否则保持延期。
