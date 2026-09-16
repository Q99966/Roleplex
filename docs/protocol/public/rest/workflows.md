# 会话工作流

| 元数据 | 值 |
|---|---|
| 状态 | 已实现首版串行流程；确定性、fake 浏览器及真实 World 协作验证通过 |
| 受众 | Owner 客户端、调度与上下文维护者 |
| 协议版本 | 1（兼容增加节点坐标；保存允许通用图，执行仍限串行） |
| 事实来源 | `workflows/models.py`、`workflows/service.py`、`workflows/schemas.py`、`routers/workflows.py` |
| 测试 | `tests/test_workflows.py`、`frontend/tests/commands/workflow-canvas.spec.ts`、`frontend/tests/real-world/workflow-provider.spec.ts` |

## 权限与入口

所有 REST 端点以 `/api/conversations/{conversation_id}/workflows` 为前缀，要求当前 World Owner，且仍为自己创建的存活会话成员。
Guest 不获得定义、任务配置、目录、私有事实或控制权，仍可在其可见会话中接收公开消息及状态提示。
未知会话和跨会话资源返回 404，非 Owner 返回 403。单聊与群聊共用接口；群聊仍仅开放原生文件工具。

`GET /`（实际为无末尾斜杠的前缀本身）返回 `{definitions: [...], runs: [...]}`，`Cache-Control: no-store`。
它是刷新、重连和事件缺口后的完整控制快照。流程数据不放入普通聊天消息快照；旧客户端可以忽略新增事件。

## 定义与保存

`PUT /definitions/{definition_id}`，ID 为调用方生成的 1–64 字符 ASCII 字母/数字/下划线/连字符标识。
客户端为新草稿生成一次 ID，避免重发创建重复定义。请求示例：

```json
{
  "name": "开发与审查",
  "expected_revision": 0,
  "graph": {
    "nodes": [
      {"id":"develop","kind":"role","title":"开发","role_id":1,"task":"完成本步任务","expected_output":"结果说明","inputs":[]},
      {"id":"check","kind":"approval","title":"人工核对","role_id":null,"task":"","expected_output":"","inputs":["develop"]},
      {"id":"review","kind":"role","title":"审查","role_id":2,"task":"审查上游结果","expected_output":"","inputs":["develop"]}
    ],
    "edges": [["develop","check"],["check","review"]]
  }
}
```

返回 `{id,name,revision,graph}`，每次保存 revision 加一。新定义 expected_revision=0；已有定义必须匹配当前版本。
节点是任务，不是角色；同一个角色可重复出现在多个节点。角色必须存活、启用、属于 Owner 且在本会话内。
保存允许分支、环路和暂未连接完整的有向图；拒绝重复 ID/边、未知连线端点、空任务或缺失角色。
inputs 必须引用现有节点且不重复。启动时必须是覆盖全部节点的唯一串行路径，按连线拓扑冻结实际执行顺序，
并检查 inputs 都来自此前步骤；节点数组顺序和坐标不作为执行授权。没有额外固定节点数限制。

节点兼容新增可空 `position: {x: number, y: number}`，坐标必须是有限数值；缺省/null 按默认横向布局显示。
坐标保存在现有 graph JSON 中，进入运行快照，不新增数据库列或迁移。
节点另有可空 `color`，仅接受 `#RRGGBB` 六位十六进制颜色；缺省/null 按节点类型使用前端默认色。颜色不影响调度或权限。修改新版布局不影响旧运行。
React Flow 负责画布的节点拖动、连线、选择和视口操作；拖动只修改位置。
串行前移/后移显式重建链，连线编辑形成完整串行路径后对齐节点顺序，并提示移除不再属于上游的结果引用。
界面仅允许删除没有任何连线（含自环）的节点，不自动连带删除或重连边；删除节点会清理指向它的结果引用。保存不要求图当前可运行。

## 启动与冻结

`POST /runs` 返回 202。请求为 `{definition_id,expected_revision,request_key,input_text}`。
request_key 为 1–64 字符；同一会话同键同请求返回原运行，同键不同正文返回 `WORKFLOW_REQUEST_CONFLICT`。
不支持运行的分支、环路、断路返回 422 `WORKFLOW_EXECUTION_UNSUPPORTED`，在创建触发消息、运行、预算或任何模型请求之前拒绝。
本会话只允许一个活动运行，新启动或历史运行重试都检查此约束。普通聊天仍可使用独立消息链。

启动同事务保存运行快照、触发消息、共享 chain 和现有 WorkflowBudget。冻结定义版本、工作区身份和规范根，并保存启动时实际可用工具名称作为撤权检查基线，不冻结永不过期的授权。
后续定义保存不改变此运行。input_text 与节点 task、expected_output、本次修订要求组合成每步明确的用户消息，不修改角色全局 system prompt。

运行返回：id、definition_id、definition_revision、name、graph、status、revision、cursor、error_code、workspace_binding_id、input_text、attempts、decision_limit、used_decisions、created_at。
cursor 为当前或下一节点的零基索引；到达节点数表示串行路径结束。名称和 graph 来自冻结快照。
attempts 保留全部历史尝试：id、node_id、number、status、current、upstream_ids、retry_source_id、instruction、message_id、generation_id、execution_id、error_code、usage、created_at、ended_at。
usage 仅汇总该 execution 的厂商报告；任一必要观测缺失则对应 Token 总量未知。人工节点无 generation/execution，usage 未知，不伪造调用。

## 状态与控制

运行状态：queued（待派发）、running、waiting（人工确认）、stopping、stopped、failed、interrupted、blocked、completed。
权限复核包含启动时实际可用工具的撤销；权限被关闭后先阻止派发，已提交事实保留。节点尝试另有 pending（已建立身份、未派发）。completed 只表示本次执行结束，不表示模型任务经过人工验收。
工具是否提交、是否已修改文件，继续以原消息工具卡、加密详情和文件证据为准。

`POST /runs/{run_id}/control` 返回更新后的运行，请求统一携带 expected_revision：

- `action=confirm`：同时携带当前 waiting 的 attempt_id，仅接受一次确认，继续下一步。
- `action=stop`：先保存 stopping 封闭后续派发，再精确停止本运行 chain；等待 execution 和工具收口后标记 stopped。没有活跃 generation 的人工等待同样可停止。
- `action=resume`：仅在所选节点已成功结束、下一节点尚无当前尝试且上游均已完成时，显式执行剩余步骤；不重跑已完成上游，不续额。已有失败/停止尝试必须核对后 retry。
- `action=retry`：携带当前 attempt_id、acknowledge_facts=true、instruction（可空）、rerun_downstream（默认 false）。仅终态运行允许；创建新尝试/新 execution，保留旧事实，不复用旧审批或重置预算。

重试上游后，原下游尝试保留但 current=false。rerun_downstream=true 明确授权串行重新执行后续节点；false 仅执行所选节点，随后停在 stopped，后续旧结果不作为当前成功。
仅选择的上游尝试作为输入；输入未完成或失效时拒绝。无自动重放整链、条件分支或自动循环。

确认、停止和重试在单进程控制锁及持久 revision 边界串行化；竞争的旧版本操作返回 `WORKFLOW_REVISION_CONFLICT`。
不是全库写锁，也不提供多 worker 运行保证。排队与实际执行之间仍复核运行、资源和权限；重复唤醒由原 QueueJob 状态拒绝。

## 查看与核对

- `GET /runs/{run_id}/attempts/{attempt_id}/message`：准确返回原消息（或尚无消息时 null），工具详情仍通过原接口获取。不能用当前聊天末尾替代历史尝试。
- `GET /runs/{run_id}/attempts/{attempt_id}/facts`：返回 `{attempt_id,text}`，只读核对该尝试的已授权文件证据与当前版本。没有记录、过期、prepared 或保存失败保持未知；不从状态推断未执行。

两者均 no-store，且校验运行/尝试/消息归属。事实核对支持群聊对应角色，但不把别的角色完整私有工具详情注入当前角色。
重试 ContextBuilder 在执行前再次核对准确来源；不是“最近本角色中断回复”。普通聊天的原中断来源语义保持不变。

## 调度、资源与恢复

角色节点复用 single/group_role、原会话调度器和唯一消息 reducer。前一步 execution 收口后才派发下一步，人工等待释放 worker、文件 lease 和事务。
上下文只投影显式选择的上游终态消息。旧尝试不会从普通历史混入本轮输入；所需上游结果装不进窗口时拒绝，不静默丢弃依赖。

运行与节点重试使用同一冻结预算，确认与切换角色不续额。新运行按当前 World 配置取得新预算。
运行期间（含 waiting）禁止工作区换绑/解绑。成员撤销、角色停用、资源失效、会话删除或 World closing 封闭后续派发并收口；原文件与服务事实不回滚。
正在执行的工具仍遵守逐次权限和服务占用门槛。

浏览器保存后从服务端快照恢复；状态通知使用现有会话 WS 的 `workflow_updated`，负载仅含 run_id、status、revision。客户端可重复读取快照，按版本丢弃迟到结果，并以有限轮询补偿事件/连接缺口。
后端启动将 queued/running/stopping 运行标为 interrupted，不自动重放；waiting 可恢复展示，但确认前重新鉴权与资源复核。

## 错误与验证

本领域错误码见[注册表](../../error-codes.md)。字段/图结构校验使用既有 422 VALIDATION_ERROR，details 仅保留 type/loc/msg 定位 graph 或 position，不回显非有限输入或异常 ctx；权限与资源错误不携带私有正文。
迁移 0019 及数据归属见[数据模型](../../internal/data-model.md)。测试和保留目录见[测试指南](../../../testing/README.md)。

## 前端编辑入口

流程级配置与编辑/运行操作统一位于右侧“工作流”，画布保留名称、返回对话和视图控件。
Tab 只在选中且聚焦的编辑态画布节点上插入角色任务；原节点的出边改由新节点承接，原节点连到新节点。
类型切换仍使用保存定义接口；人工节点 role_id 必须为 null，角色节点必须关联当前会话合法角色并填写任务。
文字、连线、位置和自定义颜色可保留，既有运行的冻结快照不受新定义修改影响。
