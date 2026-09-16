# Token Rhythm 中转接口核验

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 日期：2026-09-15；状态：本次最小请求、产品流式与工具往返通过。
- 接口：`https://tokenrhythm.studio/v1`；模型：`deepseek-flash`；Provider：`openai_compatible`。
- 凭据由用户本轮提供，仅在测试进程环境使用，本文及源码不保存 Key，不覆盖本地长期配置。

最小 Chat Completions 请求返回 HTTP 200，厂商报告 prompt_tokens=34、completion_tokens=14、total_tokens=48。这只是该次请求用量，不是全部测试合计。
随后在 backend 经真实产品 Provider 工厂执行：

```bash
python -m pytest tests/contract/test_provider_contract.py -m contract -k 'openai_compatible and (streaming_text or tool_call_round_trip)' -q --tb=short --show-capture=no
```

结果：2 passed、6 deselected（8.16 秒）。模型流式增量、usage 和工具执行往返均通过，不需要中转专用产品改动；不代表所有模型、图片或长任务均已验证。
运行前已说明联网及消耗额度。复跑时同时在进程环境设置 ROLEPLEX_CONTRACT_OPENAI_KEY、ROLEPLEX_CONTRACT_OPENAI_BASE_URL、ROLEPLEX_CONTRACT_OPENAI_MODEL；环境优先于 backend/.env，不要只替换其中一项而混用其他厂商配置。

历史记录 `logs/tests/e2e/real/2026-09-01/11-21-55_4398c0cf/events.jsonl` 中同地址返回 PROVIDER_AUTH_FAILED，当时模型名为 deepseek-v4-flash-0731。
该记录只能证明当次认证/权限类失败，不能确认旧凭据内容或网关具体原因，也不能由模型名不同认定模型名是失败根因。
网页表单使用的加密模型配置与契约测试进程环境是两个配置来源；网页可用不自动意味着测试命令已使用同一组凭据、地址和模型。
