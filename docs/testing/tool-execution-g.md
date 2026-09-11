# G 工具执行与 diff 候选验证记录

| 元数据 | 值 |
|---|---|
| 受众 | 后端、前端与测试维护者 |
| 状态 | G1 已验收提交；G2 试验完成，G3 建议随后获确认；本文件保留 G 阶段实测记录 |
| 复核日期 | 2026-09-11 |
| 范围 | [G/D/E 计划](../plan/code-diff-multiline-v1.md)；不实现 D/E |

## 一、共同规则矩阵

本轮没有新增授权系统或撤下脚本能力。新修正复用工作区授权与审批校验，规则事实来源如下：

| 规则 | 实现关口 | 证据与边界 |
|---|---|---|
| Owner、角色归属、有效会话与角色成员 | `workspaces/tools.py::_authorized_service`；审批复用 | 补齐 Owner/角色成员撤销复核；`test_tool_execution_policy.py` 的 Shell/服务 × 两种成员身份 |
| execution/generation、停止、lease/根、工作区配置 | 同上；`approvals.py::authorized_execution` | 复用原规则；已有 `test_workspace_commands.py` 配置与身份矩阵 |
| 各工具开关独立 | `_enabled_tools`、服务与 Shell 各自配置 | 不把 Shell 开关当总执行开关；`test_runtime_service.py` 仅启用服务仍可启动 |
| 审批身份、digest、过期、重复决定 | `approvals.py`、`routers/approvals.py` | 复用既有 Shell/服务审批测试，不引入跨脚本语义黑名单 |
| 宿主准备期间撤销能力 | `runtime/manager.py::_host` 创建前及交付脚本前 | 新集成测试在 waiting_ready 登记时撤销角色工具；脚本不交付，空监管进程正常回收 |
| 配额/端口与关闭范围 | `runtime/registry.py` admission、reserve、check_start | 复用短事务竞争与 cleanup gate；原生写仍受服务占用限制 |
| 服务 ready 后独立管理 | runtime 管理接口、服务身份与成员授权 | 不再要求来源 generation running；已有服务跨 generation、停止、World 操作测试 |
| 共享工作区的 Shell 共存 | `test_shell_with_services.py` | 保留已批准脚本能力、配额与清理门槛，不宣称写入隔离 |
| 私有脚本/输出不外泄 | 独立审批/详情接口，安全 WS 元数据 | 不改事件或详情字段；新增文本说明只表述能力边界 |

Shell 在复核不通过时沿用 409 WORKSPACE_TOOL_NOT_AVAILABLE；服务审批维持原有拒绝并释放预留的语义。
本轮未统一二者已有响应形态，更没有把拒绝改成未处理异常。协议说明见对应 Shell 与运行实例领域文档。

## 二、read → 更新的 hash 闭环

`WorkspaceFileService.read` 每次读取有界普通文件，先取得本次完整字节内容，再返回指定片段和全文件 SHA-256。
实际 GuardedTool 调用验证：16 字节片段、下一片段及 64 KiB 页都返回同一个全文件 hash，不是片段 hash；
外部修改后重新读取产生新 hash，携带旧 hash 的 write 被拒绝，当前文件保持不变。

可复用现有 read 返回格式，无需新增取 hash 工具。**edit 尚未实现**，本轮证明的是其输入来源和已有 write 冲突机制，
不能声称已经验证 read → edit 成功调用。E 仍需覆盖唯一/多处匹配和提交前版本复核。

## 三、G2 diff 选型试验

后端可重复命令：在 `backend/` 运行 `python scripts/check_diff_candidate.py`。
只生成占位数据，不读取用户文件，不启动产品运行器、不写正式业务日志；输出有界统计，无原文。

在本机 Python 3.12 的一次测量中（不是性能 SLA）：

| 样本 | 结果 | 观察 |
|---|---|---|
| 新建、空文件、不变、中文/换行/控制字符 | 完整记录 | 重构结果与目标一致 |
| 前后合计 216000 字节的代码，小改一行 | 完整，输出 549 字节 | 计算约 2.34ms；含 spawn/IPC/回收约 32.71ms |
| 130 KiB 文件的小改 | 启动计算前超预算 | 前后合计 266240 字节，大于候选 256 KiB |
| 接近 1 MiB 文件的小改 | 启动计算前超预算 | 不能宣称支持全部允许写入的文件 diff |
| 高重复行，合计约 49 KiB | 触发 1 秒截止并回收 | difflib 不宜直接在事件循环同步计算 |
| 全量替换，各 1800 行 | 截至 1000 展示行 | 完整统计仍可用；输出约 26 KiB，明确部分展示 |
| 80 KiB 超长单行替换 | 部分记录但无正文行 | 行本身超过输出预算；不把空展示解释为无变化 |
| 取消计算 | 进程已回收 | 不仅取消等待对象 |

独立准入试验使用标准库验证候选“两计算、四等待”：第七项拒绝，四个等待项在 250ms 后降级，
本样本排队原文峰值 196000 字节，结束为 0，两个计算进程回收。它不证明生产公平性、OS 内存硬上限或完整调度，
没有接入产品；候选容量上限下仍需计入 active/queued 数据和 Python 对象/进程本身开销。
仓库当前没有可直接复用的有界 CPU 执行池；已有会话队列和 WS 队列用途不同，不应拿来充当 diff 计算调度。
D 如采用此方案，只需最小有界技术执行适配，不新增业务 execution、调度系统或 Trace。

前端源试验保存在 `frontend/experiments/diff-view/`，与产品 src 和依赖清单分离。实际安装/运行位于
临时目录 `/tmp/roleplex-diff-spike-B6HYu4`，未修改产品 package.json/package-lock.json。
候选元数据核验：react-diff-view 3.3.3（MIT，React peer >=16.14.0），jsdiff/diff 9.0.0（BSD-3-Clause）；
react-diff-view 的依赖仍包含 diff-match-patch、gitdiff-parser、lodash 等，采用前不能只计算其入口文件大小。

- React 18.3.1、TypeScript 编译及 Chromium 浏览器试验通过；统一行内显示、折叠、中文/emoji 文本保留通过。
  `<script>` 仅显示为文本，未执行。截图已检查；试验机 emoji 字体显示不完整，不将代码点保留等同字体覆盖。
- 1000 展示行约 27.6ms 同步解析后渲染，2000 行压力样本约 41.1ms；不是完整绘制时延或所有设备保证。
- 候选演示包约 198776 字节，gzip 64496；同配置 React 基线约 143427/gzip 45976，差值约 55 KiB/gzip 18 KiB，
  另含 CSS。产品应按需加载，最终增量需在实际集成构建重新测量。
- `parseDiff` 不能直接假定接受任意 unified patch：缺少 Git 文件头的 jsdiff 输出曾触发解析异常，补齐头后试验通过。
  D 应通过项目自己的有界结构化记录适配 Hunk，不能将第三方解析器的私有表示当协议，不能对截断 JSON/patch 盲目解析。
- jsdiff 重复行样本约 8.3ms，具备库级限制选项，但本次未采用到 Python 后端；不为此新增常驻 Node 计算进程。

建议后续采用 **受限 difflib 计算 + react-diff-view 展示适配**，不手写通用 diff 算法。
G3 建议随后已获用户确认，D 沿用当前预算，write 的大文件 diff 会明确降级；E 可另验证利用已确认匹配位置生成局部 hunk，
避免大文件小修改无展示。未验证前不能承诺这条优化已可用，不静默提高预算或绕开记录准确性。

## 四、自动测试与真实测试状态

- 后端完整回归：216 passed、3 skipped、8 deselected；包括新增授权及 hash 测试。
- 普通 fake 浏览器完整回归：45 passed；服务/审批专项 2 passed；build 通过。
  最后补测授权/hash 6 passed、服务与 h1 比较专项 2 passed、fake managed-world 服务/备份/切换 2 passed；
  测试前后端和候选计算进程已退出，相关测试端口释放。
  `test_runtime_probe.py` 是直接构造 Host 的探针单元层，为新授权依赖提供显式替身；真正授权使用新集成测试，
  没有在生产代码加测试旁路。全新数据库模块重载后再安装该替身，保留真实探针/进程/清理断言。
- 真实测试沿用 `runtime-service-provider.spec.ts`，不新增重复完整场景；开启当前全部文件、结构化命令、Shell、服务工具。
  本轮限制具体可批准的脚本在已授权测试范围内，但不关闭其他工具。两轮用户消息分别创建页面/启动服务、局部修改/预览。
- 第一轮运行 `17-00-02_0214629c` 漏处理模型追加的检查审批而超时，正常回收通过。
- 第二轮运行 `17-03-07_4f2fabde` 暴露点击批准未等 HTTP 决定完成的测试竞态，达到总超时，
  用例正常回收未确认；包装器 teardown 另行收尾，不能将该轮改写为正常回收通过。
- 上一轮最后一次 `17-09-13_cb524379`：两个 generation 均正常结束；实际路径包括 read/write、启动服务及 Shell 检查，
  第二轮还包含服务状态查询、停止、write、重新审批启动。第一轮 generation 结束后的实际页面已验证，
  最终正常回收通过，但第二轮文件断言错误地替换了 title 中的首个 HelloWorld，导致整体仍报失败。
  只读解密本轮两次 write 业务记录、核对受控文件后确认实际只改 h1、其余保持；不输出源文/凭据。
  随后改为精确 h1 比较并补确定性用例；该次失败记录保留，不以后续通过改写历史结果。
- 上一轮按有界执行约束停止追加计费测试。当时真实完整验收未全绿；用户再次要求继续后，执行下述一次补验。
  新版测试将功能结果、工具路径观察和正常清理分别输出为有界 JSON 附件；失败仍保留明确阶段，不吞掉回收失败。

### 2026-09-11 真实两轮补验通过

用户授权继续后，运行一次 `npm run test:e2e:real-world -- runtime-service-provider.spec.ts`，1 passed（约 28.6 秒整轮）。
报告：`logs/tests/e2e/real/2026-09-11/17-28-15_baf51439/summary.json`；
世界：`data/roleplex-real-world-e2e-20260911172815/default`。
独立路径观察附件：同轮 `artifacts/diagnostics/__70309682.json`。

- 功能结果：两轮用户交互均完成；第一轮 generation 终态后托管服务仍就绪且页面可访问；第二轮实际文件与浏览器
  页面均验证只修改 h1，保留 title、其他结构和随机校验内容。刷新后 `/ps` 可读，最终正常停止及端口释放通过。
- 工具路径：第一轮实际调用 list/read/write/start_service/run_shell；第二轮调用 read/stop_service/write/start_service/run_shell。
  原生写入的停服和新服务身份/审批闭环通过；未要求模型使用尚不存在的 edit。
- 未覆盖声明：本轮真实模型未触发 run_command、service_status、service_logs，不据此宣称这些工具已通过本轮真实验证；
  它们的既有确定性测试结果另计。workspace_edit 和原生 diff 均尚未实现，不计为已覆盖。
- 本轮用例正常回收为 true，未执行用例级紧急清理；测试前后端退出，相关测试端口释放。
  既往正常清理未确认的失败轮仍保留原结论。本轮没有追加重跑。
- 同时复核现有 h1/title 确定性用例，1 passed；本轮未修改产品功能或新增重复场景。

无 schema 变化，无数据库迁移或 Windows 服务扩展。用户在补验通过后确认本版可提交，G1 人工验收通过；
G 提交当时 D/E 尚未实施；后续用户已确认 G3 并授权 D，最新实现/验收状态见[总计划](../plan/code-diff-multiline-v1.md)。
本文件中的 G 试验不追溯改写为产品 diff 验证。
