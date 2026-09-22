# 主动历史检索与原文回读

状态：总体计划 C 已实现。Memory 首版是当前 World 的授权历史检索，不是自动抽取长期记忆，也不在每轮自动塞入检索片段。原消息、有效摘要及其引用可作为来源。

## 工具与授权

Owner 在角色“内置工具 → 历史检索（只读）”显式启用 memory_search / memory_read，沿用 builtin_tools 配置、共同能力快照和实际工具工厂。旧角色不会隐式多出工具；读取不依赖文件工作区。两者加入内置只读 safe 白名单，每次调用仍检查真实 execution、generation、角色配置、当前成员和工作流 allocation。

普通执行使用角色已启用集合，工作流工作节点还须在本次分配中获得这些工具；群协调的图管理授权不隐含检索权。工具参数不包含 World、Owner、角色或 execution 覆盖。运行结束后的旧对象无法继续读取。

可读来源是以下范围的交集：同一物理 World、与目的地相同 Owner、未删除的源会话、触发者和目标角色仍为源成员，并且源会话包含目的地的每一位当前成员。角色能读单聊不等于可把它带进群；只因曾加入过某群也不产生永久权限。人工接口同样按选定角色及回复目的地筛选，不使用 Owner 全世界读取权限扩大结果。

当前会话的工具检索遵守宿主冻结的可见消息上界，排除本次输入自身；普通群聊允许该边界内同 chain 的前序终态。关联会话现场鉴权。返回数据注明消息/摘要、发言者、状态、版本和工作流来源；旧轮次历史不能冒充当前节点的明确上游/判断结果。已有 ContextBuilder 不因此自动注入其他任务历史。

检索返回前和回读时复核权限/来源。下一次模型调用前还复核本执行已读取的来源及能力，撤权、修订或失效时以 CONTEXT_SOURCE_CHANGED 停止后续调用，保留已发生的工具事实。原消息是事实来源，派生缓存/旧签名不能代替当前授权。

## Search

工具 `memory_search`；Owner 人工接口 `POST /api/conversations/{id}/memory/search` 使用相同参数，额外要求 role_id。

| 参数 | 当前边界 |
|---|---|
| query | 必填，1–512 字符，中文、英文或代码关键词；可组合和改写查询 |
| scope | current（默认）或 related（可共享的关联会话） |
| kinds | message / summary 数组，默认两者，最多两项 |
| limit | 默认 6，范围 1–20 |
| cursor | 可选服务端游标，最多 2048 字符 |

检索适配层使用 ORM 的关键词匹配与排序：保留英文/代码标识符，对中文长词组补充双字匹配，SQL 参数绑定并转义 `%/_`，不执行用户 SQL。按匹配词数、整段匹配、创建时间及稳定身份排序。它是可解释的词面检索，不宣称向量语义相似；实现可随后按实测替换，不把 LIKE 方案设成永久限制。

message 来源包括 done/stopped 和有原文的 error/interrupted；后两者明确表示失败/中断片段，不进入默认稳定历史。summary 搜索只包含当前有效摘要。检索使用原始来源投影，压缩后省略的内容仍能搜索。

返回 results、next_cursor、match_kind=keywords、scope、notice。每项含 kind/source_id/source_revision、conversation_id/title、created_at（UTC）、message_id 或 summary_id、sender_type/id、status、reference，以及 snippet（最多 360 字符）、snippet_offset、matched_terms、score。工作流消息兼容带 workflow 的原 run/attempt/activation/iteration 标识；工具状态以 execution_facts 表达，不泄露私有工具输入输出。

游标绑定查询参数、World/角色/触发者/目的地，以及可读来源的材料版本。范围或内容变化后返回 MEMORY_CURSOR_CHANGED，须重新搜索，不能拼接两个版本。空结果不代表资料从未存在，也不透露被排除会话的标题或原文。

## Read

工具 `memory_read`；Owner 人工接口 `POST /api/conversations/{id}/memory/read` 额外要求 role_id。

| 参数 | 当前边界 |
|---|---|
| reference | 搜索或本次摘要提供的有签名短引用，最多 256 字符 |
| offset | Unicode 字符偏移，默认 0；不是字节或 UTF-16 单元 |
| max_characters | 默认 4000，范围 1–12000 |
| context_messages | 默认 0，范围 0–2；前后各附若干条有界片段 |

引用绑定当前 World、角色、触发者及回复目的地；格式是 `m.<消息ID>.<revision>.<签名>` 或 `s.<摘要ID>.<来源版本>.<签名>`。签名本身不授予访问权；读取前重新检查源归属/版本与共享范围。复制给别的角色、群或 World 不生效；迁移到另一 World 后重新搜索取得新引用。

返回上述来源身份、text、offset/total_characters、truncated/next_offset、neighbors、notice。原文必须按当前来源版本核对；范围越界明确拒绝。每条前后文最多 400 字符，含自身引用、版本与截断标记，可继续读取；不能越过本执行可见边界。

停止原因及工具副作用计数由服务器提供，不从模型措辞推断；未知不等于未执行。历史正文和摘要都是背景资料，不提升为平台规则。对于摘要，只有全部覆盖来源仍有效才返回；删除、修订、重新置顶或来源归属失配使旧摘要不可回读。

## 工具来源记录与界面

`GET /api/conversations/{id}/memory/references?role_id=...` 为 Owner 返回该角色最近 50 条工具来源记录，复用 execution_id/tool_call_id，不另建业务 Trace。记录 action（search/read）、来源类型/身份/版本、字符范围、短引用和时间，不记录搜索词或原文。

记录表示工具已读取/命中，不表示模型最终答案已经引用。返回当前仍可访问的来源身份；失效时仅保留读取记录元数据及 available=false，不回显旧标题、片段或原文。普通人工搜索不伪造成 Agent 工具调用。

上下文面板的“历史检索”提供角色选择、范围/类型、关键词、继续搜索、原文及前后文、工具来源核对。查询草稿只在当前页面身份内保留；账号/World/角色变化丢弃迟到响应，错误后清除旧预览。未启用工具的角色仍可被 Owner 选为人工权限视角，实际 Agent 要使用工具须先显式启用。

REST 要求 Owner，调用角色的 Guest 可在已有会话权限下使用 Owner 已启用的只读工具；所有结果仍受同一共享范围限制。无认证 401，Guest 管理接口 403；不可读来源/角色 404，版本/游标变更 409，查询/参数/范围非法 422，存储不可用 503。工具把安全错误码作为 rejected/failed 返回，不把 SQL、原始异常或凭据交给模型。错误表见[注册表](../../error-codes.md)。
