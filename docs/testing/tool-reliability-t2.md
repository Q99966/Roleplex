# T2 搜索定位与范围读取验证

| 元数据 | 值 |
|---|---|
| 受众 | 开发者、测试与人工验收人员 |
| 状态 | 已实现，已通过人工验收 |
| 版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-14 |
| 事实来源 | `test_workspace_search_read.py`、`test_workspace_read_many.py`、Playwright、离线扫描基准与独立真实 Provider 报告 |

## 范围与实现

T2 配套实现 workspace_search 和 workspace_read 行范围，复用当前 Owner single/managed_directory/file_tools_enabled。
搜索按角色单独启用，旧角色不自动新增工具；读写/编辑和 Shell/服务的权限与停服规则保持独立。
原生 read/search 全部形式共用有限 FIFO 准入，批次依次扣除实际返回内容，不再相加申请值拒绝小文件。
扫描分块校验 UTF-8/普通文件身份/完整 hash，保留范围片段，扫描量与返回量独立。版本变化拒绝拼接；
超过 1 MiB 的受控文本可按行读取，写入/编辑仍限 1 MiB。

参数、预算、错误与兼容只有[搜索/读取协议](../protocol/internal/workspace-search-read.md)维护；
Owner 私有详情与 UI 以[工具详情](../protocol/public/messaging/tool-details.md)为准。本阶段没有表/列/索引变化，不需要迁移。
不增加回合统计摘要、长时记忆、Git 或自动任务恢复。

## 离线基准与预算依据

backend 下执行：

```bash
python scripts/benchmark_workspace_scan.py
```

只创建无实际价值的占位文件，不读取开发目录或调用厂商；目录为 `data/scan-benchmarks/<时间戳>/files/`，最近五轮保留。
2026-09-14 的最终报告：`data/scan-benchmarks/20260914163004/report.json`。

| 场景 | 结果 | 实际返回 | 实际扫描 | Python 分配峰值 | 本轮耗时 |
|---|---|---:|---:|---:|---:|
| 两个 20 KiB 文件，32 KiB 正文预算 | 部分返回，一个文件需续读 | 32,768 B | 两个完整文件 | 219,453 B | 5.28 ms |
| 相同文件，64 KiB 正文预算 | 两个文件一次完整返回 | 40,960 B | 两个完整文件 | 175,339 B | 3.79 ms |
| 约 2 MiB 文件的末尾两行 | 返回完整两行和全文件 hash | 21 B | 2,097,173 B | 314,372 B | 106.24 ms |
| 在该文件定位标记 | 一个真实匹配，完整版本确认 | JSON 526 B | 2,097,173 B | 317,688 B | 254.84 ms |

据此选择默认 64 KiB 实际正文额度，另保留 64 KiB JSON 上限。16 MiB 单文件/64 MiB 调用扫描量用于资源保护，
有独立主机配置和硬上限；不放大写入额度。超长行、JSON 转义和大目录仍可能返回部分结果，须查看未覆盖原因。
2 个活跃槽、8 个等待位置与 3 秒等待经取消/关闭/排队测试验证；不是对任意磁盘、网络文件系统的延迟保证。
上表包含 tracemalloc 开销和首次导入/缓存差异，不推导“预算越高内存越少”；不是进程 RSS 上限，也不含数据库鉴权或 Provider 时延。

## 后端确定性验证

```bash
python -m pytest tests/test_workspace_search_read.py tests/test_workspace_read_many.py -q
python -m pytest -q --tb=short --show-capture=no
```

覆盖：

- 两个小文件分别申请 32 KiB 成功；实际预算耗尽保留全部节点，不把未读取当成功。
- JSON 转义后的完整响应限额、UTF-8 字符边界、旧 path/五字段结果、items/行模式互斥。
- 大文件小范围、完整 hash、行号/字节起点、CRLF、EOF、超长行及版本冲突；读取期间文件改变不交付旧版本结果。
- 文件名模式、文本定位、敏感文件/依赖目录/链接排除、二进制、扫描总量和无匹配/未完整覆盖的区分。
- 队列满、等待超时、取消、关闭、移交时取消；取消的等待者不会迟到扫描，槽位和等待字节数归零。
- 真工具工厂 → search 结果 → read 行参数 → 单项/混合批次详情；排队后撤销角色能力时不进入文件扫描。
- T0 小文件误拒绝断言已改为真实成功读取；原写入/diff/取消与权限测试继续回归。

首次全量回归中两项旧租用测试在消息结束、租用尚未收口的窗口读取了 ready；测试已改为有界等待真实 retained/ended_at，
未放宽状态要求。一次群聊准备请求出现已知宿主时钟回退相关的 AUTH_INVALID；群聊 fixture 复用既有测试 JWT 稳定时钟，
不修改生产 Token 校验或增加容差。兼容性检查还纠正了旧单文件缺失文件应为 failed、而非统一标为 rejected 的映射。

最终全量 **352 passed、3 skipped、12 deselected**（245.8 秒）。新增 T2 测试全部离线，普通回归没有模型计费。

## 浏览器验证

frontend 下执行：

```bash
npm run build
npm run test:e2e:commands -- search-read.spec.ts read-many.spec.ts exploration.spec.ts
```

最终 **3 passed（25.9 秒）**，使用真实前后端与确定性 fake Provider。新用例按真实工具搜索输出填入行号/hash，
没有把文件名/行号写死在 fake 模型里。验证文件工具分类、搜索开关、超过 1 MiB 的文件、Owner 搜索/行范围/批次详情、
版本冲突、刷新后旧详情、Guest 不请求私有详情，以及 1280/390px 截图与 HTML 文本不执行。
窄屏沿用现有收起侧栏操作，刷新恢复在桌面尺寸检查，避免原产品刷新后侧栏展开遮住交互。
原 read-many/exploration 用例保持兼容；搜索未完整覆盖通过共享 truncated 状态明确提示，不伪装为完整无匹配。
截图按本轮测试日志 artifacts 索引保存，原输入/输出只有 fake 占位数据，测试后服务进程清理。

## 真实 Provider 验收

运行前已说明联网和计费，使用 DeepSeek `deepseek-v4-flash`，在 frontend 显式执行：

```bash
npm run test:e2e:real-world -- search-read-provider.spec.ts runtime-service-provider.spec.ts
```

最终 **2 passed（35.9 秒）**。世界为 `data/roleplex-real-world-e2e-20260914163004/default/`，独立工作区使用同 stamp。
日志为 `logs/tests/e2e/real/2026-09-14/16-30-04_a7e09911/`。Key 只经后端加密播种，真实截图、trace、video 关闭。

搜索专用场景使用未在 Prompt 告知的文件名与行号；校验值位于匹配行之后，超过搜索上下文范围，必须读取才能回答。
实际观测 search=1、行范围 read=1；模型回答包含正确校验值，实际起始行、完整 hash 与大文件扫描均通过。
该用例是明确的工具契约流程，不以它代表模型会在所有开发任务中自然选择搜索。

完整工具集两轮场景将 search 纳入可用工具，由模型自由选择；本轮选择了 list/read/write/edit 与服务/Shell，
没有使用 search。实际批量读取 2 次、批量修改 1 次、edit 1 次、无 ID 服务列表 1 次，详情和私有 diff 核验通过；
写入拒绝诊断未触发。两轮文件、真实页面、保留内容、服务跨轮保留和正常回收均通过，未用紧急回收掩盖失败。

本轮厂商报告共 **14 次模型请求**，input=67,302、output=3,430、total=70,732、cache hit=57,472；没有估算用量或金额。
查询、匹配正文和原始模型输出不进入正式日志/报告；扫描日志仅保留实际耗时、扫描字节和原链路身份。
最终确认测试端口释放、后端进程退出。Anthropic、Windows 实机及慢速网络文件系统未验证，不由此次样本推导通用性能结论。

## 人工验收

重启后端、刷新前端；在角色“文件操作”里启用搜索与读取，并开启绑定工作区的原生文件能力。
让角色搜索一个函数/关键词后读取附近行，查看 Owner 详情中的路径、行号、版本和实际内容；再查看部分读取/未覆盖状态。
大文件可以只返回小片段，写入/编辑的大小与停服保护保持不变。完成本切片人工验收后再提交，再进入 T3。

## 人工验收反馈补充：多关键词与详情展示

2026-09-14 根据人工截图反馈补充批量字面关键词搜索与文件归组，协议仍以领域文档为准。

- 后端相关回归：`test_workspace_search_read.py test_workspace_read_many.py test_workspaces.py` 共 37 passed；最终日志字节摘要断言补充后，搜索专项 14 passed。
- 验证 OR/同行 AND、同一行多词命中只返回一次、输入词索引、只扫描一次、非法参数、旧 query 的 `|`/`&` 字面兼容与日志不保存查询原文。
- 前端 build 通过；真实前后端 fake 浏览器用例通过，覆盖同文件两个命中归组、查询条件、两个读取范围标题、历史恢复、版本拒绝、Guest 隔离及 1280/390 宽度。
- 最终浏览器报告：`logs/tests/e2e/fake/2026-09-14/17-22-03_fc8bafee/`；四张截图包含搜索与范围读取的桌面/窄屏表现，已人工式查看截图。
- 独立真实 Provider 用例通过：`logs/tests/e2e/real/2026-09-14/17-20-47_e4a4d67f/`。实际一次双词 AND 搜索、一次范围读取，命中两个词索引、全文件版本相同、大于 1 MiB 文件小范围读取及校验值核验均通过，清理成功。
- 本补充不改变同文件多个 read 范围各自扫描并校验全文件版本的行为；多词 search 合并扫描不会缓存跨调用的文件版本。

2026-09-14 用户确认当前版本通过人工审核并授权提交，包含本补充。此前全量回归结果属于补充前基线，不冒充本补充后的全量执行。
