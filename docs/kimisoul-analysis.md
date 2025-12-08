# KimiSoul 核心类分析文档

## 概述

`KimiSoul` 是 Kimi CLI 的核心执行引擎，实现了 `Soul` 协议接口。它负责协调 AI Agent 的运行循环、上下文管理、工具调用、错误重试以及独特的"时间回溯"机制（D-Mail）。

## 架构总览

```mermaid
graph TB
    subgraph KimiSoul["KimiSoul 核心"]
        run["run()"]
        agent_loop["_agent_loop()"]
        step["_step()"]
        grow_context["_grow_context()"]
        compact["compact_context()"]
    end
    
    subgraph Dependencies["依赖组件"]
        Agent["Agent<br/>(name, system_prompt, toolset)"]
        Runtime["Runtime<br/>(config, llm, session...)"]
        Context["Context<br/>(history, checkpoints)"]
        DenwaRenji["DenwaRenji<br/>(D-Mail 机制)"]
        Approval["Approval<br/>(用户审批)"]
        Compaction["SimpleCompaction<br/>(上下文压缩)"]
    end
    
    subgraph External["外部服务"]
        LLM["LLM Chat Provider"]
        Wire["Wire<br/>(UI 通信)"]
        Kosong["Kosong Framework"]
    end
    
    run --> agent_loop
    agent_loop --> step
    step --> grow_context
    agent_loop --> compact
    
    KimiSoul --> Agent
    KimiSoul --> Runtime
    KimiSoul --> Context
    Runtime --> DenwaRenji
    Runtime --> Approval
    
    step --> LLM
    step --> Kosong
    agent_loop --> Wire
```

## 类初始化参数详解

### 构造函数签名

```python
def __init__(
    self,
    agent: Agent,
    runtime: Runtime,
    *,
    context: Context,
)
```

### Python 语法说明：`*` 强制关键字参数分隔符

构造函数中的 `*` 是 Python 的**强制关键字参数分隔符**（keyword-only arguments separator）：

```python
def __init__(
    self,
    agent: Agent,      # 位置参数或关键字参数
    runtime: Runtime,  # 位置参数或关键字参数
    *,                 # 分隔符：之后的参数必须用关键字传递
    context: Context,  # 仅关键字参数（keyword-only）
)
```

**调用方式对比**：

```python
# ✅ 正确：context 使用关键字传递
soul = KimiSoul(agent, runtime, context=context)

# ✅ 正确：全部使用关键字
soul = KimiSoul(agent=agent, runtime=runtime, context=context)

# ❌ 错误：context 不能作为位置参数
soul = KimiSoul(agent, runtime, context)  # TypeError!
```

**设计目的**：
1. **提高可读性**：强制调用者明确写出 `context=`，代码意图更清晰
2. **防止参数顺序错误**：避免位置参数传错位置
3. **API 稳定性**：后续可以在 `*` 后添加新参数而不破坏现有调用

### 参数说明

| 参数 | 类型 | 传递方式 | 说明 |
|------|------|----------|------|
| `agent` | `Agent` | 位置/关键字 | 包含 agent 名称、系统提示词和工具集的配置对象 |
| `runtime` | `Runtime` | 位置/关键字 | 运行时环境，包含 LLM、配置、会话等 |
| `context` | `Context` | **仅关键字** | 上下文管理器，负责消息历史和检查点 |

## 内部属性详解

### 核心属性

```mermaid
classDiagram
    class KimiSoul {
        -Agent _agent
        -Runtime _runtime
        -DenwaRenji _denwa_renji
        -Approval _approval
        -Context _context
        -LoopControl _loop_control
        -SimpleCompaction _compaction
        -int _reserved_tokens
        -ThinkingEffort _thinking_effort
        -bool _checkpoint_with_user_message
        +name: str
        +model_name: str
        +model_capabilities: set
        +status: StatusSnapshot
        +context: Context
        +thinking: bool
    }
    
    class Agent {
        +name: str
        +system_prompt: str
        +toolset: Toolset
    }
    
    class Runtime {
        +config: Config
        +llm: LLM
        +session: Session
        +denwa_renji: DenwaRenji
        +approval: Approval
        +disable_curl_tip: bool
    }
    
    class Context {
        +history: Sequence~Message~
        +token_count: int
        +n_checkpoints: int
        +checkpoint()
        +revert_to()
        +append_message()
    }
    
    KimiSoul --> Agent
    KimiSoul --> Runtime
    KimiSoul --> Context
```

### 属性详细说明

| 属性 | 类型 | 作用 |
|------|------|------|
| `_agent` | `Agent` | Agent 配置，包含系统提示词和工具集 |
| `_runtime` | `Runtime` | 运行时环境配置 |
| `_denwa_renji` | `DenwaRenji` | D-Mail 时间回溯机制管理器 |
| `_approval` | `Approval` | 用户审批请求管理器 |
| `_context` | `Context` | 消息历史和检查点管理 |
| `_loop_control` | `LoopControl` | 循环控制参数（最大步数、重试次数等） |
| `_compaction` | `SimpleCompaction` | 上下文压缩策略 |
| `_reserved_tokens` | `int` | 保留的 token 数量（默认 50,000） |
| `_thinking_effort` | `ThinkingEffort` | 思考模式强度（"off"/"high"） |
| `_checkpoint_with_user_message` | `bool` | 是否在检查点添加用户消息标记 |

### 循环检测属性

用于检测 Agent 是否陷入重复命令循环：

| 属性 | 类型 | 作用 |
|------|------|------|
| `_similar_pattern_count` | `int` | 连续相似命令计数器 |
| `_last_commands` | `list[str]` | 最近执行的命令历史（最多 18 条） |
| `_similarity_threshold` | `float` | 相似度阈值（0.85 = 85%） |
| `_min_cmd_length` | `int` | 最小检测命令长度（10 字符） |

## 核心方法流程分析

### 1. run() - 入口方法

```mermaid
flowchart TD
    A[run] --> B{LLM 是否设置?}
    B -->|否| C[抛出 LLMNotSet]
    B -->|是| D{输入包含图片?}
    D -->|是| E{LLM 支持图片?}
    E -->|否| F[抛出 LLMNotSupported]
    E -->|是| G[创建检查点]
    D -->|否| G
    G --> H[添加用户消息到上下文]
    H --> I[进入 _agent_loop]
```

**功能**：
- 验证 LLM 是否已配置
- 检查图片输入的模型能力支持
- 创建初始检查点（checkpoint 0）
- 将用户输入添加到上下文
- 启动主循环

### 2. _agent_loop() - 主循环

```mermaid
flowchart TD
    A[_agent_loop] --> B[step_no = 1]
    B --> C[发送 StepBegin 到 Wire]
    C --> D[启动审批请求管道任务]
    D --> E{上下文是否超限?}
    E -->|是| F[执行上下文压缩]
    F --> G[创建检查点]
    E -->|否| G
    G --> H[更新 DenwaRenji 检查点数]
    H --> I[执行 _step]
    I --> J{捕获 BackToTheFuture?}
    J -->|是| K[回滚到指定检查点]
    K --> L[创建新检查点]
    L --> M[添加 D-Mail 消息]
    M --> C
    J -->|否| N{捕获 ChatProviderError?}
    N -->|是| O[发送 StepInterrupted]
    O --> P[抛出异常]
    N -->|否| Q{step 完成?}
    Q -->|是| R[返回]
    Q -->|否| S[step_no++]
    S --> T{超过最大步数?}
    T -->|是| U[抛出 MaxStepsReached]
    T -->|否| C
```

**核心逻辑**：
1. **循环执行步骤**：每个步骤执行一次 LLM 调用
2. **上下文管理**：自动检测并压缩过长的上下文
3. **D-Mail 处理**：捕获 `BackToTheFuture` 异常实现时间回溯
4. **错误处理**：优雅处理 API 错误和取消事件
5. **步数限制**：防止无限循环

### 3. _step() - 单步执行

```mermaid
flowchart TD
    A[_step] --> B[配置重试策略]
    B --> C[调用 kosong.step]
    C --> D{成功?}
    D -->|否| E{可重试错误?}
    E -->|是| F[等待后重试]
    F --> C
    E -->|否| G[抛出异常]
    D -->|是| H[更新 token 计数]
    H --> I[构建工具调用映射]
    I --> J[获取工具执行结果]
    J --> K{disable_curl_tip?}
    K -->|是| L[直接增长上下文]
    K -->|否| M[后处理工具消息]
    M --> N[增长上下文]
    L --> O[检查 submit_answer]
    N --> O
    O --> P{答案正确?}
    P -->|是| Q[返回 True]
    P -->|否| R{工具被拒绝?}
    R -->|是| S[清除 D-Mail 并返回 True]
    R -->|否| T{有待处理 D-Mail?}
    T -->|是| U[抛出 BackToTheFuture]
    T -->|否| V{有工具调用?}
    V -->|是| W[返回 False 继续循环]
    V -->|否| X[返回 True 结束]
```

#### 工具调用 ID 映射：`id_to_call_info`

在 `_step()` 方法中，有一个关键的数据结构 `id_to_call_info`，用于建立**工具调用 ID 到工具信息的反向映射**：

```python
id_to_call_info: dict[str, tuple[str, str]] = {
    getattr(call, "id"): (
        getattr(getattr(call, "function", None), "name", "") or "",
        getattr(getattr(call, "function", None), "arguments", "") or "",
    )
    for call in (getattr(result, "tool_calls", []) or [])
    if getattr(call, "id", None)
}
```

**映射结构**：
```
tool_call_id  →  (工具名, 参数字符串)
```

**设计背景**：

LLM 返回的 `StepResult` 包含 `tool_calls`（工具调用请求），执行后得到 `tool_results`（工具执行结果）。但 `tool_results` 只包含 `tool_call_id` 和执行结果，**不包含**原始的工具名和参数。因此需要这个映射来反查。

**使用场景**：

| 场景 | 代码位置 | 说明 |
|------|----------|------|
| 后处理工具消息 | `_postprocess_tool_messages()` | 根据工具名/参数对消息做特殊处理（如添加 curl 提示） |
| 检测 `submit_answer` | `_step()` 末尾 | 通过 ID 反查工具名，判断是否是提交答案的工具 |

**防御性编程**：

使用链式 `getattr` 访问是因为 `tool_calls` 可能来自不同 provider，结构不完全一致：

```python
# 安全访问，避免 AttributeError
getattr(getattr(call, "function", None), "name", "") or ""
```

**重试策略**：
- 使用 `tenacity` 库实现指数退避重试
- 初始等待 0.3 秒，最大 10 秒
- 添加随机抖动（jitter）避免雷群效应
- 可重试错误包括：连接错误、超时、空响应、特定 HTTP 状态码

### 4. D-Mail 时间回溯机制

![alt text](image.png)
- [d-mail 理解：压缩上下文的方式之一，用于裁剪当前上下文](https://x.com/tunahorse21/status/1983600710353875037)

```mermaid
sequenceDiagram
    participant Agent as Agent
    participant Soul as KimiSoul
    participant DR as DenwaRenji
    participant Context as Context
    participant Tool as SendDMail Tool
    
    Note over Agent,Context: 正常执行流程
    Agent->>Soul: 执行步骤
    Soul->>Context: 创建检查点 0
    Soul->>DR: set_n_checkpoints(1)
    Agent->>Soul: 执行步骤
    Soul->>Context: 创建检查点 1
    Soul->>DR: set_n_checkpoints(2)
    
    Note over Agent,Context: Agent 发现需要回溯
    Agent->>Tool: 调用 SendDMail(message, checkpoint_id=0)
    Tool->>DR: send_dmail(DMail)
    
    Note over Agent,Context: Soul 检测到 D-Mail
    Soul->>DR: fetch_pending_dmail()
    DR-->>Soul: DMail(checkpoint_id=0)
    Soul->>Soul: 抛出 BackToTheFuture
    
    Note over Agent,Context: 时间回溯
    Soul->>Context: revert_to(checkpoint_id=0)
    Soul->>Context: 创建新检查点
    Soul->>Context: 添加 D-Mail 消息
    
    Note over Agent,Context: 从检查点 0 继续执行
    Agent->>Soul: 基于 D-Mail 信息重新决策
```

**D-Mail 机制说明**：
- 灵感来源于《命运石之门》
- 允许 Agent 向"过去的自己"发送消息
- 通过检查点实现上下文状态回滚
- D-Mail 消息以系统消息形式注入，对用户不可见

### 5. 上下文压缩

```mermaid
flowchart TD
    A[compact_context] --> B[调用 SimpleCompaction.compact]
    B --> C[保留最近 2 条用户/助手消息]
    C --> D[将其余消息转为文本]
    D --> E[调用 LLM 生成摘要]
    E --> F[回滚到检查点 0]
    F --> G[创建新检查点]
    G --> H[添加压缩后的消息]
```

**压缩策略**：
- 保留最近 2 条关键消息（用户/助手）
- 将历史消息发送给 LLM 生成摘要
- 重置上下文并添加压缩后的内容
- 保留 50,000 tokens 作为缓冲区

### 6. 循环检测机制

```mermaid
flowchart TD
    A[_postprocess_tool_messages] --> B{是 Shell 工具?}
    B -->|否| C[返回原始消息]
    B -->|是| D[解析命令]
    D --> E{命令长度 >= 10?}
    E -->|否| C
    E -->|是| F[与上一条命令比较相似度]
    F --> G{相似度 >= 85%?}
    G -->|是| H[similar_pattern_count++]
    H --> I{计数 >= 18?}
    I -->|是| J[注入警告消息]
    J --> K[重置计数器]
    I -->|否| L[添加到历史]
    G -->|否| M[重置计数器]
    M --> L
    K --> L
    L --> C
```

**检测参数**：
- 相似度阈值：85%（使用 `SequenceMatcher`）
- 最小命令长度：10 字符
- 触发警告阈值：连续 18 次相似命令
- 历史记录容量：最近 18 条命令

## 属性和方法速查表

### 公开属性

| 属性 | 返回类型 | 说明 |
|------|----------|------|
| `name` | `str` | Agent 名称 |
| `model_name` | `str` | LLM 模型名称 |
| `model_capabilities` | `set[ModelCapability] \| None` | 模型能力集 |
| `status` | `StatusSnapshot` | 当前状态快照（上下文使用率） |
| `context` | `Context` | 上下文管理器 |
| `thinking` | `bool` | 是否启用思考模式 |

### 公开方法

| 方法 | 参数 | 说明 |
|------|------|------|
| `set_thinking(enabled)` | `bool` | 启用/禁用思考模式 |
| `run(user_input)` | `str \| list[ContentPart]` | 运行 Agent |
| `compact_context()` | 无 | 压缩上下文 |

### 私有方法

| 方法 | 说明 |
|------|------|
| `_checkpoint()` | 创建上下文检查点 |
| `_agent_loop()` | 主执行循环 |
| `_step()` | 执行单个 LLM 步骤 |
| `_grow_context()` | 将结果添加到上下文 |
| `_is_similar_to_last_command()` | 检测命令相似度 |
| `_postprocess_tool_messages()` | 后处理工具消息 |
| `_is_retryable_error()` | 判断错误是否可重试 |
| `_retry_log()` | 记录重试日志 |

## 异常类

### BackToTheFuture

```python
class BackToTheFuture(Exception):
    def __init__(self, checkpoint_id: int, messages: Sequence[Message]):
        self.checkpoint_id = checkpoint_id
        self.messages = messages
```

**用途**：触发时间回溯，将上下文回滚到指定检查点并注入新消息。

## 依赖组件关系

```mermaid
graph LR
    subgraph soul["kimi_cli.soul"]
        KimiSoul
        Agent
        Runtime
        Context
        DenwaRenji
        Approval
        Compaction
    end
    
    subgraph external["外部依赖"]
        kosong["kosong (LLM 框架)"]
        tenacity["tenacity (重试)"]
        httpx["httpx (HTTP)"]
    end
    
    subgraph wire["kimi_cli.wire"]
        Wire
        WireMessage
    end
    
    KimiSoul --> Agent
    KimiSoul --> Runtime
    KimiSoul --> Context
    KimiSoul --> Compaction
    Runtime --> DenwaRenji
    Runtime --> Approval
    
    KimiSoul --> kosong
    KimiSoul --> tenacity
    KimiSoul --> Wire
```

## 配置参数

### LoopControl 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `max_steps_per_run` | 每次运行最大步数 | 配置文件定义 |
| `max_retries_per_step` | 每步最大重试次数 | 配置文件定义 |

### 可重试 HTTP 状态码

```python
(408, 404, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527)
```

## 使用示例

```python
from kimi_cli.soul.kimisoul import KimiSoul
from kimi_cli.soul.agent import load_agent
from kimi_cli.soul.runtime import Runtime
from kimi_cli.soul.context import Context

# 创建运行时
runtime = await Runtime.create(config, llm, session, yolo=False, ...)

# 加载 Agent
agent = await load_agent(agent_file, runtime, mcp_configs=[])

# 创建上下文
context = Context(file_backend=session.context_file)

# 创建 KimiSoul 实例
soul = KimiSoul(agent, runtime, context=context)

# 运行
await soul.run("你好，请帮我分析这段代码")
```

## 总结

`KimiSoul` 是一个功能完善的 AI Agent 执行引擎，具有以下特点：

1. **健壮的错误处理**：指数退避重试、多种可恢复错误类型支持
2. **智能上下文管理**：自动压缩、检查点机制
3. **独特的时间回溯**：D-Mail 机制允许 Agent 修正错误决策
4. **循环检测**：防止 Agent 陷入无效的重复操作
5. **灵活的扩展性**：支持思考模式、多种工具集成
