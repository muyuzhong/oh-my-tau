# Agent 改进记录

本文件记录每次小而安全的改动，便于后续会话继续按模块推进。

## 2026-06-23 - agent 运行期事件闭合

- 模块：`nanoagent.agent`
- 改动：异常发生在消息、回合或工具执行中途时，`agent_loop` 会先补齐对应的 `message_end`、`turn_end` 和 `tool_execution_end`，再产出唯一的 `agent_end`。
- 改动：`MessageStart` 增加 `generated` 标记，用来区分本轮模型生成输出和注入的 assistant 历史消息，避免状态层把历史消息误认为正在流式输出。
- 测试：新增覆盖 hook/stream 异常后的事件平衡，以及 Agent 状态中 `streaming_message`、`pending_tool_calls` 的清理。
- 验证：`pytest tests/agent/test_agent.py tests/agent/test_loop_hooks.py -q`，18 passed，1 个 pytest cache 写入警告。
- 验证：`pytest -q`，58 passed，1 个 pytest cache 写入警告。
- 验证：`pytest tests/test_import_contract.py -q`，1 passed，1 个 pytest cache 写入警告。

## 2026-06-23 - ai 流式文本增量可见

- 模块：`nanoagent.ai`
- 改动：`StreamAccumulator` 现在会在收到 `TextDelta` 时追加到当前 `TextContent`，让中间累计消息在 `TextEnd` 前也能反映已到达文本。
- 约束：`TextEnd` 仍保留最终文本覆盖逻辑，避免改变 provider 最终消息的权威性。
- 测试：先新增 `test_stream_accumulator_exposes_text_delta_before_text_end` 并确认失败，再做最小实现。
- 验证：`pytest tests/ai/test_accumulator.py::test_stream_accumulator_exposes_text_delta_before_text_end -q`，1 passed，1 个 pytest cache 写入警告。
- 验证：`pytest tests/ai/test_accumulator.py -q`，3 passed，1 个 pytest cache 写入警告。
- 验证：`pytest -q`，59 passed，1 个 pytest cache 写入警告。
- 验证：`pytest tests/test_import_contract.py -q`，1 passed，1 个 pytest cache 写入警告。
