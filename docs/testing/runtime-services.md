# W1d 后台服务收尾：验证与人工检查

| 元数据 | 值 |
|---|---|
| 受众 | 测试维护者、Owner 人工验收 |
| 状态 | 已实现的 Linux 测试；Windows 服务运行器未开放 |
| 复核日期 | 2026-09-11 |
| 事实来源 | `backend/tests/test_runtime_*.py`、`test_world_runtime_operations.py`、Playwright 专项 |

范围以[阶段计划](../plan/conversation-services-v1.md)为准，接口与限制以
[运行实例协议](../protocol/public/messaging/runtime-services.md)和[World 协议](../protocol/public/rest/worlds.md)为准。
数据目录、固定占位测试账号、轮次保留与端口沿用[统一测试指南](README.md)，不另建一套目录规则。

## 自动验证覆盖

| 边界 | 主要证据 |
|---|---|
| 配额、端口和重复调用并发 | `test_runtime_registry.py`：三层默认/修改、原子预留、重复身份与端口竞争 |
| 服务与 Shell 共存 | `test_shell_with_services.py`：同会话/共享工作区服务不阻止审批，拒绝与 Guest 不执行，批准不停止服务，配额和清理门槛仍生效 |
| 删除/停用确认和版本竞争 | `test_runtime_cleanup_faults.py`：空预览后出现新实例、旧配置停用、冻结时再次校验 |
| 取消、超时、审计与结果保存故障 | 同文件：取消发生在冻结交接或停止过程中仍处理清单；中间项超时继续后续项；结果/日志失败保留失败状态及重试关联 |
| 真实服务的故障回收 | `test_runtime_host_faults.py`：实际 HTTP 服务遇到数据库/审计/私有日志故障仍回收，恢复后可重试登记 |
| 就绪与日志边界 | `test_runtime_probe.py`：HTTP 500、可跳转到 200 的重定向、错误监听、陌生服务 200、提前退出、UTF-8 大输出、代理环境、在途 spawn 与清理竞争 |
| 时钟与进程身份 | 服务寿命使用单调时钟；内核出生身份不依赖 epoch 时间；测试只替换局部时间来源，不修改系统时钟 |
| 重启与缺失证明 | `test_runtime_recovery.py`、`test_runtime_receipts.py`：监管器单独被杀、PID 复用、证明缺失/损坏/错身份不误释放；模拟内核启动 UUID 变化，不实际重启测试机 |
| 后端/包装器硬退出 | `test_runtime_service.py`、`test_wrapper_lifecycle.py`：真实终止本轮后端/包装器，核对后代、端口及恢复登记，不重放脚本 |
| 加密与预算 | `test_runtime_logs.py`：环形/分页限制、加密尾部、预算淘汰和启动过期维护 |
| 状态与恢复事件 | `test_runtime_events.py`：状态与事件同事务，真实 WS 断线回放版本/序号，旧 epoch 快照后重读登记 |
| World 操作 | `test_world_runtime_operations.py`：Owner/Guest 备份边界、导出失败重试、下载中断清理、切换目标不覆盖、离线文件操作租约、已关闭默认世界可删除 |
| 浏览器闭环 | `test:e2e:commands` 的服务/审批/工具时间线；`test:e2e:worlds` 的实际服务→备份下载→重新审批启动→切换回收 |
| 真实模型 | 独立 `runtime-service-provider.spec.ts`：真实厂商启动 npm 固件、读取状态/随机日志验证值、回答后存活、`/ps` 停止 |

后端专项可执行：

```bash
cd backend
pytest tests/test_runtime_registry.py tests/test_runtime_cleanup_faults.py tests/test_runtime_host_faults.py tests/test_runtime_probe.py tests/test_runtime_recovery.py tests/test_runtime_receipts.py tests/test_runtime_events.py tests/test_runtime_logs.py tests/test_runtime_service.py tests/test_world_runtime_operations.py tests/test_wrapper_lifecycle.py -q
python scripts/check_migrations.py
```

浏览器专项在 `frontend/` 执行：

```bash
npm run test:e2e:commands
npm run test:e2e:worlds
```

以下独立命令会联网计费；凭据只由后端播种进入加密边界，关闭真实截图/trace/video：

```bash
npm run test:e2e:real-world -- runtime-service-provider.spec.ts
```

浏览器备份测试不把含世界密钥的 ZIP 附加到报告；下载由测试上下文清理，只保留不含密钥正文的界面截图。
普通回归继续使用 fake，真实 smoke 不能替代确定性故障测试。

## 人工验收

1. 升级前先正常关闭旧后端，让旧版服务完成回收。启动新版完成迁移后刷新前端；不要同时运行两个版本操作同一世界。
2. 启用工作区与角色服务工具，创建 HelloWorld 页面并请求以前台方式在固定 IPv4 loopback 端口启动。
   Owner 批准后确认可访问，回答结束、刷新与切换会话后仍运行。
3. `/ps` 查看状态、来源、脚本和日志；修改会话上限，停止一个服务，确认其他服务不受影响且占用更新。
   服务存活时再请求 Shell 检查（例如 curl）：应显示独立审批及占用风险，批准后执行，服务保持运行。
   原生 workspace_write 的占用限制暂未改变；经批准的 Shell 仍可能写文件，不将工具限制解释为文件隔离。
4. 创建共享工作区的两个会话服务，删除一个会话应只回收它的服务；删除工作区登记应回收关联服务但保留物理文件。
5. 在“系统与环境设置 → 世界”点击“回收进程并备份当前世界”。确认停止范围及密钥提示，检查下载成功、服务端口释放。
   备份含密钥，妥善保管；不包含外部工作区，勿直接解压覆盖运行中的世界。
6. 重新审批启动服务，再切换 World；确认旧端口释放、新世界要求重新登录。应用正常关闭也应回收本次登记实例。
7. 若显示“清理待确认”，结合日志与来源详情核查。不能把根 PID 消失、重启前端或重启后端当成完整回收证明；
   有效的延迟凭据或已证实的主机重启可用于恢复，不提供按裸 PID 强杀或盲目释放按钮。

World 删除仍使用已有 CLI：先切换离开或正常关闭目标后端，再对明确目标使用 `delete <世界名> --yes`。
实际租约或未确认运行记录会阻止删除；配置中的默认世界名本身不再错误地阻止离线删除。

旧版非终态服务可能没有回收凭据；升级不会补造证明。遇到这类记录应保留诊断，不能为通过测试或解除配额直接改为成功。
PostgreSQL 当前验证为离线编译，真实实例重放仍属于 CI/切库检查；没有原生 Windows 服务测试证据时继续关闭该运行器。
