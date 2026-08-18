# 产物原始内容读取

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 原型（读取端点与隔离已实现；产物创建/更新接口与前端预览属于后续里程碑） |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/artifacts.py` |
| 关联测试 | `backend/tests/test_artifact_raw.py` |
| 复核日期 | 2026-08-18 |

## 范围

```http
GET /api/artifacts/{artifact_id}/versions/{version}/raw
```

返回某个产物固定版本的原始内容，供加固后的 iframe 预览使用。版本是不可变的：历史消息
引用哪个版本就永远拿到那份内容。

## 鉴权与资源归属

需要 `Authorization: Bearer <访问令牌>`，且请求者必须是产物所属会话的 `user` 成员。
产物不存在、版本不存在、请求者非成员一律返回 `404 ARTIFACT_NOT_FOUND`，不区分三者，
避免泄露资源是否存在。未认证请求返回 `401`。

## 隔离响应头

产物可能包含模型生成的 HTML 与脚本，直接在本站同源上下文里执行等于把浏览器里的登录
凭据交出去。因此响应固定携带：

```http
Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors 'self'
X-Content-Type-Options: nosniff
Cache-Control: no-store
```

策略含义：禁止加载任何外部资源、禁止发起网络请求、只允许本站自己嵌套。客户端还应在
iframe 上使用 `sandbox="allow-scripts"`（不带 `allow-same-origin`）。

## 顶层打开被强制下载

只有请求头 `Sec-Fetch-Dest: iframe` 时才按内容类型渲染；其余情况（地址栏直接打开、
脚本抓取、以及不发送该请求头的客户端）都会附加：

```http
Content-Disposition: attachment; filename="artifact-{id}-v{version}.{ext}"
```

内容类型按产物 `kind` 决定（`html`、`svg` 各自的类型，`markdown`/`code` 与未知类型
一律纯文本），永远不会把未知类型回退成 HTML。

## 已知限制

浏览器给 iframe 子资源请求不会带 `Authorization` 头，因此当前端点无法直接用作
`<iframe src>`。产物预览落地时需要补一条短期有效的签名访问方式，这属于后续里程碑，
不在本协议当前范围内。
