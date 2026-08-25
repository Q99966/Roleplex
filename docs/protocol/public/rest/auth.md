# 认证、密码策略与强制重置

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现 |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/auth.py`、`backend/app/security/passwords.py`、`backend/app/security/tokens.py`、`backend/app/security/credentials.py`、`backend/app/schemas.py` |
| 关联测试 | `backend/tests/test_password_policy.py`、`frontend/tests/m1-workspace.spec.ts` |
| 复核日期 | 2026-08-20 |

## 范围

账号注册、登录、查看本人资料、修改密码、登出，以及"当前口令不符合密码策略时强制重置"的拦截规则。WebSocket 首帧认证的帧格式见 [WebSocket 会话事件流](../websocket/conversation-stream.md)，其待改密拒绝规则在本文档定义。

## 凭据传递

REST 请求使用 Bearer 令牌：

```http
Authorization: Bearer <访问令牌>
```

WebSocket 通过首帧传递令牌，不得放入查询参数：

```json
{"type":"auth","token":"<访问令牌>"}
```

令牌有效期七天。用户的 Token 版本变化后（登出、改密），此前签发的令牌立即失效。

## 密码策略

服务端 `backend/app/security/passwords.py` 是唯一权威，注册与改密共用同一套校验：

| 要求 | 说明 |
|---|---|
| 长度 | 至少 10 个字符，按字符计 |
| 字符类别 | 必须同时包含字母、数字和符号 |
| 符号定义 | ASCII 可见的非字母数字字符；空格允许出现但不计作符号 |
| 编码上限 | UTF-8 编码不超过 72 字节 |

72 字节上限来自 bcrypt：超出部分被静默截断，导致更长的密码与其前 72 字节等价，因此必须在入口拒绝而不是任其截断。

客户端可以镜像这套规则做实时提示（`frontend/src/components/PasswordRequirements.tsx`），但最终判定以服务端为准。

## 注册

```http
POST /api/auth/register
```

```json
{"username":"占位用户名","password":"<明文密码>","nickname":"占位昵称"}
```

首个成功注册者原子地成为 Owner，`is_owner` 不接受客户端提交。密码不合规时返回 `422 PASSWORD_POLICY_VIOLATION`，不返回笼统的 `VALIDATION_ERROR`。

## 登录

```http
POST /api/auth/login
```

响应：

```json
{
  "access_token": "<访问令牌>",
  "token_type": "bearer",
  "user": {"id": 1, "username": "占位用户名", "nickname": "占位昵称", "avatar": null, "is_owner": true},
  "password_reset_required": false
}
```

**弱口令不拒绝登录。** 分发出去的世界使用初始弱口令，接收方必须先能登录才有机会改密。口令不符合当前策略时：

- 响应体的 `password_reset_required` 为 `true`；
- 签发的令牌内部带待改密标记，后续请求据此被拦截。

客户端读取响应体字段即可，不需要也不应解析 JWT。

## 强制重置的拦截规则

带待改密标记的令牌采用**默认拒绝**：除下列两个接口外，所有 REST 接口返回 `403 PASSWORD_RESET_REQUIRED`，新增路由自动受保护。

| 接口 | 待改密令牌 | 原因 |
|---|---|---|
| `GET /api/auth/me` | 放行 | 重置页需要展示当前账号身份 |
| `POST /api/auth/password` | 放行 | 否则无法完成重置 |
| 其余全部接口（含 `POST /api/auth/logout`） | `403 PASSWORD_RESET_REQUIRED` | 默认拒绝 |

WebSocket 首帧认证同样拒绝带标记的令牌，连接以关闭码 `1008` 关闭；否则弱口令账号虽然进不了 REST 接口，却仍能订阅会话事件流。

## 修改密码

```http
POST /api/auth/password
```

```json
{"current_password":"<当前明文密码>","new_password":"<新明文密码>"}
```

流程：校验旧密码 → 按策略校验新密码 → 落库 → 递增 Token 版本使旧令牌失效 → 返回新令牌。

响应体与登录一致，其中 `password_reset_required` 为 `false`。客户端必须用响应中的新令牌替换本地令牌，否则后续请求会被判为已撤销。

## 查看本人资料与登出

```http
GET /api/auth/me
POST /api/auth/logout
```

`me` 返回公开用户资料。`logout` 递增 Token 版本使全部已签发令牌失效，返回 `204`。

## 错误

| 错误码 | 状态码 | 含义 |
|---|---|---|
| `PASSWORD_POLICY_VIOLATION` | 422 | 新密码不满足策略；`error.details` 逐条说明未通过的要求 |
| `PASSWORD_RESET_REQUIRED` | 403 | 当前令牌被标记为必须先改密 |
| `PASSWORD_UNCHANGED` | 409 | 新密码与当前密码相同 |
| `USERNAME_TAKEN` | 409 | 用户名已被占用 |
| `REGISTRATION_CONFLICT` | 409 | 注册事务冲突 |
| `AUTH_REQUIRED` | 401 | 缺少凭据 |
| `AUTH_INVALID` | 401 | 凭据无效，或改密时旧密码不正确 |
| `AUTH_REVOKED` | 401 | Token 版本已变化 |
| `OWNER_REQUIRED` | 403 | 该接口仅 Owner 可用 |

`PASSWORD_POLICY_VIOLATION` 的错误信封在通用字段之外附带 `details` 数组：

```json
{"error":{"code":"PASSWORD_POLICY_VIOLATION","message":"PASSWORD_POLICY_VIOLATION","request_id":"占位标识","details":["至少需要 10 个字符","需要包含符号"]}}
```

## 兼容性

`password_reset_required` 是 `TokenResponse` 的新增字段，默认 `false`，属于兼容新增。旧客户端忽略该字段时仍可登录，但会在后续请求上收到 `403 PASSWORD_RESET_REQUIRED`，应按错误码引导用户改密。

令牌内的待改密标记属于服务端内部实现，不承诺 wire 兼容；客户端不得依赖 JWT 载荷结构。
