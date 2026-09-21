# Roleplex

Roleplex 是运行在本机的多 Agent 聊天与开发协作应用。Owner 在独立 World 中管理模型配置、角色和会话，为单聊与群聊角色授权本地工作区和文件工具，并为单聊提供 Shell 与后台服务。

## 当前可用范围

以下按 2026-09-21 的代码、测试入口与既有验证记录整理；本次文档复核未重跑历史验收，历史里程碑见[计划索引](docs/plan/README.md)。

| 领域 | 当前能力与边界 |
|---|---|
| 账号与配置 | 每个 World 首个注册账号为 Owner；密码策略、强制改密、Token 撤销、加密模型 Key、模型配置与角色管理已实现。Guest 权限隔离已有实现与测试，但邀请码创建、兑换和产品入群流程尚未接入。 |
| 聊天 | 单聊流式回复、停止、消息幂等、历史分页、断线恢复、会话切换缓存与阅读位置恢复；群聊按 @ 的角色顺序串行回复，无 @ 只记录消息。会话回收站保留 7 天，角色删除保留历史身份。 |
| 会话工作流 | 右栏入口与覆盖聊天区的画布；人工编辑保存角色任务、人工确认、汇合与结构化条件；支持并行和显式循环。群内协调者可从目标读图、整体写入或局部编辑，并在明确授权内启动及重规划运行；图版本、历史与人工草稿冲突可查看。运行快照、消息/diff/用量、精确停止及节点重试已接入。 |
| 工作区与文件 | Owner 登记本机目录并绑定单聊或群聊，已有会话支持换绑和解绑，工作区和角色分别授权。支持列目录、搜索、按字节/行读取、批量读取、写入、自动创建父目录、局部和多片段编辑、批量修改、版本冲突与写入排队。普通群聊按 @ 顺序串行使用原生文件工具；工作流按依赖并行，共享读与排他修改在实际操作边界协调，命令、Shell 和后台服务工具仍限单聊。 |
| 工具展示 | 正文和工具卡按执行顺序显示；Owner 可展开加密保存的私有详情、逐文件结果和 diff，Guest 只看允许的摘要。详情保留 7 天，历史缺失和展示降级会明确标记。 |
| 命令与服务 | 固定的结构化命令；每次由 Owner 批准的 Shell；Linux 会话后台服务及 `/ps` 查询、日志和停止。服务发现、写入拒绝诊断、World 操作前协调回收已实现。 |
| 决策与执行事实 | Owner 可配置新消息链共享的决策次数，支持自定义与不限，默认 8；角色执行用量按厂商实际报告展示。停止、失败、崩溃后保留必要执行证据，后续请求按当前角色最近中断回复核对并交接，不自动重放或续跑旧任务。 |
| World 存档 | 独立数据库和双密钥、Owner 界面创建空 World、CLI 管理与一致性备份、包装器切换 World、界面下载备份；外部工作区不包含在 World 备份中。 |

原型或尚未形成产品功能的内容：MCP 生命周期与工具安全已有内部验证，尚无产品配置/调用闭环；Artifact 仅有原始内容读取与隔离原型，创建、更新、附件和前端预览尚未接入。Repository/Git/worktree、世界 Orchestrator/桌宠协调入口、Checkpoint、长期记忆、会话导出/导入、重新生成和单条超长消息分块尚未实现。规划中的能力不表示已排定下一项任务。

会话内工作流从右侧“工作流”模块打开；画布覆盖主消息和输入区域，返回对话保留草稿与阅读位置，不停止运行。画布采用 React Flow，可编辑保存分支和环路，连线支持节点避障与循环外侧绕行；新图支持并行分支、汇合与显式条件循环，旧串行图保持兼容；编辑草稿自动保存在当前浏览器，刷新或重开会话后恢复，仍需“保存流程”提交服务端；操作与恢复规则见[工作流协议](docs/protocol/public/rest/workflows.md)。

Owner 在右侧“会话成员”任命协调者，通过 `/plan@协调者 <要求>` 从目标创建/调整草稿，不自动启动；“协调执行”允许规划后启动，“让协调者调整运行”限定当前运行。整图写入和局部编辑是独立工具。普通发送与 @ 规则不变，手动流程无须任命。具体边界见[工作流协议](docs/protocol/public/rest/workflows.md)。

本轮图管理实施范围见[群 Orchestrator 图管理与重规划计划](docs/plan/orchestrator-graph-control-v1.md)：补齐无需先画完图的规划入口、读图/改图工具、明确的图管理授权和运行图修订。现有并行循环与资源协调作为基础保留，共享工作区修改仍互斥；世界级、桌宠 API 和 Git/worktree 不作为前置。真实模型已验证从目标建图、整体写入、局部编辑、文件执行及运行中新增审查；本次记录见[图管理验收](docs/testing/orchestrator-graph-control.md)。

当前批量写入/编辑已取消旧的固定文件数和整批参数大小限制；单文件、读取、编辑片段和展示预算仍按[工作区协议](docs/protocol/public/rest/workspaces.md)执行。决策配置已取消旧 256 上限，`AGENT_DECISION_CEILING` 不再生效；不限次数仍受权限、上下文、单次超时、错误和用户停止约束。统一任务时间、Token 和费用限额尚未实现。

## 安装与启动

后端为 Python/FastAPI + SQLAlchemy async + Alembic，前端为 React/TypeScript + Vite。使用 Python 3.12、Node.js 22 与 npm；依赖以 [requirements.txt](backend/requirements.txt) 和 [package-lock.json](frontend/package-lock.json) 为准。Conda 或 venv 均可，不要求固定的个人环境名或补丁版本。

Linux / WSL，在仓库根目录启动后端：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend
python scripts/run_world_server.py --world default --host 127.0.0.1 --port 8000
```

Windows PowerShell，在仓库根目录启动后端：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
cd backend
python scripts/run_world_server.py --world default --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

浏览器访问 `http://127.0.0.1:51173`。首次注册创建当前 World 的 Owner；在设置中添加模型配置并创建角色即可开始聊天。正常启动默认调用角色绑定的真实模型，会使用相应厂商额度；离线调试可在后端环境设置 `AGENT_USE_FAKE_PROVIDER=true`。

Vite 开发服务器和 preview 将同源 `/api`（含 WebSocket）代理到后端。`npm run build` 生成 `frontend/dist/`；部署静态文件时，由同源网关转发 `/api`，或在构建时设置 `VITE_API_URL`。当前服务使用单进程、单 worker，直接增加 Uvicorn worker 不受支持。

## 配置与工具使用

后端在 `backend/` 启动时读取可选的 `backend/.env`，环境变量优先；`.env` 已忽略，不提交凭据。模型 Key 通常通过设置页面进入加密存储。需要从契约测试配置播种开发角色时，可在 `backend/` 运行 `python scripts/seed_dev_provider.py`，凭据变量见[测试指南](docs/testing/README.md#22-provider-契约测试)。

| 配置 | 用途与默认值 |
|---|---|
| `ROLEPLEX_WORLD`、`WORLDS_DIR` | 后端进程使用的世界名与根目录；包装器通过 `--world`、`--worlds-dir` 选择并传入，默认 `default` 和仓库下 `worlds/`，不读取这两个环境变量来替代 CLI 参数。 |
| `DATABASE_URL` | 直接启动后端时显式连接串优先，进入单库兼容模式并禁用 World 切换。包装器会清除继承环境中的该项；正常 World 运行也应从 `.env` 移除显式测试连接串。 |
| `CORS_ORIGINS` | 逗号分隔的浏览器来源，默认包含 localhost/127.0.0.1 的 51173 端口。 |
| `AGENT_USE_FAKE_PROVIDER` | 默认 false；普通自动化测试在各入口强制 true。 |
| `MAX_CONTEXT_TOKENS` | 主机上下文上限，默认 2,000,000；角色默认窗口 200,000，可在设置中调整。窗口预算不等于厂商支持的容量或输出上限。 |
| `WORKSPACE_SHELL_KIND` | `auto` / `bash` / `powershell`；auto 在 Linux 使用 Bash，在 Windows 使用 PowerShell。 |
| `WORKSPACE_COMMAND_TIMEOUT_SECONDS`、`WORKSPACE_COMMAND_OUTPUT_BYTES` | 命令默认 30 秒、合计输出 64 KiB；参数范围见[命令契约](docs/protocol/internal/workspace-commands.md)。 |
| `WORKSPACE_READ_CONTENT_BYTES`、`WORKSPACE_SCAN_FILE_BYTES`、`WORKSPACE_SCAN_TOTAL_BYTES`、`WORKSPACE_SCAN_SECONDS` | 读取和扫描预算；默认及范围见[搜索与读取契约](docs/protocol/internal/workspace-search-read.md)。 |
| `LOG_DIR`、`LOG_LEVEL` 及 `LOG_*` 轮转/归档项 | 默认仓库下 `logs/`、INFO；详情见[日志设计](docs/design/logging-v2.md)。 |
| `VITE_PROXY_TARGET` | 前端开发/preview 的后端代理地址，默认 `http://127.0.0.1:8000`。 |
| `VITE_API_URL` | 前端构建时写入的公开 API 地址；同源部署可省略。 |

完整后端配置和校验范围见 [settings.py](backend/app/config/settings.py)。不要将服务端密钥放入 `VITE_*` 变量。

工作区由 Owner 在界面登记本机绝对目录；不要求特定个人路径或预设部署白名单。使用工具前，启用相应工作区能力、角色工具，并在会话工作区入口绑定目录；群聊仅支持原生文件工具。文件内容可能发送给角色所用模型厂商。

原生文件与结构化命令限制在绑定的工作区根内。Shell 和后台服务可访问宿主其他路径与网络，是需要逐次审批的脚本能力，工作目录不构成系统沙箱。服务运行期间原生写入/编辑仍可能被占用门槛阻止；已批准 Shell 可与服务共存，具体规则见[运行实例协议](docs/protocol/public/messaging/runtime-services.md)。

Linux Shell 与后台服务已有测试和既有真实场景验证。Windows Shell 的参数及 Job 路径有实现和分层测试，原生 Windows 实机验收仍有缺口；Windows 后台服务未开放，其他 POSIX 平台未开放 Shell。Windows 后端需保留默认 Proactor 事件循环，以支持异步 stdio 子进程。

## World 与数据库

正常数据位于 `worlds/<世界名>/`，包含元数据、SQLite 数据库、JWT/API Key 双密钥及 files。每个 World 的首个账号独立成为 Owner，切换 World 后需要重新登录。

Owner 可在“系统与环境设置 → 运行世界与存储”填写世界名称并创建空 World，随后从切换列表进入。
创建不改变当前世界，也不复制账号或模型配置；首次进入新世界需注册 Owner。兼容数据库模式不支持界面创建。

在 `backend/` 管理 World：

```bash
python scripts/manage_worlds.py list
python scripts/manage_worlds.py create another-world
python scripts/manage_worlds.py backup default --output ../backups
```

旧单库 `data/roleplex.db` 可在旧后端停止后，通过 `python scripts/manage_worlds.py adopt default` 一致性接管，原目录保留供回退。正常启动使用上面的世界包装器；直接运行 Uvicorn 不提供热切换。

备份包含可解密模型 Key 的世界密钥，按敏感数据保管；不要提交或公开分享 World、测试世界、密钥及备份。外部工作区需自行备份。涉及后台服务时，使用界面的协调回收/备份流程，避免在进程未回收时直接操作存档。详细行为见[World 协议](docs/protocol/public/rest/worlds.md)。

启动自动执行 Alembic 向前迁移。新于当前软件的 World 格式或迁移版本会阻止启动，不自动降级。业务查询以可移植 ORM 为主；当前运行与 World 管理以 SQLite 为基础，PostgreSQL 只有迁移离线编译检查，尚未完成运行部署验收。切换数据库也不能直接解决进程内调度、事件广播和运行时归属的多 worker 问题。

schema 变更后在 `backend/` 执行：

```bash
python scripts/check_migrations.py
```

该脚本在临时 SQLite 上重放升级/降级、比对 ORM 和外键，并渲染 PostgreSQL 离线 SQL；它不连接 PostgreSQL 实例。真实数据使用备份和正常迁移，不能用删库重建代替升级。

## 测试与排障

按变更范围选择测试，完整入口、端口、账号和保留数据的查看方式见[测试指南](docs/testing/README.md)。常用命令：

```bash
cd backend
pytest -q
cd ../frontend
npm run build
npm run test:e2e
```

执行浏览器测试前，当前终端的 `python` 需能使用后端依赖，并在 `frontend/` 安装 Playwright Chromium：`npx playwright install chromium`。普通 pytest/E2E 使用 fake Provider；工作区工具和 World 切换另有专项入口。真实 contract、real、real-world 使用独立命令，会联网计费，按实际验证目标选择。

后端输出结构化日志；`X-Request-ID` 可关联 HTTP 错误与后台调用链。运行日志位于 `logs/runtime/`，测试报告位于 `logs/tests/`；Provider 未返回的 usage 保持未知。排查方法见[测试指南](docs/testing/README.md#八常见失败排查)，字段与事件见[观测协议](docs/protocol/internal/observability.md)。

## 文档入口

- [工程约定](AGENTS.md)：工作方式、安全边界和按风险验证。
- [文档导航](docs/README.md)：文档职责、状态和维护方式。
- [协议索引](docs/protocol.md)：公开接口与内部契约。
- [计划索引](docs/plan/README.md)：已完成阶段、历史设计与待选方向。
- [测试指南](docs/testing/README.md)：可重复运行的验证入口。

工作流并发容量由 `WORKFLOW_PARALLELISM` 配置（默认 4 个模型执行槽）；运行可选更小容量。`WORKSPACE_RESOURCE_WAIT_SECONDS` 控制资源等待（默认 30 秒，最大 300 秒）。它们独立于文件扫描线程池与写入排队，且不提供多 Uvicorn worker 支持。显式循环当前支持互不嵌套的循环域，体内可以并行；条件和回边须配置完整，不自动解释旧图。
