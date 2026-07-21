# Lion Code 上下文管理基准报告

- 生成时间：2026-07-21T11:28:21.873422+00:00
- 模型：`deepseek-v4-flash`
- API：`https://api.deepseek.com`
- 测试模式：仅离线探针
- 当前代码模型窗口：200,000 token
- 本次有效窗口：180,000 token
- API Key：未写入报告

## 分层离线探针

| 探针 | 结果 |
|---|---|
| `large_result_persistence` | 107,704 → 5,635 字符，减少 94.77% |
| `dynamic_budget_0.55` | 240,000 → 119,860 字符，减少 50.06% |
| `dynamic_budget_0.72` | 240,000 → 59,860 字符，减少 75.06% |
| `snip_hot_below_override` | 清理 0 条，保留 8 条 |
| `snip_cold` | 清理 5 条，保留 3 条 |
| `snip_hot_override` | 清理 5 条，保留 3 条 |
| `microcompact_after_idle` | 清理 5 条，保留 3 条 |

## 说明

离线探针只验证本地压缩行为，不能证明真实 token、缓存命中和 API 成本。
