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
- WebSocket 首帧认证、按事件序号断线恢复、epoch 变化回落完整快照
- HTTP、后台生成与 WebSocket 共用关联 ID；终端可读日志与轮转 JSONL 日志统一输出
- Alembic 迁移覆盖全部表结构，可在 SQLite 与 PostgreSQL 方言上重放
- React + TypeScript + Vite + Tailwind + zustand 的登录/工作台 UI 与实时聊天界面

M0 风险验证已补齐（只做验证，未接入产品页面）：LangGraph 防腐层与统一领域事件、工具危险
分级与执行层拦截、工具调用审计、取消传播、Windows stdio MCP 生命周期与进程树清理、
产物原始内容的 iframe 隔离。结论记录在 `docs/protocol/internal/agent-runtime.md`。

富媒体产物、群聊调度、Orchestrator 与 MCP 产品接入将在后续里程碑完成。

### 模型 provider 开关与契约测试

自动化测试固定使用确定性 fake provider：pytest 与 Playwright 都会显式设置
`AGENT_USE_FAKE_PROVIDER=true`，即使本地 `.env` 配了真实厂商也不会联网或产生费用。

正常启动默认使用角色绑定的真实模型配置，不需要额外设置 provider 开关。只有需要离线调试时，
才显式设置 `AGENT_USE_FAKE_PROVIDER=true`；不要把该值长期写进正常运行环境。

手动跑真实厂商时，先把凭据写进 `backend/.env`（已被 Git 忽略），再生成一份可用的模型配置与角色：

```powershell
cd backend
python scripts/seed_dev_provider.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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

真实 E2E 的 Owner 为 `realtest<时间戳>`，密码固定为 `Roleplex-Real-E2E-1`。数据库包含加密后的
真实 Key，只能用于本机核对，不要分享或提交；离开当前实例密钥后其中的模型配置无法解密。

### 测试数据库与测试账号

每轮测试使用带时间戳的独立数据库，跑完保留最近 5 轮，更早的在下一轮开始时自动清理：

- 后端：`data/roleplex-test-<时间戳>.db`（pytest 结束时会打印本轮路径）
- 端到端：`data/roleplex-e2e-<时间戳>.db`
- 真实 API 端到端：`data/roleplex-real-e2e-<时间戳>.db`（仅显式运行 `test:e2e:real` 时产生）

测试账号与本轮数据库同名可追溯：Owner 为 `test<时间戳>`，Guest 为 `test<时间戳>_<用途>`，
密码统一是 `Roleplex-Test-1234`。想查看某轮测试产生的数据，把后端指向那个库启动即可登录查看：

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

### 日志与排障

后端启动后会同时输出两份内容一致的日志：终端使用便于阅读的单行格式，机器可检索的
JSONL 按“日期 → 运行类型 → 片段起始时间”保存：

```text
logs/
└── 20260824/
    ├── runtime/20260824153000.jsonl  # 正常开发或产品运行
    ├── unit/20260824153500.jsonl     # pytest
    ├── e2e-fake/20260824154000.jsonl # 普通 Playwright，fake provider
    └── e2e-real/20260824154500.jsonl # 真实 API Playwright
```

每次后端进程启动都会新建文件，不续写上一次运行；单个文件写入跨度达到 1 小时，或下一条日志会使
文件超过 10 MiB 时，立即以新片段的起始时间创建文件；跨过本地午夜也会切片，保证目录日期准确。
同一类型在同一秒启动或轮转时使用 `-01`、`-02` 后缀避免覆盖。根路径、级别、大小上限和时间上限
可分别通过 `LOG_DIR`、`LOG_LEVEL`、`LOG_MAX_BYTES`、`LOG_MAX_SECONDS` 调整，其中大小不能超过
10 MiB、时间不能超过 3600 秒。

每个 HTTP 响应都带 `X-Request-ID`；错误信封中的 `request_id` 与它相同。前端报错时可用该值
串起 `http.request_started`、认证/消息业务事件、后台 `generation.*` 和最终状态。WebSocket 使用
独立的 `ws_connection_id`，并在认证、订阅（含 `conversation_id`）、恢复方式和断开时记录生命周期。
模型调用结束还会记录 `provider.call_completed`，包含首分片耗时、调用总耗时、输入/输出/总 token
和缓存命中 token；厂商未提供的数据保持 `null`。
例如：

```bash
# 查看一次请求的完整链路
grep 'req-login-failed' logs/20260824/runtime/*.jsonl

# 只看生成任务的开始、结束或失败
grep '"event": "generation\.' logs/20260824/{runtime,e2e-fake,e2e-real}/*.jsonl
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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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

首次注册的账号是 Owner。运行数据写入 `data/`，API Key 使用实例密钥加密，密钥文件不应提交到 Git。

## 数据库迁移边界

业务代码只使用 SQLAlchemy 通用类型和 ORM，数据库 URL 由 `DATABASE_URL` 配置。SQLite 适合 P1 单进程开发；需要多 worker 或常态化多人并发时切 PostgreSQL。迁移前运行测试并使用 Alembic 重放 schema，再导入存量数据。

表结构只由 Alembic 迁移创建：后端启动时自动执行 `alembic upgrade head`，因此新库会被记录迁移版本，已有库会拿到新增字段。开发阶段如果数据库早于迁移体系创建（没有 `alembic_version`），升级会失败，直接删除 `data/*.db` 重建即可。

修改表结构后在 `backend` 目录运行迁移检查：

```powershell
python scripts/check_migrations.py
```

它在临时 SQLite 库上重放 `upgrade head`/`downgrade base`、比对迁移结果与 ORM 模型是否一致，并按 PostgreSQL 方言离线渲染 SQL（不需要本机安装 PostgreSQL 驱动）。
