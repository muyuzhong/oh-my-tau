# Mono 初期架构决策文档

- 日期：2026-06-14
- 状态：已确认架构方向，作为 HarnessSession 阶段的设计基线
- 适用阶段：现有 Runtime 完成后，到第一个可实际使用的 Coding Agent MVP 之前
- 主要参考：`D:\Pi\pi` 的 agent loop、AgentHarness 与 coding-agent 分层
- 案例参考：`D:\Pi\oh-my-pi` 中成熟 Coding Agent 面临的工程问题

## 1. 文档目的

本文档决定 Mono 在项目初期应当采用什么架构，以及当前 Runtime 如何演进为可实际使用的
Coding Agent。

这里的“初期”不是项目刚创建时，而是以下范围：

```text
当前 Runtime
    -> HarnessSession
    -> Coding Tools V1
    -> Coding Agent MVP
```

本文档不试图一次设计完整 Harness 生态。它只确定那些如果现在不明确，后续会导致 Runtime、
会话层和 Coding Agent 产品层相互污染的关键边界。

## 2. 产品定位

Mono 的产品目标是一个可以实际完成代码修改与验证工作的 Coding Agent。

Mono 的内部架构应保留一个通用 Harness 内核，但当前开发优先级、功能取舍和验收标准始终围绕
Coding Agent，而不是围绕构建一个独立对外销售或发布的通用 Agent 框架。

一句话定位：

> Mono 是一个以 Coding Agent 为产品目标、以通用 Harness 为内部架构的项目。

这个定位带来三个约束：

1. 通用 Harness 只抽象已经被 Coding Agent 主链需要的能力。
2. Coding Agent 的项目规则、工具体验和验证流程不能下沉到 Runtime。
3. 每个阶段都必须形成一条可以由用户真实使用和验收的纵向链路。

## 3. 当前项目真实状态

### 3.1 已实现能力

截至 2026-06-14，Mono 已经拥有一套可运行的事件驱动 Runtime，包含：

- Provider 无关的块消息模型
- Anthropic 与 OpenAI-compatible Provider 适配器
- 流式模型响应累积
- 多轮 Agent Loop
- 工具注册、参数校验、并发执行与超时处理
- 工具审批、转向、中断、暂停和恢复控制
- Runtime 事件流
- Token 预算、重试和最小上下文压缩
- append-only JSONL 会话转录
- 基础监督策略
- Rich REPL

当前自动化验证基线：

```text
python -m pytest -q
80 passed
```

因此，Mono 已经超过“基本架构尚未实现”的阶段。更准确的判断是：

> Runtime 主链已经可运行，但 Harness 生命周期层和 Coding Agent 产品层尚未建立。

### 3.2 当前结构中的主要问题

#### 问题一：CLI 直接组装 Runtime

`cli/repl.py` 当前直接创建 Provider、ToolRegistry、SessionState、TokenLedger、ControlPlane 和
AgentLoop。CLI 因此知道过多运行时细节，也成为事实上的会话装配层。

#### 问题二：AgentLoop 承担了部分 Harness 职责

当前 `AgentLoop` 除了推进推理与工具调用，还负责审批等待、控制命令处理、预算终止和 Supervisor
注入。这些能力并非都必须移出 Runtime，但它们的策略配置、生命周期和持久化语义需要由
HarnessSession 统一拥有。

#### 已收口问题：推理视图、重复协议与副作用边界

Runtime Kernel 已完成三项边界收口：

- `ContextAssembler` 只从完整历史派生模型请求，不再覆盖 `SessionState.messages`。
- 零消费者的 `core/message.py`、`core/event.py` 与 `core/agent.py` 已删除，主链只使用
  `runtime/blocks.py` 与 `runtime/events.py`。
- 文件与 Shell 副作用通过注入的 `ExecutionEnv` 执行；REPL 绑定当前工作区，取消和超时会终止
  直接子进程，错误原因会反馈给模型。

仍然成立的架构问题更深一层：完整转录、预算和多轮运行驱动目前仍由 Runtime 持有。它们应在
HarnessSession 建立后逐步上移，而不是继续留在内核中扩展。

## 4. 参考 Pi 的方式

### 4.1 借鉴 Pi 的架构责任，而不是复制目录

Pi 中值得 Mono 借鉴的核心分层是：

1. 低层 agent loop 负责模型推理、工具调用和事件推进。
2. Harness 负责会话生命周期、运行锁、配置快照、持久化和安全变更。
3. Coding Agent 负责将 Harness 组装为面向代码仓库的产品。

Pi 的关键思想不是拥有更多模块，而是每一层拥有不同时间尺度的状态：

- Runtime 处理一次正在进行的执行。
- Harness 管理跨 Turn、跨 Prompt 的长期会话。
- Coding Agent 管理工作区级产品行为。

### 4.2 当前应借鉴的 Pi 决策

Mono 初期应借鉴：

- Runtime 与 Harness 分层
- 同一会话只允许一个结构性运行操作
- 每个 Turn 使用不可变配置快照
- 运行中配置修改只影响未来安全点
- 完整持久化会话与模型推理上下文分离
- 明确的 steering 与 abort 语义
- 事件处理与持久化顺序必须确定
- Provider、Tool 和 Storage 通过边界接口接入

### 4.3 当前不应复制的 Pi 能力

Mono 初期不复制：

- Session Tree、分支导航与分支摘要
- 通用 Hooks 与 Extension 系统
- Skill、Prompt Template 和资源市场
- 多种 UI/RPC 产品入口
- 复杂模型注册表和动态 Provider 生态

这些能力可以作为未来演进参考，但不属于 Coding Agent MVP 的必要条件。

### 4.4 如何使用 Oh My Pi

Oh My Pi 用于发现成熟 Coding Agent 会遇到的问题，例如：

- 高质量 read/search/edit/shell 工具
- LSP、DAP、浏览器和远程能力
- Subagent 隔离
- 工具审批分级
- Memory、Skills、MCP 与扩展生态
- 大型会话的压缩、恢复和观测

它不是 Mono 当前的目标结构。Oh My Pi 中存在的能力，只有在 Mono 已经通过真实任务暴露相同问题
时，才进入设计范围。

## 5. 目标分层

```text
CLI / SDK
用户输入、事件展示、审批交互
        |
        v
CodingAgentSession
工作区、项目规则、编码工具、任务完成条件
        |
        v
HarnessSession
会话生命周期、配置、运行锁、快照、持久化、控制
        |
        v
Runtime Kernel
模型推理、工具调用、运行事件、单次运行推进
        |
        v
Provider / Tool / Storage
外部能力适配器
```

### 5.1 Runtime Kernel

Runtime Kernel 负责：

- 消费一次运行所需的不可变快照
- 推进模型响应与工具调用循环
- 验证并执行工具调用
- 产生细粒度运行事件
- 响应 Runtime 支持的控制信号
- 返回明确的运行终止结果

Runtime Kernel 不负责：

- 决定当前工作区是什么
- 加载项目规则
- 管理跨 Prompt 的产品配置
- 决定会话保存在哪里
- 直接实现 CLI 行为
- 保存或恢复完整产品会话

### 5.2 HarnessSession

HarnessSession 是下一阶段的核心建设目标。它负责：

- 持有长期会话配置
- 持有完整、可恢复、可审计的会话转录
- 为每个 Turn 创建不可变运行快照
- 保证同一会话不能并发执行两个结构性操作
- 管理 prompt、steer、abort、status 和 resume
- 决定配置修改在当前 Turn 或下一 Turn 生效
- 将 Runtime 事件按确定顺序持久化并转发给调用者
- 把 Runtime 终止原因归一化为稳定的会话结果

HarnessSession 不负责：

- 定义 Coding Agent 的系统提示
- 决定哪些路径属于工作区
- 实现 read、search、edit、shell 等具体编码工具
- 决定编码任务是否完成

### 5.3 CodingAgentSession

CodingAgentSession 负责：

- 绑定工作区根目录
- 加载项目级规则
- 构建 Coding Agent 系统提示
- 提供受工作区和安全策略约束的编码工具
- 定义代码修改任务的完成与验证要求
- 将 HarnessSession 暴露为面向编码任务的使用方式

CodingAgentSession 不负责：

- Provider 协议翻译
- Runtime 循环推进
- JSONL 的底层写入格式
- CLI 渲染

### 5.4 CLI / SDK

CLI 与未来 SDK 负责：

- 收集用户输入
- 展示事件和最终结果
- 响应审批请求
- 调用公开 Session API

它们不得直接修改 Runtime、会话历史或控制队列的内部状态。

## 6. 核心架构决策

### ADR-001：产品目标是 Coding Agent，Harness 是内部通用层

**决策**

Mono 的产品开发围绕 Coding Agent 展开，但 HarnessSession 的接口不得包含编码领域专属概念。

**原因**

- 只做通用框架会导致过早抽象，缺少真实任务验证。
- 直接做 Coding Agent 会把工作区、工具和项目规则污染到底层循环。
- 该方案同时保留真实产品反馈和长期可维护边界。

**后果**

- 每个 Harness 抽象都必须能由当前 Coding Agent 需求解释。
- Coding Agent MVP 是阶段验收对象，Harness 本身不是独立产品验收对象。

### ADR-002：采用四层架构，渐进迁移，不整体重写

**决策**

采用 `Interface -> CodingAgentSession -> HarnessSession -> Runtime Kernel` 分层。现有 Runtime
保留，通过提取职责和增加适配边界逐步迁移。

**原因**

当前 Runtime 已有 80 个测试覆盖的可运行主链，整体重写会丢失已经验证的边界情况。

**后果**

- 新能力优先添加到目标所属层。
- 只在迁移对应职责时修改现有 Runtime。
- 现有测试继续作为回归基线。

### ADR-003：Runtime 消费不可变的 TurnSnapshot

**决策**

每次 Provider 请求和其后工具执行使用同一个不可变 `TurnSnapshot`。HarnessSession 的最新配置与
当前运行快照分离。

概念结构：

```python
@dataclass(frozen=True)
class TurnSnapshot:
    session_id: str
    system_prompt: str
    messages: list[Message]
    model: str
    max_tokens: int
    tools: list[Tool]
    runtime_policy: RuntimePolicy
```

**行为契约**

- 当前 Provider 请求开始后，模型、工具和系统提示不再变化。
- 运行中配置修改立即写入 HarnessSession 的未来配置。
- Runtime 到达下一个安全点时，由 HarnessSession 创建新快照。
- 当前 Turn 不读取 HarnessSession 的可变内部状态。

**原因**

这可以消除运行中配置变化的竞态，并使一次运行可复现、可测试。

### ADR-004：完整转录与推理上下文分离

**决策**

HarnessSession 持有 append-only 完整转录；ContextBuilder 根据完整转录构建发送给模型的推理上下文。
压缩、截断和摘要只改变推理视图，不覆盖完整转录。

```text
完整转录 Transcript
        |
        v
ContextBuilder + ContextPolicy
        |
        v
TurnSnapshot.messages
```

**原因**

完整转录用于恢复、审计和未来重新构建上下文；推理上下文受模型窗口限制，必然是可变且可能有损的
派生数据。两者不能共用一个可被覆盖的消息列表。

**对当前代码的影响**

- `ContextAssembler` 不再修改 `SessionState.messages`。
- `SessionState` 的持久化职责迁移到 Harness 会话存储。
- Runtime 只消费已经构建好的消息快照。

### ADR-005：HarnessSession 拥有会话运行锁和显式阶段

**决策**

同一 HarnessSession 同时只能执行一个结构性操作。初期阶段状态为：

```text
idle | running | closing
```

初期结构性操作只有：

- `prompt`
- `resume` 后的首次恢复初始化
- `close`

运行期间允许：

- `steer`
- `abort`
- `status`
- 修改仅影响未来快照的配置

**原因**

一个布尔 `is_running` 无法表达清理与关闭边界，但初期也不需要复制 Pi 的 compaction、
branch_summary 等完整 phase 集合。

### ADR-006：HarnessSession 提供最小公开 API

**决策**

第一版公开接口保持最小：

```python
session = HarnessSession(...)

async for event in session.prompt("修复这个问题"):
    ...

session.steer("先不要修改配置文件")
session.abort()
session.status()

session = HarnessSession.resume(session_id, ...)
```

建议补充：

```python
await session.wait_for_idle()
await session.close()
```

**行为契约**

- `prompt()` 在非 idle 状态下失败，不隐式排队为第二个任务。
- `steer()` 只允许在 running 状态提交，并在安全点追加为新用户消息。
- `abort()` 发出取消信号，但只有 `wait_for_idle()` 完成后才代表运行已经结算。
- `status()` 返回公开快照，不暴露内部可变对象。
- `resume()` 恢复完整转录和可持久化配置，不恢复不可安全重放的半完成副作用。

### ADR-007：事件是跨层观测协议，不是任意状态修改入口

**决策**

Runtime 产生运行事件；HarnessSession 按确定顺序持久化、归一化并转发；CLI 只消费事件。

初期事件消费者是观察者，不允许通过回调直接修改内部状态。需要改变运行行为时，必须调用
HarnessSession 的显式命令 API。

**原因**

这可以避免事件监听器重入、持久化乱序和内部状态被任意修改。通用 Hook 系统等真实需求出现后再设计。

### ADR-008：错误分为可观察结果、运行终止和调用失败

**决策**

错误按责任边界分为三类：

1. **工具可观察错误**：参数错误、工具异常、权限拒绝，转换为 ToolResult 反馈模型。
2. **运行终止结果**：用户中断、预算耗尽、上下文溢出、Provider 失败，形成稳定 termination reason。
3. **公开 API 调用失败**：会话 busy、恢复文件损坏、配置非法，由 HarnessSession 抛出类型化错误。

建议的运行结果：

```python
@dataclass(frozen=True)
class RunResult:
    reason: str
    final_message_id: str | None
    error: str | None
```

**原因**

调用者需要区分“模型看到了一个工具错误”“本次 Agent 运行正常结束但未完成”和“调用本身无效”。

### ADR-009：安全必须在工具边界执行

**决策**

审批是安全策略的一部分，但不能替代工具边界中的强制约束。

Coding Tools V1 必须实现：

- 工作区路径归一化和越界拒绝
- 工具输入 Schema 校验
- 输出长度限制
- 超时和取消传播
- 明确的副作用等级
- shell 危险操作审批
- edit 的旧内容匹配或等价并发保护

**原因**

Prompt 指令和用户审批无法保证工具参数安全。真正的强制边界必须存在于工具实现和执行策略中。

### ADR-010：不保留没有真实消费者的通用协议

**决策**

对 `core/message.py`、`core/event.py` 和 `core/agent.py` 进行一次用途审计：

- 如果它们有明确的跨层消费者，定义与 Runtime 类型之间的转换契约。
- 如果没有真实消费者，不继续扩展这些类型，并在迁移完成后删除或收缩。

**原因**

“未来可能使用”的通用类型会形成第二套协议，并增加每次功能开发的决策成本。

## 7. 关键运行流程

### 7.1 用户发起 Prompt

```text
CLI / SDK
  -> CodingAgentSession.prompt()
  -> HarnessSession.prompt()
  -> 获取会话运行锁
  -> 从完整转录与最新配置创建 TurnSnapshot
  -> Runtime Kernel 执行
  -> Harness 持久化并转发事件
  -> 到达安全点时创建下一 TurnSnapshot
  -> 形成 RunResult
  -> 释放运行锁
```

### 7.2 运行中修改配置

```text
当前 Turn 使用 Snapshot A
        |
用户将 model 修改为 B
        |
Harness 最新配置立即变为 B
当前 Provider 请求仍使用 A
        |
下一个安全点创建 Snapshot B
```

### 7.3 会话恢复

```text
读取持久化会话记录
  -> 校验记录完整性
  -> 重建完整转录
  -> 恢复可持久化配置
  -> 标记未完成运行已中断
  -> 返回 idle HarnessSession
```

初期恢复不自动重试半完成工具调用，也不假设 Provider 流可恢复。

## 8. 初期数据所有权

| 数据 | 所有者 | 是否持久化 |
|---|---|---|
| Provider 流中的部分 assistant 消息 | Runtime | 否，完成后再提交 |
| 当前 TurnSnapshot | Runtime | 否，可由会话状态重新构建 |
| 完整会话转录 | HarnessSession / SessionStore | 是 |
| 最新模型与运行配置 | HarnessSession | 是，采用显式配置记录 |
| steering 队列 | HarnessSession | 初期否 |
| 当前运行锁与取消信号 | HarnessSession | 否 |
| 工作区根目录和编码策略 | CodingAgentSession | 是或由启动参数重建 |
| UI 展示状态 | CLI / SDK | 否 |

## 9. 目录目标

初期建议逐步演进为：

```text
mono/
├── runtime/                 # 单次运行内核
│   ├── blocks.py
│   ├── events.py
│   ├── engine.py
│   ├── executor.py
│   └── policy.py
├── harness/                 # 长期会话与运行治理
│   ├── session.py
│   ├── snapshot.py
│   ├── transcript.py
│   ├── control.py
│   ├── context.py
│   ├── errors.py
│   └── events.py
├── coding_agent/            # 编码产品层
│   ├── session.py
│   ├── system_prompt.py
│   ├── workspace.py
│   ├── policy.py
│   └── tools/
├── providers/
├── cli/
└── tests/
```

这只是目标责任地图，不要求立即移动现有文件。只有在职责迁移时才调整目录。

## 10. 迁移路线

### 阶段一：建立 HarnessSession

目标：让 CLI 只依赖 HarnessSession，不再直接装配 AgentLoop。

工作顺序：

1. 定义 HarnessSession 行为契约、阶段、状态和错误类型。
2. 将完整转录与推理上下文分离。
3. 定义 TurnSnapshot，并让 Runtime 消费快照。
4. 将会话运行锁、配置所有权和控制入口放入 HarnessSession。
5. 将 Runtime 事件转发、终止结果和持久化顺序统一到 HarnessSession。
6. 让 CLI 改为只调用 HarnessSession。
7. 添加恢复、并发 prompt、运行中配置变更和端到端 prompt 测试。

完成标准：

- CLI 不再直接创建或持有 AgentLoop、SessionState、TokenLedger 和 ControlPlane。
- 同一会话无法并发执行两个 prompt。
- ContextBuilder 不修改完整转录。
- 当前 Turn 与未来配置的生效边界有测试。
- 恢复后会话处于确定的 idle 状态。
- 现有 Runtime 测试继续通过。

### 阶段二：Coding Tools V1

目标：让 Mono 拥有完成小型代码修改所需的最小安全工具集。

第一批工具：

- `read`
- `search`
- `edit`
- `shell`

完成标准：

- 所有文件能力被限制在工作区。
- shell 支持审批、超时和取消。
- edit 对过期内容或不匹配内容拒绝写入。
- 输出被限制并提供有助于模型修正的错误信息。
- 能在测试仓库完成“定位代码 -> 修改 -> 运行测试”。

### 阶段三：Coding Agent MVP

目标：将工作区规则、系统提示、Coding Tools 和 HarnessSession 组合为可实际使用的 Coding Agent。

完成标准：

- 用户可以从 CLI 发起真实编码任务。
- Agent 能读取项目规则，修改代码并运行验证。
- 用户可以观察工具、审批、错误和最终终止状态。
- 会话退出后可以恢复。
- 使用真实 Provider 完成一组小型编码任务。

### 阶段四：真实任务回归集

目标：根据真实失败决定下一项 Harness 能力，而不是根据参考项目功能列表决定。

至少覆盖：

- 修改函数并通过已有测试
- 定位并修复简单 Bug
- 添加小功能与测试
- 根据失败测试继续修复
- 拒绝工作区外写入
- 危险命令要求审批
- 中断后停止副作用
- 恢复会话后继续任务

## 11. 明确暂缓的能力

Coding Agent MVP 跑通前，不进入以下能力的正式设计或实现：

- Subagent 与多 Agent 编排
- 长期 Memory
- MCP
- 通用插件与 Hook 系统
- Session Tree、分支和 Fork
- LSP、DAP 和浏览器
- Web UI、RPC 和 ACP
- 后台任务
- 分布式执行和多租户
- 自动 Git 工作流

如果真实任务失败明确由其中某项能力缺失导致，再为该能力单独编写设计决策。

## 12. 架构检查线

出现以下信号时，应暂停新增功能并重新检查边界：

- CLI 需要直接修改 Runtime 内部状态。
- ContextBuilder 会删除或覆盖完整会话历史。
- Runtime 开始读取工作区规则或项目配置文件。
- CodingAgentSession 开始解析 Provider 协议。
- 同一个配置字段同时被 CLI、Harness 和 Runtime 持有。
- 同一种消息或事件出现两套无法稳定转换的模型。
- 文档声称支持某能力，但真实 Coding Agent 主链没有调用它。
- 恢复后无法判断上一次副作用是否已经发生。
- 一个新能力必须同时修改多个无关层的内部实现。

## 13. 下一份设计文档

本决策文档确认目标架构和迁移方向。下一份设计文档应只聚焦：

> HarnessSession 的状态模型、公开 API、TurnSnapshot、完整转录与推理上下文边界，以及从当前
> Runtime 迁移的具体方案。

该设计通过后，再编写 HarnessSession 的 TDD 实现计划。

---

## 14. 补充设计：Runtime Kernel 执行内核底层

- 追加日期：2026-06-14
- 范围：**只覆盖 Runtime Kernel 这一层**，即 Pi `packages/agent` 中 agent loop 在 Mono 的对应物。HarnessSession 及以上属于另一份文档（见 §13），本节不展开其内部实现，只定稿内核向上暴露的契约。
- 方法：独立通读 `runtime/`、`providers/`、`core/` 源码后提炼，再与前文 ADR 对齐。结论不依赖 §1–§13 的既有判断；凡与前文有出入处，在 §14.1 显式校正。

§5.1 只列出了 Runtime Kernel “负责什么/不负责什么”，没有给出这一层的实际设计。本节补上：内核的设计原则、边界、组成契约、依赖端口、变更纪律、错误模型与迁移路径。本节同时把前文已埋下但未展开的几个桩补全——ADR-003 的 `runtime_policy: RuntimePolicy` 字段内容、ADR-004 的“派生而非改写”、ADR-008 的 `RunResult`、ADR-010 的孤儿类型、§9 目录树中 `runtime/policy.py` 的职责。

### 14.1 现状校正（写在设计之前）

前文有一处关键判断已被代码推进，继续按旧判断设计会走偏，先校正：

**§3.2 问题三与 ADR-004 中“`ContextAssembler` 会直接替换 `state.messages`”已不成立。** 当前 `runtime/context.py` 的 `build()` 在入口即 `messages = list(complete_messages)` 复制一份（`context.py:83`），随后 `_truncate`/`_snip` 只重新赋值这个**局部**变量（`context.py:87,89`），最终包进 `ModelRequest` 返回；`engine.py:84` 拿到的 `request` 仅用于发往 Provider，从不写回 `self.state.messages`。`SessionState.messages` 只通过 `state.append(...)` 增长（`engine.py:68,118,148,153`）。因此完整历史不会被压缩视图覆盖——这正是 `eac8adf fix(runtime): harden protocol and compaction edge cases` 已经落实的，`context.py:74` 的类文档也已写明“不修改调用者持有的会话状态”。ADR-004 的“对当前代码的影响：`ContextAssembler` 不再修改 `SessionState.messages`”应视为**已完成**，而非待办。

校正后，问题三**仍然成立的部分**是更深一层的耦合：`state.messages` 仍是唯一的历史容器，且**内核仍直接持有并追加它**。即“完整转录的所有权仍在内核内部”。真正要在内核层消除的，不是“压缩覆盖历史”这个已修复的 bug，而是“内核拥有持久历史”这个结构。这是 §14.8 迁移的核心。

### 14.2 内核设计原则（提炼自 Pi `packages/agent`，限定到本层）

Pi 的底层不是“模块更多”，而是**每一层拥有不同时间尺度的状态**，且最底层几乎无状态。落到 Runtime Kernel 这一层，提炼出六条原则：

1. **内核是对快照的近纯函数。** 给定一个不可变 `TurnSnapshot` 加注入的能力端口，内核推进一轮模型推理与工具调用、产出事件与新消息，自身不拥有跨轮存活的状态。对应 Pi 中 agent loop 与有状态 `Agent` 的分离。
2. **进行中的请求不可变，只在安全点变更。** Provider 请求一旦开始，模型、工具、系统提示、消息集都不再变化（呼应 ADR-003）；steer / pause / 配置变更只在完整消息之间的安全点生效。对应 Pi 的 `prepareNextTurn`/`createTurnState`——改动落到下一轮快照，绝不改写在途请求。
3. **派生而非改写。** 送入模型的上下文是完整转录的**纯归约**（Pi 的 `buildSessionContext`）；压缩、截断、摘要只改派生视图，不改真相源。内核**消费**已派生好的快照，自己不持有真相源、也不回写历史。
4. **所有外部边界注入，内核运行时中立。** Provider、工具、文件系统与 Shell（Pi 的 `ExecutionEnv`）、控制源、存储都通过端口注入。内核因此不知道自己跑在 CLI、测试还是未来的 RPC 之下。
5. **错误分层；失败编码进流；异常绝不逃逸。** 低层能力以 Result 风格返回、不抛（Pi 的 `Result<T,E>`）；Provider 失败编码为流内终止原因（Pi 的 `stopReason: "error"/"aborted"`），不向上抛；内核生成器**永远**以一个终止结果收尾，不让异常穿透。
6. **事件是观察，变更是命令。** 内核向上只发布事件供观察；任何改变运行行为的动作必须通过显式命令在安全点进入（呼应 ADR-007）。事件消费者不得回调改写内核内部状态。

### 14.3 内核边界：消费什么、产出什么、依赖什么

```text
            HarnessSession（上层，本节不展开其内部）
   构建 TurnSnapshot │ 注入 ports │ 持久化产出消息 ▲ 观察 events │ 接收 RunResult
                     ▼                              │
   ┌───────────────────────────────────────────────────────────┐
   │                  Runtime Kernel（本节范围）                  │
   │                                                             │
   │   TurnSnapshot ──▶ TurnEngine ──▶ StreamAccumulator         │
   │   (frozen, 单轮)        │                                    │
   │                         ▼                                    │
   │                    ToolExecutor                              │
   │                                                             │
   │   注入端口: Provider · Tools · ExecutionEnv · ControlSource  │
   │            · Budget(只读检查) · RuntimePolicy                 │
   └───────────────────────────────────────────────────────────┘
        │ stream()            │ fs / shell           │ control
        ▼                     ▼                       ▼
     Provider 适配器       FileSystem / Shell       外部指令源
```

三句话契约：

- **消费**：一个不可变 `TurnSnapshot`（ADR-003 已定义其字段：`system_prompt / messages / model / max_tokens / tools / runtime_policy`）。`messages` 已是上层派生好的推理上下文，内核不再二次压缩。
- **产出**：① 细粒度运行事件流；② 本轮新产生的消息（assistant 消息与 tool_result 消息）；③ 一个结构化 `RunResult`（终止原因 + 末条消息 id + 可选错误）。
- **依赖**：Provider、Tools、ExecutionEnv、ControlSource、Budget（只读）、RuntimePolicy，全部注入。

上层（HarnessSession）如何创建快照、如何持久化产出消息、如何实现这些端口，属于 §13 的另一份文档；本节只保证**内核侧契约稳定**。

### 14.4 内核组成与各自契约

下列组件都已在 `runtime/` 中存在或半成形，本节给出它们作为“内核构件”的契约，并指出与今天实现的差距。

**(1) 消息词汇表 —— `runtime/blocks.py`（内核唯一消息模型）**

`Message` 用混合块列表承载内容（`blocks.py:109`），这是并行工具调用、推理块与无损恢复能够共存的基础。契约：**内核对内对外只有这一套消息类型**；Provider 线格式差异封在适配器内（`providers/base.py:1` 已写明），`ModelRequest` 的构造即 Pi 所谓 `convertToLlm` 边界。Mono 目前**不需要**第二套“转录条目”表示（Pi 的 `AgentMessage` vs provider `Message` 双表示，源于其会话树）——见 ADR-013。

**(2) 流累积器 —— `StreamAccumulator`（`engine.py:15`）**

契约：把交错、可能不完整的 Provider 流归一为**恰好一条良构 assistant 消息**。已具备的健壮性应作为内核保证固化：先 thinking 后 text 的顺序（`engine.py:23`）、按工具 id 累积交错的 `partial_json`、工具参数 JSON 解析失败时降级为 `{"__parse_error__": raw}` 而非崩溃（`engine.py:38-39`，再由执行层转成参数校验错误）、`is_complete()` 要求 `stop_reason` 已到且无未闭合工具（`engine.py:48`）。

**(3) 轮引擎 —— `TurnEngine`（今 `AgentLoop` 的单轮体）**

契约：消费一个快照，推进“Provider 流 → 累积 → 识别工具调用 → 审批门 → 执行 → 产出本轮消息与结果”，并以**调用序**而非完成序产出结果事件（`engine.py:144-147` 的 `ordered` 重排，保证并发执行下可观察顺序确定）。这是内核的主控制流，必须能够单独按顺序讲清。

**(4) 工具执行 —— `ToolExecutor`（`executor.py:42`）**

契约：查找 → 参数校验 → 限流并发 → 超时 → **一切失败观察化**。`ToolNotFound`、`ParameterValidation`、`ToolTimeout` 及任意异常都被转为 `ToolResultBlock(is_error=True)` 返回模型（`executor.py:56-72`），单个外部动作失败绝不打断循环。这一层已经是 Pi 式“Result，不抛”的良好范例。

**(5) 运行策略 —— `runtime/policy.py`（ADR-003 已命名的 `RuntimePolicy`）**

今天这些旋钮散落各处：重试（`context.py:31` `RetryPolicy`）、最大轮数（`engine.py` `max_turns`）、并发度（`executor.py` `max_concurrent`）、预算上限（`context.py:17` `TokenLedger` 的 `max_total_tokens/max_api_calls`）。契约：把**纯策略参数**收敛进 `RuntimePolicy`，作为 `TurnSnapshot.runtime_policy` 随快照下发；其中“预算上限”是策略、可入快照，但**预算记账**（累计已用）是跨轮状态，归上层（见 §14.5 Budget 端口）。

### 14.5 能力端口（依赖注入边界）

| 端口 | 现状 | 内核契约 |
|---|---|---|
| Provider | 已干净（`providers/base.py`，`ModelProvider.stream`） | 只消费标准化流事件与归一化异常；厂商差异不得泄漏进内核 |
| Tools | 已注入（`ToolRegistry`） | 内核只按 id 关联调用与结果，不感知工具实现 |
| **ExecutionEnv** | **缺失** | 见 ADR-014：文件系统与 Shell 作为注入能力，Result 风格不抛，取消传播到副作用 |
| ControlSource | 已存在（`ControlPlane`） | 内核**只读取**：`abort` 旁路标志 + 安全点队列；不拥有、不持久化 |
| Budget（只读检查） | 半成形（`TokenLedger`） | 内核在安全点查询 `budget_ok()`；**记账与上限的所有权在上层** |
| RuntimePolicy | 散落 | 随快照下发的不可变策略（§14.4-5） |

其中最重要的缺口是 **ExecutionEnv**。问题五（`read_file` 无工作区边界、`run_command` 直跑 shell）的根因不在“缺少审批”，而在**内核没有副作用能力边界**：工具直接 `import pathlib/subprocess` 触达宿主机。Pi 把文件系统与 Shell 收敛为 `ExecutionEnv` 能力端口注入到底层，正是为此。这一端口属于内核底层（定义接口与纪律），其**工作区受限的实现**由 Coding Agent 层提供——与 ADR-009 互补（见 ADR-014）。

### 14.6 安全点与变更纪律

内核区分两种外部干预通道，这是“在途不可变 + 安全点变更”的具体落地：

- **中断旁路（同步标志）**：`abort` 通过 `ControlPlane.abort_requested` 标志暴露（`control.py:47-51`），流式循环每收一个事件即检查（`engine.py:96`），命中则**直接弃流**——放弃在途请求，而不是改写它。
- **安全点队列（顺序命令）**：`steer`/`pause`/`resume` 入队，只在完整消息之间 `drain_nowait` 应用（`engine.py:73-80`）。`steer` 在安全点追加为新用户消息，绝不改写既有历史（`control.py:14-18`）。

队列语义已做过正确性加固，应作为内核不变量固化：`Pause` 在 `drain_nowait` 中**提前返回**，把后续 `Resume` 留在队列里供 `wait_resume` 消费，避免 Pause/Resume 连发时丢失恢复信号（`control.py:59-64`）；审批等待 `wait_decision` 会把不相关的 `Steer`/`Pause` 暂存后按原序放回，不吞指令（`control.py:66-77`）。

### 14.7 内核错误模型（把 ADR-008 细化到内核）

ADR-008 的三类错误在内核中的精确映射：

1. **工具可观察错误 → `ToolResultBlock(is_error=True)`**，永不传播（`executor.py` 已实现）。
2. **运行终止结果 → 结构化 `RunResult`**。今天内核只 `yield AgentEnded(reason)`，`reason` 是裸字符串（`max_turns / user_abort / token_budget / context_overflow / provider_error / incomplete_stream / completed / supervisor_* / fatal`，散见 `engine.py`）。契约升级：内核应以**结构化 `RunResult`** 收尾（ADR-008 已给出 `reason / final_message_id / error` 的雏形），把这些原因收敛为枚举，让上层无需解析字符串即可区分“正常未完成 / 预算耗尽 / Provider 失败 / 调用前置失败”。
3. **Provider 失败 → 编码进流**（`ErrorEvent` + 终止原因），不抛过内核边界（`engine.py:102-110` 捕获 `ProviderError`、归一化重试、最终 `ErrorEvent` + `break`）。

并固化一条不变量：**异常绝不逃逸内核生成器**。`engine.py:155` 的 `except Exception` 兜底把任何意外转成 `ErrorEvent` + `reason="fatal"`，生成器始终以 `AgentEnded`/`RunResult` 收尾（`engine.py:158`）。这对应 Pi“循环总是产出一个终止 stopReason”。

### 14.8 内核迁移：从 `AgentLoop` 到 `TurnEngine` + 运行驱动

**关键观察：迁移的接缝已经半成形。** `AgentLoop.__init__`（`engine.py:54-61`）已把 `state / ledger / executor / assembler / supervisor / control / retry_policy` 全部做成可注入参数。所以本次迁移**不是重写循环**（符合 ADR-002），而是改变这些依赖的**所有权与方向**。

**推演（为什么内核会收敛为按轮粒度）：** §9 目标树已把 `context.py` 与 `control.py` 划入 `harness/`，ADR-004 已规定上下文由上层从完整转录派生。一旦“转录所有权”和“上下文派生”都上移，**每个轮边界都要由上层 append 产出消息并重建下一轮快照**——内核自然变成“按轮粒度”。今天 `AgentLoop.run` 之所以是“整次运行粒度”的循环，恰恰因为它**自持 `state` 与 `assembler`**。消除这一自持，run-grained 循环就失去前提。

**三步迁移（每步保持 67 测试通过）：**

1. **内部抽轮**：从 `AgentLoop.run` 提取 `run_one_turn(snapshot, ports) -> TurnOutcome`，`run` 改为在循环里调用它。纯内部重构。
2. **上移驱动与状态**：把“多轮循环 + 控制 drain + 预算检查 + 下一轮快照构建 + supervisor”移到上层（运行驱动），内核停止直接 `state.append`，改为**产出本轮消息**交由上层持久化；上下文派生随 `assembler` 一并上移。
3. **注入 ExecutionEnv**：工具改为经能力端口触达文件系统与 Shell，移除直连 `pathlib/subprocess`。

为保证迁移期可用，内核可暂时保留 `run(user_input)` 便捷入口（用默认端口自驱），但**长期目标入口是 `run_one_turn`**，循环归上层。

**组件所有权迁移表：**

| 组件 | 今天（内核内） | 目标 |
|---|---|---|
| 完整转录 `SessionState.messages` | 内核持有并 append | 上层持有；内核只产出本轮消息 |
| 上下文派生 `ContextAssembler` | 内核每轮调用 | 上层构建快照时调用（`harness/context.py`） |
| 预算记账 `TokenLedger` | 内核持有 | 上层持有；内核只读 `budget_ok()` |
| 控制源 `ControlPlane` | 内核可选持有 | 上层持有并注入；内核只读 |
| 多轮循环 / `max_turns` | 内核 `run` 内 | 上层运行驱动 |
| `StreamAccumulator` / `ToolExecutor` / 工具识别与审批门 | 内核 | **保持在内核** |

**Supervisor 的归属**：`Supervisor`（`supervisor.py`）在每轮边界检查并可注入消息或终止（`engine.py:149-153`）。它是“轮边界策略”——建议内核只暴露一个**轮边界 review 钩子**（机制），具体策略（`RepetitionDetector` / `ConstraintValidator` / `ReflectionStep`）由上层配置注入，避免把产品级质量策略焊进内核。

### 14.9 内核架构决策

#### ADR-011：内核以 `TurnSnapshot` 为单位推进，单轮引擎与运行驱动分离

**决策**：内核的基本单元是“消费一个不可变 `TurnSnapshot`、推进一轮、产出 `TurnOutcome`”的单轮引擎；多轮循环（是否继续、构建下一轮快照、安全点变更、预算检查）属于运行驱动，归上层。

**原因**：这是 ADR-003（每轮一快照、上层创建下一快照）与 ADR-004（上层派生上下文）的必然推论；也对应 Pi 中有状态层驱动、底层 loop 无状态的分工。

**后果**：内核不再自持多轮循环所需的跨轮状态；迁移按 §14.8 三步推进，期间保留 `run()` 便捷入口与现有测试。

#### ADR-012：内核对持久历史无状态，只产出消息交由上层持久化

**决策**：内核不拥有完整转录，不执行 `state.append`；它产出本轮新消息（assistant 消息、tool_result 消息），由上层追加到 append-only 转录并据此构建下一轮快照。

**原因**：消除问题三仍然成立的部分（内核拥有历史）。真相源单一化于上层转录，内核保持近纯函数，可复现、易测试。

**后果**：`SessionState` 的持久化职责迁出内核（呼应 ADR-004 与 §9）；内核测试只需校验“给定快照 → 产出消息/事件/`RunResult`”，不再依赖会话存储。

#### ADR-013：内核只保留一套消息词汇，删除孤儿 `core` 类型

**决策**：`runtime/blocks.py` 是内核唯一的消息模型。`core/message.py`、`core/event.py`、`core/agent.py` 经核实**零引用**（全仓 grep 无导入），按 ADR-010 处置：迁移完成后删除或收缩，不再扩展。

**原因**：避免“两套接近但不兼容的协议”。`core/message.py:37` 的 `parent_id` 字段是 Pi 式会话树的化石，但分支/树形历史已被 §11 明确暂缓——**在引入分支之前不需要第二套“转录条目”表示**。等真正做会话树时，再按 Pi 的 `AgentMessage` vs provider `Message` 双表示设计，并定义 `convertToLlm` 边界。

**后果**：现在收敛到单一词汇，降低每次功能开发的决策成本；双表示作为分支能力的前置项，记入未来设计。

#### ADR-014：一切副作用经注入的 `ExecutionEnv`，工具不直接触达宿主机

**决策**：内核定义 `ExecutionEnv`（FileSystem + Shell）能力端口并注入；工具通过该端口执行副作用，不得直接 `import pathlib/subprocess`。端口方法以 Result 风格返回、不抛异常，并将取消传播到底层副作用（如子进程终止）。

**原因**：问题五的根因是内核缺少副作用能力边界，而非缺少审批。把能力端口化是 Pi `ExecutionEnv` 的核心借鉴，使内核运行时中立、使工作区边界**可强制**而非仅靠提示。

**与 ADR-009 的关系（互补，不重复）**：ADR-009 规定“**有哪些**安全约束”（路径归一化、越界拒绝、副作用分级、危险命令审批、edit 旧内容匹配），其工作区受限的**实现**在工具/Coding Agent 层；ADR-014 规定“**通过什么机制**让这些约束可强制”——即存在一个注入的能力端口。今天 `executor.py` 已承担其中通用部分（schema 校验、超时、错误观察化），缺的正是这个端口；且 `asyncio.wait_for`（`executor.py:65`）只取消 awaitable，并不保证底层子进程被杀，取消传播到副作用必须由 `ExecutionEnv` 落地。

**后果**：新增 `ExecutionEnv` 接口属内核；其工作区根定与策略实现属 Coding Agent 层；内置工具改为依赖端口。

#### ADR-015：内核以结构化 `RunResult` 终止，异常绝不逃逸生成器

**决策**：内核每次运行以一个结构化 `RunResult`（终止原因枚举 + 末条消息 id + 可选错误）收尾；任何内部异常都被兜底转为终止结果，绝不穿透内核生成器。

**原因**：上层需要无歧义地区分“模型看到工具错误 / 本次运行正常结束但未完成 / Provider 失败 / 调用本身无效”，不应解析裸字符串原因。`engine.py:155` 的兜底已保证异常不逃逸，本 ADR 把“裸 `reason` 字符串”升级为结构化结果。

**后果**：`AgentEnded(reason)` 演进为携带 `RunResult`；终止原因集合收敛为枚举，纳入测试断言。

**实现状态（2026-06-16）**：已实现。`runtime/result.py` 定义 `StopReason` 与 `RunResult`，`AgentEnded` 携带 `RunResult` 并保留 `.reason` 兼容属性；`engine.run` 收敛为单一结构化出口。gap C 关闭；A/B/D/E 仍按 §14.8 推进。

### 14.10 内核完成标准与不变量（可测）

内核层（独立于上层）应满足：

- 给定同一 `TurnSnapshot` 与确定性 Provider 脚本，内核产出的事件序列、消息与 `RunResult` 可复现。
- 内核不执行任何持久化，也不读取工作区或项目配置文件（违反即越界）。
- 在途 Provider 请求期间收到 `abort`：弃流并以 `user_abort` 终止，不产生后续工具副作用。
- 工具任意失败（不存在 / 参数非法 / 超时 / 抛异常）都表现为 `ToolResultBlock(is_error=True)`，循环不中断。
- 并发执行多个工具时，结果事件按**调用序**产出。
- 任意内部异常都被转为 `ErrorEvent` + `fatal` 终止，生成器不抛。
- 所有文件/Shell 副作用都经 `ExecutionEnv`；移除工具内的直连 `pathlib/subprocess` 后，用伪 `ExecutionEnv` 即可完整测试工具，无需真实文件系统。

触发以下信号时，应停下重新检查内核边界（接 §12）：

- 内核出现 `open(...)`、`subprocess`、`pathlib` 直连副作用。
- 内核读取工作区根目录、项目规则或会话存储路径。
- 内核 `import` 了 `cli/` 或 `coding_agent/` 的任何符号。
- 终止原因重新退化为只能靠字符串匹配区分。
- `runtime/blocks` 之外又出现一套进入主链的消息类型。
