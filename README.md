# Roleplex

Roleplex 是运行在 Owner 本机上的个人多 Agent 群聊协作服务：Owner 配置 Agent、模型 Key 和 MCP，Guest 通过邀请码加入指定会话。它不是多租户 SaaS；Guest 不可见 Owner 的配置，默认不能驱动有副作用的工具。

## 当前阶段

已完成 M0/M1 基础骨架和 M2 单聊闭环：

- SQLite + SQLAlchemy 2 async，WAL、busy timeout、外键约束、单进程单 worker 边界
- Owner 原子初始化模型、JWT 7 天有效期与 token version 撤销
- 密码策略（至少 10 位且含字母、数字、符号）、弱口令登录后强制重置与改密接口
- API Key 加密落库，接口只返回 masked hint
- 用户认证、模型配置 CRUD、角色 CRUD、会话创建/列表/个人置顶归档
- 角色删除保留墓碑（历史消息仍显示原发送者），会话删除进回收站并可在 7 天内恢复
- 单聊消息发送、客户端幂等键、真实模型流式回复、停止生成；自动化测试使用确定性 fake provider
- 单聊通过统一 ContextBuilder 读取终态历史；角色上下文窗口默认 200K 并可由 Owner 配置
- WebSocket 首帧认证、按事件序号断线恢复、epoch 变化回落完整快照
- HTTP、后台生成与 WebSocket 共用关联 ID；终端可读日志与轮转 JSONL 日志统一输出
- 多世界物理存档、CLI 一致性备份与包装器热切换；每个世界独立数据库和密钥
- Alembic 迁移覆盖全部表结构，可在 SQLite 与 PostgreSQL 方言上重放
- React + TypeScript + Vite + Tailwind + zustand 的登录/工作台 UI 与实时聊天界面

M0 风险验证已补齐（只做验证，未接入产品页面）：LangGraph 防腐层与统一领域事件、工具危险
分级与执行层拦截、工具调用审计、取消传播、Windows stdio MCP 生命周期与进程树清理、
产物原始内容的 iframe 隔离。结论记录在 `docs/protocol/internal/agent-runtime.md`。

富媒体产物、群聊调度、Orchestrator 与 MCP 产品接入将在后续里程碑完成。

完整测试分层、命令、端口、数据留存、账号和日志排查见 [Roleplex 测试指南](docs/testing/README.md)。

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
双密钥和 files 目录，并保留最近 5 轮。该命令同样联网计费、关闭 trace/video，但不测试世界切换；世界
切换仍由 fake Provider 的 `test:e2e:worlds` 确定性覆盖。

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

前一日及更早日志在 runtime 启动时归档为 tar.gz（gzip level 6），保留 30 天；整个日志树目标上限
1 GiB，超限时从最旧正式归档开始淘汰。归档经过 manifest、路径、成员类型、大小和 SHA-256 校验，
删除前后都有结构化审计；当天、活跃、running 和未迁移旧日志不会自动删除。
可通过 `LOG_DIR`、`LOG_LEVEL`、`LOG_MAX_BYTES`、`LOG_MAX_SECONDS` 调整输出与轮转；归档开关和边界为
`LOG_ARCHIVE_ENABLED`、`LOG_RETENTION_DAYS`、`LOG_MAX_TOTAL_BYTES`、`LOG_ARCHIVE_COMPRESSLEVEL`。

每个 HTTP 响应都带 `X-Request-ID`；错误信封中的 `request_id` 与它相同。前端报错时可用该值
串起访问、认证/消息业务事件、后台 `generation.*` 和最终状态。WebSocket 使用
独立的 `ws_connection_id`，并在认证、订阅（含 `conversation_id`）、恢复方式和断开时记录生命周期。
模型调用结束还会记录 `provider.call_completed`，包含首分片耗时、调用总耗时、输入/输出/总 token
和缓存命中 token；厂商未提供的数据省略，不进行估算。
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
