# Agent 框架（项目①）设计 — 模仿 oh-my-pi 的 ai/agent 分层

- **日期**：2026-06-17
- **状态**：设计已批准（2026-06-17）；实现计划已出（2026-06-18，见 `docs/superpowers/plans/2026-06-18-nanoagent-framework.md`）。§5 消息模型经 brainstorm 修订为**三层**（见 §16 决策细化）。
- **参考架构**：`D:\Pi\oh-my-pi`（omp，v15.11.7）的 `packages/{ai,agent,coding-agent}`。其 `AGENTS.md` 权威定义：`agent`=「Agent runtime with tool calling and state management」，`coding-agent`=「Main CLI application」。
- **学习参照**：`D:\harness agent\Mono`（仅借概念，不绑代码、不在其上演进）。
- **本 spec 范围**：只覆盖**项目① = 框架**（`ai` + `agent` 两个包 + 薄 `utils`）。**harness（项目②，omp `coding-agent` 类比）是未来的独立 spec，不在此文。**

---

## 1. 目标与非目标

**目标**
- 自建一个高解耦、低依赖、易扩展的 agent 框架，刻意模仿 omp 的**包边界与依赖方向**（不是它的规模）。
- 框架能**脱离 harness 独立运行与测试**（自带 mock provider）。
- 为后续 harness 留好扩展接缝，且框架代码对 harness 的改动**封闭**。

**非目标（YAGNI，明确不做）**
- 不做 omp 的成熟度功能：Rust `natives`、`mnemopi` 记忆、`stats` 遥测、`catalog` 模型目录、`snapcompact`、`swarm` 扩展、MCP、远程压缩、provider 专属 quirk（如 harmony-leak）。
- 不做 harness：具体工具集、权限/审批策略、会话生命周期、预算策略、TUI。
- 不追求生产级性能。

---

## 2. 术语（三层，钉死不混）

| 层 | 是什么 | 对应 omp | 本计划 |
|---|---|---|---|
| **agent runtime** | loop + 调度 + 工具调用 + 状态，零领域逻辑 | `packages/agent` | 项目①核心 |
| **harness** | 组装 + 策略（工具/权限/审批/规则/生命周期） | `packages/coding-agent` | 项目②（以后） |
| **agent application** | runtime + harness + provider + UI 拼成可跑产品 | 整个 omp CLI | 最终产物 |

「框架」= runtime + 其下的 `ai`。**framework ≠ runtime + harness**——别让 framework 吞掉 harness（那是胖内核病）。

---

## 3. 参考：omp 的真实架构（模仿对象）

包（核实自 omp）：`ai`（多 provider LLM 客户端+流式）、`agent`（runtime）、`coding-agent`（harness/CLI）、`tui`、`utils`、`catalog`、`natives`(Rust)、`mnemopi`(记忆) 等。

依赖 DAG（自底向上，实测）：
```
coding-agent (harness, 顶层, 几乎依赖所有)
     │
   agent (runtime: loop+工具+状态+context/compaction)
     │
    ai (多 provider 客户端 + 流式)
     │
  catalog ── utils ── natives (Rust)
```
关键点：**`ai` 是比 runtime 更低的包，runtime 直接依赖它**（用 `ai` 的 `Model` 类型与流式）；context/compaction 在 runtime 内部；harness 在最顶把一切组装成产品。

---

## 4. 我们的架构（缩到学习尺度）

包与依赖方向（严格单向 DAG，工具强制）：
```
agent (runtime)  ──►  ai (provider 抽象 + 客户端)  ──►  utils
```

| 包 | 职责 | 依赖 |
|---|---|---|
| `utils` | 日志、流 helpers、ID 生成 | 无 |
| `ai` | **wire `Message` 模型 + 内容块（TextContent/ThinkingContent/ToolCall/ImageContent）** + `Model` 抽象 + 归一化流事件 + 错误分类 + 适配器（mock 必有 + 1 个真实）+ per-provider wire 序列化 | utils |
| `agent`（runtime） | **`AgentMessage` 模型（= wire `Message` ∪ 自定义类型）+ `convert_to_llm` 接缝** + agent_loop + 有状态 Agent + context 装配 + 工具协议与执行 + 控制接缝 + 事件 + 终止契约 | ai, utils |

**import 规则**（护栏，见 §9）：`agent` 不得 import 任何 harness；`ai` 不得 import `agent`；框架内不出现"选哪个 provider/密钥"。

跳过的 omp 包：`natives`/`mnemopi`/`stats`/`catalog`/`snapcompact`/`swarm`（`catalog` 的模型元数据暂并入 `ai`）。

---

## 5. 框架的组成（六块 + runtime 内部模块）

**六块**（每块"做什么/依赖谁"）：①消息模型（**三层**：`ai` 持 wire `Message` + 内容块；`agent` 持 `AgentMessage` + `convert_to_llm`；provider 序列化在 `ai/providers`） ②Provider 抽象（在 `ai`） ③工具系统 ④agent 循环 ⑤控制面 ⑥终止契约。

**runtime（`agent` 包）内部模块**（模仿 omp `agent/src`，砍到最小）：
- `messages` —— `AgentMessage` 模型（= `ai` 的 wire `Message` ∪ app 自定义 `CustomMessage` 类型）+ `convert_to_llm` 接缝（默认过滤到 wire 三类 user/assistant/toolResult）。**wire `Message` 与内容块本身下沉到 `ai`，不在 `agent`**（修订自旧 `blocks` 说法，见 §16）。
- `events` —— loop 对外发布的运行时事件词汇。
- `tools` —— Tool 协议 + 参数 schema 校验 + 并发执行器。
- `control` —— abort / approval / steer 接缝原语（只在安全点生效）。
- `result` —— `StopReason` 枚举 + `RunResult` 终止契约。
- `context` —— 从历史装配 `ModelRequest`；最小压缩留 stub（成熟压缩是后话）。
- `loop` —— `agent_loop` 异步生成器（心脏）。
- `agent` —— 有状态 `Agent` 类（包 loop、持有会话、暴露 `prompt()`）。

---

## 6. 入口契约（runtime 消费 / 暴露什么）

忠于 omp（修正早前"StreamFn 由 harness 注入"的说法）：

- **消费**：(a) `Model`（来自 `ai` 的 provider 抽象，**不是 harness 注入的不透明函数**）；(b) `Context` = system prompt + 消息历史 + `Tool[]`；(c) `options`（max_turns、thinking level…）；(d) 一个 **control source**（交互式 abort/approval 用）。
- **两个入口**（对应 omp 的 `agentLoop` 函数 / `Agent` 类分离）：
  - `agent_loop(...)` —— 异步生成器，较纯，逐事件产出。
  - `Agent` —— 有状态类，包 loop、持有会话、暴露 `prompt()`。
- **产出**：一串运行时事件，并以 `RunResult(reason, final_message_id, error, detail)` 收尾。
- **harness 的活**：构建 `Model` + `Context` + `Tool[]`、选 provider/key、提供 control source、驱动 loop。

---

## 7. 边界纪律（机制 vs 策略）

框架=机制，harness=策略；开闭原则（对扩展开放、对修改关闭）。每往框架加东西，过四关（**任一不过 → 属于 harness**）：

1. **两产品测试**（最强）：两个不同 agent 产品会不会"一模一样"地想要它? 会想要得不同 → 策略 → harness。
2. **默认值陷阱**：是个数字/字符串/规则吗（max_turns=10、默认提示词、retry 3 次）? 框架给旋钮，harness 拧数值。
3. **命名测试（只约束 runtime 核心 `agent`）**：`agent` 包绝不出现具体名字（Anthropic、read_file、claude-xxx、rm -rf、某业务规则）——它只认 `Model`/`Tool` 抽象。**例外**：`ai` 包的职责就是 provider 集成，**允许**出现具体 provider 客户端代码，但藏在 `Model` 抽象之后；即便如此，`ai` 也不决定「用哪个 provider / 哪把 key」——那是 harness 的策略。
4. **无副作用测试**：框架近纯；所有 I/O / 时钟 / 环境走注入端口。

---

## 8. 扩展模型：五条接缝

harness 加约束**永远走接缝，不改框架源码**：

| 接缝 | 用法 | 约束活在哪 |
|---|---|---|
| ① 注入 | loop 收 Model/Tool[]/policy/config，由你决定注入什么 | harness 装配代码 |
| ② 实现接口 | 框架定义 `Tool`/`Policy`/`ControlSource` 端口，你写具体类 | 你的实现类 |
| ③ 配置 | max_turns、预算等量化上限 | 调用参数 |
| ④ 包装 | 拿 wrapper 包住框架的 Model/Tool 再注入（脱敏/限流/白名单） | 你的 wrapper |
| ⑤ 装配点 | harness 把 Model+Tool[]+policy+config 拼起来 | harness 入口 |

常见约束→接缝：只允许工具 X/Y/Z→①；单轮最多 N 次调用 / 重复检测→②（写进 `Policy` 实现，框架只在轮边界 `review()`）；禁止 `rm -rf`→④；写操作需审批→②+①；token 预算→③；输出脱敏→④。

**何时才动框架代码**：仅当某约束需要框架尚无的插入点——这时加一个**通用 hook**（机制），不是塞具体策略。判据：通用机制→框架；具体策略→harness。

---

## 9. 边界验证（把判断变成护栏）

- **import-linter**：声明分层契约（`agent→ai→utils`；禁止反向、禁止 `agent` import harness、禁止框架 import 具体 provider 选择）。机械保证"不贪"+ 无环。
- **只用 mock 跑通全部框架测试**：依赖真 provider/harness = 泄漏。
- **测试套件即"第一个 harness"**：每条接缝用 mock/琐碎实现踩一遍；测试想伸手进框架内部 = 缺缝信号（在框架开发期就抓到）。
- **需求→接缝映射**：开工前把未来 coding harness 的约束逐条指向接缝；指不到的 = 现在就补的 hook。
- **建 harness 时框架仓 diff 应为空**：被迫改框架 → 停下评审。

---

## 10. 数据流（一轮 loop）

装配 `Context`（system + 历史 + tools）→ `ai` 流式（`stream(model, request)`）→ 累积成 assistant 消息 → **有工具调用?** 无 → 收尾 `COMPLETED`；有 →（需审批则经 control source 暂停询问）并发执行 → 工具结果回填为新消息 → 下一轮。**安全点**（完整消息之间）应用 abort / steer。预算或轮数到 → 对应 `StopReason` 收尾。

---

## 11. 错误与终止模型（借 Mono 的 ADR-008/015 经验）

- **工具可观察错误** → `ToolResult(is_error=True)`，永不传播。
- **Provider 失败** → 编码进事件流（错误事件 + 终止原因），不抛过框架边界；可重试错误按策略重试。
- **运行终止** → 结构化 `RunResult`（`StopReason` 枚举 + `final_message_id` + `error` + `detail`）。
- **不变量**：异常绝不逃逸 loop 生成器；总以 `RunResult` 收尾。

---

## 12. 测试策略

- mock-driven、自底向上、每层独立测试（Python `asyncio`）。
- `ai`：mock 适配器 + 真实适配器的流解析单测。
- `agent`：用 mock provider + 琐碎工具驱动 loop 的集成测试，覆盖**每个终止路径**与**每条接缝**。
- import-linter 作为结构测试纳入 CI。

---

## 13. 开发顺序（自底向上，每步用 mock 即可独立验证）

1. `utils`（极小）+ `blocks` 消息模型（+测）
2. `ai`：流事件词汇 + `Model` 抽象 + mock 适配器（+测：能 fake 一个模型）
3. `agent.tools`：Tool 协议 + echo 工具 + 并发执行器（+测）
4. `agent.loop` 最小：纯文本一轮、无工具（+测：mock 说 hi → `COMPLETED`）
5. `agent.loop` + 工具：调用 → 执行 → 回填 → 续轮（+测）
6. `agent.control`：abort + 审批接缝（+测）
7. `agent.result`：结构化终止（+测）
8. `Agent` 有状态类 + 1 个真实 `ai` 适配器 + 薄 REPL 冒烟（+测）

每步即一个 TDD 任务，交给 writing-plans 细化。

---

## 14. 与 Mono 的关系

Mono 仅作学习参照。可借鉴的成熟件：`blocks` 消息模型、`StreamAccumulator`、并发 `executor`、`ControlPlane`、`RunResult`、`ExecutionEnv` 思路。但**全新实现**，不 import Mono、不在 Mono 内演进；Mono 的 §14 内核迁移路线作废。

---

## 15. 已定决策（2026-06-17，用户拍板）

1. **位置 / 名字**：新建独立目录 **`nanoagent`**（`D:\harness agent\` 下作 Mono 的兄弟目录、独立 git 仓）。包名 `nanoagent`，import 前缀 `nanoagent.{agent, ai, utils}`。
2. **打包方式**：**B** —— 单安装包内分层子模块 + `import-linter` 强制 `agent → ai → utils`（可随时毕业到多包 workspace）。
3. **语言**：**Python**。
4. **第一个真实 provider**：**OpenAI-compatible**（`base_url` 可切 deepseek 等）；`mock` 适配器必有。

---

## 16. 决策细化（2026-06-18，brainstorm 拍板，plan 依据）

实现计划 `docs/superpowers/plans/2026-06-18-nanoagent-framework.md` 据此细化；以下覆盖/补充上文：

1. **消息模型三层（修订 §4/§5）**：wire `Message`（`UserMessage | AssistantMessage | ToolResultMessage`）+ 内容块下沉到 `ai`；`agent` 持 `AgentMessage = Message ∪ CustomMessage` + `convert_to_llm` 接缝（框架给默认，具体转换是 harness 策略）；per-provider wire 序列化在 `ai/providers/<name>`（mock 跳过）。旧 §5「`blocks` 整体在 `agent`」作废。
2. **流式原语**：用 Python `async generator` 产出事件 + `StreamAccumulator` 折叠成 `AssistantMessage`，**不移植** omp 的 `EventStream` 双接口。
3. **终止契约**：保留 omp 事件流，叠加 Mono 风格 `RunResult` 合成（`AgentEnd` 事件携带 `RunResult`，为单一真相源）。**两级 StopReason 不混**：wire 级在 `nanoagent.ai`（stop/length/tool_use/error/aborted）；run 级在 `nanoagent.agent`（completed/max_turns/aborted/error）。
4. **数据表示 / schema**：消息与内容块用 `dataclass`；工具参数用 `pydantic` v2（校验模型吐出的 args）。
5. **控制面**：`AbortSignal`（asyncio）+ `ControlSource` 协议（approval，默认 `AllowAll`）+ steering 队列；审批走 loop 接缝，策略归 harness。
