# 多 Agent 群聊平台 Prompt Cache 优化计划

> **文档状态：参考设计，不是 Roleplex 当前实施权威。** 本文来自产品外部讨论，包含 Workspace、跨会话
> Shared Memory、FTS5、sqlite-vec、Embedding 和 PostgreSQL 等尚未纳入当前项目的假设。经 Roleplex
> 架构适配后的正式阶段、非目标、权限边界和验收标准见
> [上下文、Prompt Cache、群聊与 Orchestrator 实施计划 v1](../plan/context-cache-orchestration-v1.md)。两者
> 冲突时以独立实施计划、主计划和 `AGENTS.md` 为准。

## 1. 目标

当前平台支持：

* 一个 Workspace 下存在多个群聊和单聊。
* 一个 Role/Agent 可以同时存在于多个 Conversation。
* Role 的长期记忆在同一 Workspace 内跨群聊共享。
* 不同群聊拥有独立群介绍、独立聊天历史和独立上下文。
* 当前默认数据库为 SQLite，保持轻量、免部署。
* 数据访问层保持通用抽象，未来允许迁移 PostgreSQL + pgvector。
* 实际模型 API 不假设支持显式 cache breakpoint，只按最通用的“相同 Prompt 前缀更容易命中缓存”进行设计。

本次优化目标：

1. 最大化模型 API 的 Prompt Prefix Cache 命中率。
2. 避免动态状态和共享记忆更新导致大范围缓存失效。
3. 避免聊天历史无限膨胀。
4. 建立缓存命中可观测能力。
5. 不实现模型厂商自己的 KV Cache / Token Trie。
6. 为未来 SQLite → PostgreSQL + pgvector 保留迁移能力。

---

# 2. 核心设计原则

## 2.1 不实现真正的 Token Prefix Tree

平台不需要自己实现：

* token-level Trie；
* KV Cache；
* Prompt Cache 存储；
* 模型推理缓存。

这些属于模型 Provider 内部能力。

平台只需要让最终发送给模型的 Prompt 在逻辑上形成稳定的“前缀树”。

逻辑结构：

```text
Workspace Stable Prefix
        │
        ├── Role A Stable Prefix
        │       │
        │       ├── Group 1 Prefix
        │       │      └── Conversation History...
        │       │
        │       ├── Group 2 Prefix
        │       │      └── Conversation History...
        │       │
        │       └── DM Prefix
        │              └── Conversation History...
        │
        └── Role B Stable Prefix
                └── ...
```

Prompt 从前往后遵循：

```text
越稳定 → 越靠前
越动态 → 越靠后
```

---

# 3. Context 分层

统一 Context Builder，禁止各 Agent 自己随意拼 Prompt。

建议固定为以下层级：

```text
L0 Workspace Stable Prefix
L1 Role Stable Prefix
L2 Conversation Stable Prefix
L3 Conversation Checkpoint
L4 Recent Conversation History
L5 Retrieved Shared Memory
L6 Current Events / Current Message
```

最终请求：

```text
L0
+
L1
+
L2
+
L3
+
L4
+
L5
+
L6
```

---

## 3.1 L0 Workspace Stable Prefix

包含 Workspace 级长期稳定内容，例如：

* 平台协作规则；
* 项目公共约束；
* Agent 通信协议；
* 通用安全规则；
* 通用工具使用规则；
* 稳定日志规范；
* 稳定输出协议。

禁止放入：

* 当前时间；
* 当前任务列表；
* 在线 Agent；
* 当前状态；
* request_id；
* trace_id；
* 随机 UUID；
* 最近消息；
* 动态数据库快照。

L0 应尽可能长期不变。

---

## 3.2 L1 Role Stable Prefix

每个 Role 固定自己的角色定义，例如：

```text
role_id
角色职责
权限范围
禁止事项
长期行为规范
专业领域
```

同一 Role 在不同群聊复用同一个 L1。

例如：

```text
Workspace
   ↓
Backend Role
   ├── Backend Group
   ├── Coordination Group
   └── Supervisor DM
```

这样同一个 Role 切换不同群聊时，至少 Workspace + Role 前缀保持一致。

---

## 3.3 L2 Conversation Stable Prefix

每个 `(role_id, conversation_id)` 拥有独立 Conversation Context。

包含：

* 群聊/单聊名称；
* 群聊用途；
* 讨论边界；
* 稳定成员角色说明；
* 群长期规则；
* Conversation 固定约束。

不要把动态成员状态放入这里。

例如禁止：

```text
A currently online
B currently working
当前 task = xxx
当前 sprint = xxx
```

这些应该作为 Event 放到末尾。

---

# 4. Conversation 上下文必须按 Role × Conversation 隔离

不要维护：

```text
一个 Role = 一条巨大聊天历史
```

必须维护：

```text
(role_id, conversation_id)
```

例如：

```text
backend-01 × group-backend
backend-01 × group-fullstack
backend-01 × group-debug
backend-01 × dm-supervisor
```

分别拥有独立 Session/Conversation History。

因此同一个 Role：

```text
共享：
Workspace Prefix
Role Prefix
Shared Memory

不共享：
Conversation Prefix
Conversation History
Conversation Checkpoint
```

---

# 5. 聊天历史采用 Append-Only

Conversation History 原则：

> 已进入历史的内容尽量永远不修改，只追加新内容。

正确：

```text
message 1
assistant response 1
message 2
assistant response 2
message 3
...
```

错误：

```text
每轮重新生成整个聊天状态
每轮重写历史摘要
重新排序旧消息
修改旧消息表示方式
```

需要确保 Prompt 序列化确定性：

* message 顺序固定；
* JSON key 顺序固定；
* tool 顺序固定；
* tool schema 固定；
* 空格/换行模板固定；
* 文档排序规则固定；
* 相同数据产生相同字符串。

---

# 6. Shared Memory 不进入稳定前缀

角色长期记忆虽然跨群共享，但不能直接放在：

```text
Workspace
Role
Shared Memory
Conversation
History
```

原因：

只要 Role 在 Group A 学到一个新 Memory，Shared Memory 发生变化，就会导致这个 Role 在 Group B、Group C、DM 等所有 Conversation 后面的长前缀一起变化。

这是缓存失效扩散。

因此 Shared Memory 必须独立存储。

架构：

```text
                Shared Memory Store
                  /      |      \
                 /       |       \
Workspace → Role → Group A → History A
             ├──→ Group B → History B
             └──→ DM      → History C
```

Memory Store 是旁路知识库，不属于主 Prefix Tree。

---

# 7. Shared Memory 使用按需检索

不允许：

```text
每轮把 Role 的全部 Memory 塞给模型
```

应该：

```text
Current Message
      ↓
Memory Retriever
      ↓
筛选当前相关 Memory
      ↓
Top-K Relevant Memories
      ↓
放到 Conversation History 后面
```

最终 Prompt：

```text
Workspace Stable Prefix
Role Stable Prefix
Conversation Stable Prefix
Checkpoint
Recent History
--------------------------------
Relevant Shared Memory
Current Events
Current Message
```

这样 Shared Memory 每轮变化只影响 Prompt 尾部，不破坏前面的长缓存。

---

# 8. Lazy Memory Sync

跨群 Shared Memory 不主动广播到所有 Conversation。

例如：

Group A 产生：

```text
M900:
日志方案切换到 JSONL v3
```

只写：

```text
Shared Memory Store
```

不要立即修改：

```text
Group B context
Group C context
DM context
```

当 Group C 后续讨论日志时：

```text
Memory Retriever
→ 找到 M900
→ 本轮注入 Group C
```

这叫 Lazy Memory Sync。

优点：

* 避免无关群聊缓存失效；
* 降低输入 Token；
* 减少 Memory 噪音；
* Shared Memory 更新不会全局污染 Context。

---

# 9. Memory 一旦进入 Conversation 后应自然进入历史

例如本轮：

```text
History
+
Retrieved Memory M900
+
Current Message
```

模型回答后，下一轮历史中自然已经包含：

```text
M900
Message
Assistant Response
```

因此 M900 后续可以成为该 Conversation 稳定前缀的一部分。

不需要每一轮重新 Retrieval 同一条 Memory。

Memory Retriever 应考虑：

```text
当前 Conversation 是否已经知道该 Memory/version
```

避免重复注入。

---

# 10. Memory 使用版本和 supersede，不修改历史

Memory 尽量 Append-Only。

错误：

```text
UPDATE M103
source_id 从 SHA256 改为 BLAKE3
```

更推荐：

```text
M103 v1:
source_id = SHA256

M921 v2:
source_id = BLAKE3
supersedes = M103
```

已经看到旧 Memory 的 Conversation 不修改历史。

以后只追加：

```text
M921 supersedes M103
```

原则：

> 旧事实不从历史中删除，新事实通过 supersede/correction event 修正。

---

# 11. Conversation Checkpoint

聊天历史不能无限 Append。

需要阶段性 Checkpoint。

结构：

```text
Workspace Prefix
Role Prefix
Conversation Prefix

Conversation Checkpoint vN

Recent History

Retrieved Memory
Current Events
Current Message
```

例如 Recent History 达到阈值：

```text
50K / 80K tokens
```

执行一次压缩：

```text
Checkpoint v7
+
Recent History
↓
生成 Checkpoint v8
```

然后清空旧 Recent History，开启新 Epoch。

---

# 12. 禁止每轮滚动更新 Summary

不要：

```text
第1轮 summary v1
第2轮 summary v2
第3轮 summary v3
```

这样 Summary 位于前部时会导致后面缓存每轮失效。

采用：

```text
Checkpoint v7
保持几十轮稳定

append
append
append

达到阈值

Checkpoint v8
```

即：

> 阶段式压缩，不做持续滚动压缩。

---

# 13. Prefix Versioning

以下稳定 Prefix 都必须版本化：

```text
workspace_prefix_version
role_prefix_version
conversation_prefix_version
checkpoint_version
```

例如：

```text
workspace:v12
role:backend:v5
conversation:group-debug:v3
checkpoint:v8
```

稳定规则改变时创建新版本，而不是静默修改。

这样可以追踪：

```text
缓存为什么突然下降
哪个 Prefix 版本变化
哪些 Conversation 已迁移
```

---

# 14. Prefix Fingerprint

对稳定 Context 计算 fingerprint/hash。

建议至少：

```text
workspace_prefix_hash
role_prefix_hash
conversation_prefix_hash
checkpoint_hash
```

例如：

```text
SHA-256(canonical serialized content)
```

必须基于确定性序列化后的最终文本计算。

用途：

* 检查 Prefix 是否意外变化；
* 分析缓存下降；
* 比较不同请求稳定前缀；
* 以后进行 Prefix reuse metrics。

---

# 15. 缓存可观测性

模型调用日志增加：

```text
workspace_id
role_id
conversation_id

model
provider

workspace_prefix_version
workspace_prefix_hash

role_prefix_version
role_prefix_hash

conversation_prefix_version
conversation_prefix_hash

checkpoint_version
checkpoint_hash

input_tokens
output_tokens

provider_cached_tokens
provider_cache_write_tokens

provider_cache_hit_ratio

stable_prefix_tokens
prefix_reuse_ratio
```

定义：

```text
provider_cache_hit_ratio =
provider_cached_tokens / input_tokens
```

平台自己计算：

```text
prefix_reuse_ratio =
本次请求与上一次同 Conversation 请求的最长相同前缀 Token 数
/
本次 input_tokens
```

目的：

如果：

```text
prefix_reuse_ratio = 93%
cache_hit_ratio = 20%
```

说明 Prompt 结构没有大问题，更可能是：

* 中转路由变化；
* 上游节点变化；
* cache TTL；
* 上游账号轮换；
* Provider 缓存策略。

如果：

```text
prefix_reuse_ratio = 25%
cache_hit_ratio = 22%
```

说明是平台 ContextBuilder 自己破坏了稳定前缀。

注意：

首版不必立刻做精确 token-level longest-prefix 算法，可以先记录各层 hash/version 和 provider cached_tokens，后续再补精确指标。

---

# 16. Tools 也必须稳定

如果 API 使用 Tool Calling：

* Tool definition 顺序固定；
* JSON Schema key 顺序固定；
* 描述文本固定；
* 不要每轮动态增删 Tools，除非确实必要；
* 不要因为当前 Agent 不需要某工具就随意改变前部 Tool Schema。

动态权限最好由平台层判断，而不是每轮重构整套 Tool Definition。

---

# 17. 动态 Runtime State 必须放在末尾

禁止把这些放到稳定 Prefix：

```text
current_time
request_id
trace_id
随机 UUID
在线 Agent
当前任务列表
数据库当前快照
最新消息
当前模型负载
current branch
当前 sprint 状态
```

这些统一作为：

```text
Current Events
```

放在 Prompt 最后。

结构：

```text
Stable Prefix
Stable History
--------------------
Dynamic Events
Current Message
```

---

# 18. 数据库存储设计原则

当前项目继续保持：

```text
SQLite 默认
```

不为了 Memory 引入：

```text
Qdrant
Milvus
Redis
PostgreSQL
```

等额外强依赖。

Shared Memory 是逻辑模块，不等于独立向量数据库。

---

# 19. Memory 存储与 Vector Store 分离

保持通用 ORM/Repository，不将 sqlite-vec 或 pgvector SQL 方言泄漏到业务层。

建议：

```text
Domain
├── Memory
├── MemoryScope
├── MemoryType
├── MemoryFilter
└── MemorySearchResult

Application
├── MemoryService
└── MemoryRetriever

Infrastructure
├── MemoryRepository
└── VectorStore
    ├── SQLiteVectorStore
    └── PgVectorStore
```

业务层接口例如：

```python
semantic_search(
    workspace_id,
    role_id,
    query_embedding,
    limit,
)
```

不允许 Application 层直接出现：

```sql
embedding MATCH ...
```

或：

```sql
embedding <=> ...
```

---

# 20. SQLite 当前方案

默认本地模式：

```text
SQLite
+
FTS5
+
sqlite-vec（或兼容的 SQLite Vector Adapter）
```

其中：

```text
普通 SQLite 表
```

保存权威 Memory 数据。

```text
Vector Store
```

只保存/索引 Embedding 派生数据。

Embedding 可随时重建。

---

# 21. PostgreSQL 未来方案

未来服务化部署：

```text
PostgreSQL
+
pgvector
```

要求：

```text
Domain
Application
MemoryService
MemoryRetriever
Agent Runtime
```

不需要修改。

仅替换：

```text
SQLiteVectorStore
↓
PgVectorStore
```

以及对应 Repository Adapter。

---

# 22. 推荐 Memory 表的逻辑字段

先保持 ORM 通用，不绑定具体 SQL 方言。

Memory：

```text
id
workspace_id
owner_role_id
scope_type
scope_id

memory_type

content

importance

version
supersedes_id
active

source_conversation_id
source_message_id

created_at
updated_at
```

Embedding Profile：

```text
id
provider
model
dimensions
version
```

Vector Store 至少建立：

```text
memory_id
embedding_profile_id
embedding
```

---

# 23. Hybrid Retrieval

编程 Agent 长期记忆不能只依靠 Vector Search。

需要同时支持：

```text
Keyword Search
+
Semantic Search
```

因为：

```text
source_id
task-00128
ROLE_NOT_FOUND
conversation_id
函数名
类名
error_code
```

这类内容关键词搜索通常优于向量搜索。

推荐：

```text
Query
  │
  ├── FTS / keyword Top-K
  │
  └── Vector Top-K
          │
          ↓
       Merge/Rerank
          ↓
       Final Top-K
```

默认 SQLite 可以：

```text
FTS5 + sqlite vector adapter
```

未来 PG：

```text
PostgreSQL FTS + pgvector
```

---

# 24. Memory 写入需要筛选

不要把每一条聊天消息都直接变成长久 Memory。

需要后续单独实现 Memory Extraction Policy。

至少区分：

```text
workspace_memory
role_memory
project_memory
decision_memory
conversation_local_memory
temporary_context
```

Memory 应更倾向于保存：

* 稳定事实；
* 已确认决定；
* 长期约束；
* 用户明确要求记住的内容；
* 跨 Conversation 有价值的信息；
* 已完成的重要设计选择；
* 长期角色经验。

不应大量保存：

* 闲聊；
* 重复内容；
* 临时调试输出；
* 一次性状态；
* 已废弃但无追踪价值的信息；
* 每一条工具输出。

---

# 25. ContextBuilder 应成为唯一 Prompt 构建入口

新增统一：

```text
ContextBuilder
```

禁止 Supervisor、Worker、Role runtime 各自拼 Prompt。

示例：

```python
context = await context_builder.build(
    workspace_id=workspace_id,
    role_id=role_id,
    conversation_id=conversation_id,
    current_events=events,
)
```

ContextBuilder 内部固定顺序：

```text
1. Workspace Prefix
2. Role Prefix
3. Conversation Prefix
4. Checkpoint
5. Recent History
6. Retrieved Shared Memory
7. Current Events
8. Current User/Agent Message
```

---

# 26. 推荐实施顺序

## Phase 1：Context 结构统一

优先完成：

* ContextBuilder；
* `(role_id, conversation_id)` 独立 Session；
* 固定 Prefix 顺序；
* Append-only History；
* 动态 Event 尾部化；
* 确定性序列化。

这是缓存收益最大的部分。

---

## Phase 2：Prefix 可观测性

增加：

```text
prefix_version
prefix_hash
input_tokens
cached_tokens
cache_hit_ratio
```

先不要急着做复杂算法。

确保能够回答：

```text
哪个 Agent 缓存下降？
哪个 Conversation 缓存下降？
是哪个 Prefix 变化导致的？
```

---

## Phase 3：Conversation Checkpoint

实现：

* Conversation Epoch；
* Checkpoint；
* Recent History token 阈值；
* 阶段式压缩；
* 禁止每轮 Summary 重写。

---

## Phase 4：Shared Memory Store

实现：

* Memory Domain Model；
* MemoryRepository；
* MemoryScope；
* Memory version/supersedes；
* Memory source tracking。

首版甚至可以先只做普通 SQLite 存储。

---

## Phase 5：Memory Retrieval

先实现：

```text
Keyword / FTS
```

然后增加：

```text
VectorStore abstraction
```

默认：

```text
SQLiteVectorStore
```

未来：

```text
PgVectorStore
```

最后实现：

```text
Hybrid Retrieval
```

---

## Phase 6：Lazy Memory Sync

实现：

* 当前 Conversation 已知 Memory tracking；
* Relevant Memory 按需注入；
* 避免重复 Memory；
* Memory 第一次注入后自然进入 Conversation History；
* superseded Memory 按事件修正，而不是修改历史。

---

## Phase 7：高级缓存分析

后续再考虑：

```text
stable_prefix_tokens
prefix_reuse_ratio
prefix break location
cache regression diagnostics
```

不作为首版阻塞项。

---

# 27. 本次明确不做

本阶段不要实现：

```text
❌ 自己的 KV Cache
❌ token-level Prefix Trie
❌ 模拟模型 Provider Prompt Cache
❌ 全量 Memory 每轮注入
❌ 一个 Role 共用一条巨大群聊历史
❌ 每轮滚动 Summary
❌ 动态状态放在 System Prompt 前部
❌ 业务层依赖 sqlite-vec SQL
❌ 业务层依赖 pgvector SQL
❌ 为默认使用强制部署独立向量数据库
```

---

# 28. 最终目标架构

```text
                    Workspace Stable Prefix
                              │
                     Role Stable Prefix
                    /         |          \
                   /          |           \
              Group A      Group B        DM
                 │            │            │
            Checkpoint    Checkpoint   Checkpoint
                 │            │            │
              History      History      History
                 │            │            │
              append       append       append


                   Shared Memory Store
                          │
          ┌───────────────┼────────────────┐
          │               │                │
    Workspace Memory   Role Memory    Decision Memory
          │               │                │
          └──────────── Memory Retriever ───┘
                          │
                     Relevant Top-K
                          │
                          ▼
              注入当前 Conversation 尾部
```

底层：

```text
默认本地：

SQLite
├── 普通业务表
├── FTS5
└── SQLite Vector Adapter


未来服务化：

PostgreSQL
├── 普通业务表
├── FTS
└── pgvector
```

上层保持不变：

```text
Agent Runtime
ContextBuilder
MemoryService
MemoryRetriever
MemoryRepository
VectorStore Interface
```

---

# 29. 最重要的四条原则

如果实现过程中出现取舍，优先遵守以下四条：

1. **稳定内容放前面，动态内容放后面。**
2. **Conversation History 只追加，尽量不修改旧前缀。**
3. **跨群 Shared Memory 按需检索，不全量同步。**
4. **平台优化 Prompt 结构，不尝试自己实现模型厂商缓存。**

缓存优化的本质不是“维护缓存”，而是：

> 让不同 Agent、不同群聊、同一群聊连续请求之间，尽可能拥有长而稳定的相同 Prompt Prefix。
