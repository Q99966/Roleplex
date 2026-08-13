# Roleplex

Roleplex 是运行在 Owner 本机上的个人多 Agent 群聊协作服务：Owner 配置 Agent、模型 Key 和 MCP，Guest 通过邀请码加入指定会话。它不是多租户 SaaS；Guest 不可见 Owner 的配置，默认不能驱动有副作用的工具。

## 当前阶段

已完成 M0/M1 基础骨架：

- SQLite + SQLAlchemy 2 async，WAL、busy timeout、外键约束、单进程单 worker 边界
- Owner 原子初始化模型、JWT 7 天有效期与 token version 撤销
- API Key 加密落库，接口只返回 masked hint
- 用户认证、模型配置 CRUD、角色 CRUD、会话创建/列表/个人置顶归档
- `stream_epoch` + 会话 `event_seq` 内存事件环和重启快照语义原型
- `docs/protocol.md` REST/WS 主链路契约
- React + TypeScript + Vite + Tailwind + zustand 的登录/工作台 UI 骨架

聊天流式、群聊调度、Orchestrator、MCP 连接宿主任务将在后续里程碑接入。

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
