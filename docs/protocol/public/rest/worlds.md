# REST 世界存档与切换

| 元数据 | 值 |
|---|---|
| 受众 | 公开（Owner 管理接口；健康检查例外） |
| 状态 | 已实现 |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/worlds/manager.py`、`backend/app/routers/worlds.py`、`backend/app/main.py` |
| 关联测试 | `backend/tests/test_worlds.py`、`frontend/tests/world-switching.spec.ts` |
| 复核日期 | 2026-09-11 |

## 世界与运行模式

一个世界对应一个物理目录，数据库、JWT 签名密钥、模型 Key 加密密钥和附件文件均归该目录所有。
正常模式由 `ROLEPLEX_WORLD` 选择世界；显式设置 `DATABASE_URL` 时进入兼容模式，数据库路径不再由
世界推导，pytest 和既有 E2E 因而不受影响。兼容模式仍公开当前世界名，但不能通过界面切换。
世界托管模式会忽略全局 `JWT_SECRET`，强制读取世界内 `.jwt-secret`，避免配置残留破坏跨世界隔离；
兼容模式仍允许显式 `JWT_SECRET`。

`GET /api/health` 无需认证，兼容新增：

```json
{"status":"ok","stream_epoch":"...","world_name":"default","world_managed":true}
```

## 列出世界

```http
GET /api/worlds
Authorization: Bearer <Owner Token>
```

```json
{
  "current":"default",
  "switching_supported":true,
  "items":[
    {"name":"default","current":true,"created_at":"2026-08-25T09:00:00+00:00"}
  ]
}
```

仅 Owner 可调用。目录缺少合法世界元数据时不进入列表，避免把任意目录暴露为可切换世界。

## 切换世界

```http
POST /api/worlds/switch
Authorization: Bearer <Owner Token>
Content-Type: application/json

{"name":"another-world"}
```

成功返回 `202`：

```json
{"target":"another-world","restarting":true}
```

服务端只在包装器提供了受控切换文件时接受请求：先原子写入目标世界，再优雅退出；包装器读取目标并
以新世界重启。前端轮询健康检查，观察到 `world_name` 变更后清除 Token 并要求重新登录。

W1d 增量：写入切换目标前关闭当前 World 新进程入口，并按
[运行实例协议](../messaging/runtime-services.md)逐项回收已登记命令/服务；未确认回收返回 409，不能假装已切换。
收尾验证及人工步骤见[服务验证指南](../../../testing/runtime-services.md)。

错误码：

- `403 OWNER_REQUIRED`：非 Owner。
- `404 WORLD_NOT_FOUND`：目标世界不存在或元数据无效。
- `409 WORLD_ALREADY_ACTIVE`：目标就是当前世界。
- `409 WORLD_SWITCH_REQUIRES_WRAPPER`：当前后端不是由世界包装器启动。

## 隔离与兼容

### W1d 当前 World 协调备份

`POST /api/worlds/backup` 仅当前 World Owner 可调用，body 为 `{"confirm_cleanup":true}`；未确认返回
409 RUNTIME_CLEANUP_CONFIRM_REQUIRED，兼容数据库模式返回 409 WORLD_OPERATION_REQUIRES_MANAGED。
与切换共用互斥入口；先冻结并回收全部登记实例，在保持启动门槛期间生成 SQLite 一致性备份，再释放门槛。
返回 ZIP 附件及 Cache-Control: no-store，包含世界数据库、附件与密钥；必须视为敏感备份，不能分享给 Guest。
不备份外部工作区目录，不自动重启已停止服务。临时导出文件在响应结束后清理，失败只返回稳定错误码。
切换一旦接受后不得被另一目标覆盖（409 WORLD_OPERATION_IN_PROGRESS）；控制文件写失败返回
503 WORLD_OPERATION_FAILED，保留失败回收门槛，允许 Owner 显式重试。目标版本不兼容必须在停止当前世界前拒绝。

离线 CLI 备份/删除在整个准备期取得 World 租约，未确认回收记录会阻止操作。删除先原子移出原世界名称再清理文件，
防止另一后端打开逐文件删除中的世界；文件清理失败可能留下 `.deleted-*` 残片，不能将其解释为完整可恢复备份。
删除运行中的 World 仍须先通过切换或正常关闭回收；不新增跨 World Token 远程删除或按 PID 强杀其他后端的能力。
租约的检查、过期文件回收与创建由 `.lease-locks/` 中的 OS 文件锁串行化，查询不删除租约；损坏租约保守拒绝，
不能把无法识别的租约当作无人使用。释放同时核对 PID 与出生身份，锁不是业务数据库写锁。

- 不允许把 Token 带到新世界继续使用；世界 JWT 密钥不同，服务端也会拒绝旧 Token。
- 世界版本不高于软件时由 Alembic 自动升级；数据库记录未知的新 revision 时，启动失败必须翻译为
  `WORLD_REQUIRES_NEWER_ROLEPLEX` 的可读提示，不得尝试 stamp、降级或修改数据。
- 世界切换不是数据库事务，也不承诺保留进行中的生成；包装器退出前沿用正常关闭流程。
