# Kimi CLI for CTF 竞赛改造分析报告

> 本文档分析了作者在原版 [kimi-cli](https://github.com/MoonshotAI/kimi-cli) 项目基础上，为支持 CTF 竞赛自动化解题所做的改造和新增功能。

## 一、改造概览

### 1.1 项目背景

原版 kimi-cli 是 MoonshotAI 开发的命令行 AI Agent 工具，主要用于通用的编程辅助任务。本项目在此基础上进行了深度定制，使其成为一个专门针对 **腾讯云智能渗透挑战赛（CTF）** 的自动化解题 Agent。

### 1.2 核心改造点

```mermaid
mindmap
  root((CTF Agent 改造))
    专用 Agent 系统
      security Agent
      security_beta Agent
      ctfer Agent
    Daemon 自动解题模式
      无限循环执行
      自动重启机制
      MCP 集成
    防沉迷保护
      命令相似度检测
      循环检测与中断
      智能提示干预
    Session 隔离
      工作目录独立
      历史记录分离
      多实例并行
    自定义 API 支持
      DeepSeek 集成
      通义千问支持
      OpenAI 兼容接口
```

## 二、新增 CTF 专用 Agent

### 2.1 Agent 架构设计

作者新增了三个专门针对 CTF 竞赛的 Agent：

```mermaid
graph TB
    subgraph "CTF Agent 体系"
        A[default Agent<br/>通用编程助手] --> B[security Agent<br/>安全分析专家]
        A --> C[security_beta Agent<br/>带本地知识库]
        A --> D[ctfer Agent<br/>轻量级 CTF]
    end
    
    subgraph "工具集配置"
        B --> T1[Bash/Terminal]
        B --> T2[文件操作工具]
        B --> T3[Web 搜索/抓取]
        B --> T4[Think/Todo]
        
        C --> T1
        C --> T2
        C --> T3
        C --> T5[PayloadsAllTheThings<br/>本地知识库]
        
        D --> T3
        D --> T4
    end
    
    subgraph "MCP 工具集成"
        M[xbow MCP Server] --> M1[list_challenges]
        M --> M2[do_challenge]
        M --> M3[submit_answer]
        M --> M4[get_challenge_hint]
        M --> M5[read/write_challenge_note]
        M --> M6[get_ctf_skill]
    end
```

### 2.2 Agent 配置文件结构

以 `security` Agent 为例，配置文件位于 `src/kimi_cli/agents/security/agent.yaml`：

```yaml
version: 1
agent:
  name: "security"
  system_prompt_path: ./system.md
  system_prompt_args:
    ROLE_ADDITIONAL: ""
  tools:
    - "kimi_cli.tools.task:Task"
    - "kimi_cli.tools.think:Think"
    - "kimi_cli.tools.todo:SetTodoList"
    - "kimi_cli.tools.bash:Bash"
    - "kimi_cli.tools.file:ReadFile"
    - "kimi_cli.tools.file:Glob"
    - "kimi_cli.tools.file:Grep"
    - "kimi_cli.tools.file:WriteFile"
    - "kimi_cli.tools.file:StrReplaceFile"
    - "kimi_cli.tools.web:SearchWeb"
    - "kimi_cli.tools.web:FetchURL"
  subagents:
    coder:
      path: ./sub.yaml
      description: "Good at general software engineering tasks."
```

### 2.3 System Prompt 设计

`security` Agent 的 System Prompt (`src/kimi_cli/agents/security/system.md`) 包含以下关键设计：

#### 2.3.1 角色定位与任务流程

```markdown
你是一个安全分析专家，借助已有的工具进行安全漏洞分析并且拿到 flag

**Challenge Lifecycle (READ → PLAN → DO → NOTE → HINT → SUBMIT)**:
1. 使用 `list_challenges` 获取赛题列表
2. 调用 `read_challenge_note` 读取历史笔记
3. 使用 `do_challenge` 标记开始尝试
4. 迭代过程中随时用 `write_challenge_note` 记录
5. 获得 `flag{...}` 后，使用 `submit_answer` 提交
```

#### 2.3.2 安全测试方法论

System Prompt 中定义了详细的漏洞测试方法：

```mermaid
flowchart TD
    START[开始测试] --> CRAWL[爬虫收集信息]
    CRAWL --> |收集完成| ANALYZE[分析 URL/参数]
    
    ANALYZE --> FUZZ[参数 Fuzz]
    ANALYZE --> XSS[XSS 测试]
    ANALYZE --> SQLI[SQL 注入]
    ANALYZE --> LFI[文件包含]
    ANALYZE --> AUTH[认证绕过]
    
    FUZZ --> |发现异常| EXPLOIT[漏洞利用]
    XSS --> |发现异常| EXPLOIT
    SQLI --> |发现异常| EXPLOIT
    LFI --> |发现异常| EXPLOIT
    AUTH --> |发现异常| EXPLOIT
    
    EXPLOIT --> FLAG{获取 Flag?}
    FLAG --> |是| SUBMIT[提交答案]
    FLAG --> |否| HINT[获取提示]
    HINT --> ANALYZE
```

### 2.4 ctfer Agent 的高级特性

`ctfer` Agent (`src/kimi_cli/agents/ctfer/system.md`) 包含更复杂的策略：

#### 2.4.1 反循环护栏 (Anti-Loop Guardrails)

```markdown
**Anti-Loop Guardrails（反循环护栏）**:
- **时间盒**：同一假设最多 3 轮无新信号，停止该线
- **等价类限额**：每轮最多测试 6 个等价类 payload
- **去重检查**：每次执行前必须比对笔记"覆盖台账"
- **转向触发器**：
  - 连续 2 轮同一端点无新错误指纹 → 调用 get_challenge_hint
  - 30 条命令后仍无可行动线索 → 调用 get_challenge_hint
```

#### 2.4.2 智能 Payload 升级策略

```mermaid
graph LR
    subgraph "Evasion Hierarchy"
        L1[Level 1: 语法绕过<br/>替代语法表达] --> L2[Level 2: 编码绕过<br/>URL/Hex/Base64]
        L2 --> L3[Level 3: 抽象绕过<br/>间接访问]
    end
    
    subgraph "示例"
        E1["/**/替代空格<br/>img替代script"] --> E2["%27 URL编码<br/>字符串拼接"]
        E2 --> E3["window['ale'+'rt']<br/>getattr()"]
    end
```

## 三、Daemon 自动解题模式

### 3.1 实现原理

Daemon 模式在 `src/kimi_cli/ui/shell/__init__.py` 中实现：

```python
async def run(self, command: str | None = None) -> bool:
    if self.daemon:
        # daemon mode: only accept user command, run infinitely
        if command is None or not command.strip():
            console.print("[red]Daemon mode requires --command to be provided[/red]")
            return False
        
        try:
            while True:
                print("🔄 Looping...")
                try:
                    # 重置 context 到初始状态
                    if isinstance(self.soul, KimiSoul) and self.soul.context.n_checkpoints > 0:
                        await self.soul.context.revert_to(0)
                    # 执行用户命令
                    await self._run_soul_command(command, ...)
                except asyncio.CancelledError:
                    break
                except BaseException as e:
                    console.print(f"[red]Daemon iteration error: {e}[/red]")
                await asyncio.sleep(10.0)  # 每轮间隔 10 秒
        except KeyboardInterrupt:
            console.print("Bye!")
        return True
```

### 3.2 工作流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant CLI as Kimi CLI
    participant MCP as xbow MCP
    participant LLM as AI 模型
    
    User->>CLI: kimi --daemon -c "解题指令"
    
    loop 无限循环
        CLI->>CLI: 重置 Context
        CLI->>LLM: 发送解题指令
        LLM->>MCP: list_challenges()
        MCP-->>LLM: 返回题目列表
        
        LLM->>MCP: do_challenge(id)
        LLM->>LLM: 分析题目
        LLM->>CLI: 执行 Bash 命令
        CLI-->>LLM: 命令结果
        
        alt 找到 Flag
            LLM->>MCP: submit_answer(flag)
            MCP-->>LLM: 提交结果
        else 需要提示
            LLM->>MCP: get_challenge_hint()
            MCP-->>LLM: 返回提示
        end
        
        CLI->>CLI: sleep(10s)
    end
```

### 3.3 启动脚本

`start.sh` 实现了带自动重启的守护进程：

```bash
#!/bin/bash
nohup bash -c '
    while true; do
        echo "[$(date)] Agent 启动中..."
        uv run kimi -a security -m deepseek-chat --daemon --verbose \
            -c "优先尝试没有做过的题目,解决的题禁止尝试做和验证..."
        
        echo "[$(date)] Agent 进程已退出，将在 15 秒后重启..."
        sleep 15
    done
' > nohup.out 2>&1 &
```

## 四、命令执行防沉迷保护

### 4.1 设计背景

在 CTF 自动解题过程中，AI Agent 可能陷入无效的循环操作（如重复尝试相似的 payload），导致资源浪费和效率低下。防沉迷保护机制用于检测并中断这种行为。

### 4.2 实现机制

在 `src/kimi_cli/soul/kimisoul.py` 中实现：

```python
class KimiSoul(Soul):
    def __init__(self, ...):
        # 用于跟踪重复命令模式的变量
        self._similar_pattern_count = 0
        self._last_commands: list[str] = []
        self._similarity_threshold = 0.85  # 相似度阈值（85%）
        self._min_cmd_length = 10  # 最小命令长度才进行检测

    def _is_similar_to_last_command(self, current_cmd: str) -> bool:
        """检查当前命令是否与最近一个命令相似（连续重复检测）"""
        current_cmd = current_cmd.strip()
        
        if len(current_cmd) < self._min_cmd_length:
            return False
        if not self._last_commands:
            return False
        
        last_cmd = self._last_commands[-1]
        # 计算与最近命令的相似度 (0.0 - 1.0)
        similarity = SequenceMatcher(None, current_cmd, last_cmd).ratio()
        return similarity >= self._similarity_threshold
```

### 4.3 检测与干预流程

```mermaid
flowchart TD
    CMD[执行命令] --> CHECK{命令长度 >= 10?}
    CHECK --> |否| EXEC[正常执行]
    CHECK --> |是| SIMILAR{与上一命令相似度 >= 85%?}
    
    SIMILAR --> |否| RESET[重置计数器]
    RESET --> EXEC
    
    SIMILAR --> |是| COUNT[计数器 +1]
    COUNT --> THRESHOLD{计数 >= 18?}
    
    THRESHOLD --> |否| EXEC
    THRESHOLD --> |是| WARN[注入警告信息]
    WARN --> RESET2[重置计数器]
    RESET2 --> EXEC
```

### 4.4 警告信息注入

当检测到连续 18 次相似命令时，系统会在工具输出前注入警告：

```python
if self._similar_pattern_count >= 18:
    message = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  检测到可能陷入循环（已连续执行 {self._similar_pattern_count} 次相似命令）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请先思考以下问题来重新制定计划：
1. 我的核心假设是什么？
2. 过去的尝试是否证明了这个假设是错误的？
3. 还有哪些其他的可能性？
4. 是否有更高效的方式？

💡 建议：如果确认当前策略正确，可以继续执行；否则建议调整方法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
```

## 五、Session 隔离机制

### 5.1 设计目的

支持多个 CTF 解题实例并行运行，每个实例维护独立的对话上下文和历史记录。

### 5.2 实现架构

```mermaid
graph TB
    subgraph "Session 管理"
        META[kimi.json<br/>全局元数据] --> WD1[工作目录 A]
        META --> WD2[工作目录 B]
        META --> WD3[工作目录 C]
    end
    
    subgraph "工作目录 A"
        WD1 --> S1[Session 1<br/>uuid-xxx.jsonl]
        WD1 --> S2[Session 2<br/>uuid-yyy.jsonl]
        WD1 --> LAST1[last_session_id]
    end
    
    subgraph "工作目录 B"
        WD2 --> S3[Session 3<br/>uuid-zzz.jsonl]
        WD2 --> LAST2[last_session_id]
    end
```

### 5.3 核心代码

`src/kimi_cli/session.py` 中的 Session 管理：

```python
class Session(NamedTuple):
    id: str
    work_dir: Path
    history_file: Path

    @staticmethod
    def create(work_dir: Path) -> "Session":
        """为工作目录创建新 Session"""
        metadata = load_metadata()
        work_dir_meta = next((wd for wd in metadata.work_dirs if wd.path == str(work_dir)), None)
        if work_dir_meta is None:
            work_dir_meta = WorkDirMeta(path=str(work_dir))
            metadata.work_dirs.append(work_dir_meta)

        session_id = str(uuid.uuid4())
        history_file = work_dir_meta.sessions_dir / f"{session_id}.jsonl"
        return Session(id=session_id, work_dir=work_dir, history_file=history_file)

    @staticmethod
    def continue_(work_dir: Path) -> "Session | None":
        """继续上一次的 Session"""
        metadata = load_metadata()
        work_dir_meta = next((wd for wd in metadata.work_dirs if wd.path == str(work_dir)), None)
        if work_dir_meta is None or work_dir_meta.last_session_id is None:
            return None
        # 返回上次的 Session
        ...
```

### 5.4 多实例并行示例

```bash
# 终端1：使用 DeepSeek 模型做 web 题
cd /path/to/project1
./kimi -a security -m deepseek-chat --daemon --verbose -c "优先做 web 题"

# 终端2：使用其他模型做 pwn 题
cd /path/to/project2
./kimi -a security_beta -m qwen-plus --daemon --verbose -c "优先做 pwn 题"
```

## 六、自定义 OpenAI API 支持

### 6.1 配置架构

```mermaid
graph LR
    subgraph "配置层"
        CONFIG[config.json] --> PROVIDER[providers]
        CONFIG --> MODEL[models]
        CONFIG --> DEFAULT[default_model]
    end
    
    subgraph "Provider 类型"
        PROVIDER --> KIMI[Kimi]
        PROVIDER --> OPENAI[OpenAI Legacy]
        PROVIDER --> OPENAI_R[OpenAI Responses]
        PROVIDER --> ANTHROPIC[Anthropic]
    end
    
    subgraph "第三方服务"
        OPENAI --> DS[DeepSeek]
        OPENAI --> QWEN[通义千问]
        OPENAI --> OTHER[其他兼容服务]
    end
```

### 6.2 配置示例

`src/kimi_cli/config.py` 中的配置结构：

```python
class LLMProvider(BaseModel):
    type: ProviderType  # "kimi" | "openai_legacy" | "openai_responses" | "anthropic"
    base_url: str
    api_key: SecretStr
    custom_headers: dict[str, str] | None = None

class LLMModel(BaseModel):
    provider: str
    model: str
    max_context_size: int
    capabilities: set[ModelCapability] | None = None
```

### 6.3 CLI 参数支持

`src/kimi_cli/cli.py` 中新增的参数：

```python
@cli.command()
def kimi(
    agent: Annotated[str | None, typer.Option("--agent", "-a")] = None,
    model_name: Annotated[str | None, typer.Option("--model", "-m")] = None,
    daemon_mode: Annotated[bool, typer.Option("--daemon")] = False,
    disable_curl_tip: Annotated[bool, typer.Option("--disable-curl-tip")] = False,
    ...
):
```

## 七、Flag 自动提交检测

### 7.1 实现位置

在 `src/kimi_cli/soul/kimisoul.py` 的 `_step` 方法中：

```python
async def _step(self) -> bool:
    # ... 执行工具调用 ...
    
    # 检查是否有 submit_answer 工具调用
    for tool_result in results:
        tool_name, tool_args = id_to_call_info.get(tool_result.tool_call_id, ("", ""))
        if tool_name and "submit_answer" in tool_name.lower():
            out_text = str(getattr(tool_result.result, "output", None))
            norm = out_text.replace(" ", "").lower()
            
            if '"correct":true' in norm:
                print(f"✨ Congratulations! Flag is correct! {tool_args}")
                return True  # 结束当前任务
```

### 7.2 工作流程

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant Tool as Tool System
    participant MCP as xbow MCP
    
    Agent->>Tool: 调用 submit_answer(flag)
    Tool->>MCP: 提交 flag
    MCP-->>Tool: {"correct": true/false}
    Tool-->>Agent: 返回结果
    
    alt correct: true
        Agent->>Agent: 打印成功信息
        Agent->>Agent: 返回 True (结束任务)
    else correct: false
        Agent->>Agent: 继续尝试
    end
```

## 八、整体架构图

```mermaid
graph TB
    subgraph "用户层"
        USER[用户] --> CLI[kimi CLI]
        CLI --> |--agent| AGENT_SELECT{Agent 选择}
        CLI --> |--daemon| DAEMON[Daemon 模式]
        CLI --> |--model| MODEL_SELECT{模型选择}
    end
    
    subgraph "Agent 层"
        AGENT_SELECT --> DEFAULT[default]
        AGENT_SELECT --> SECURITY[security]
        AGENT_SELECT --> SECURITY_BETA[security_beta]
        AGENT_SELECT --> CTFER[ctfer]
    end
    
    subgraph "核心层"
        SECURITY --> SOUL[KimiSoul]
        SOUL --> CONTEXT[Context 管理]
        SOUL --> LOOP[Agent Loop]
        SOUL --> ANTI[防沉迷检测]
    end
    
    subgraph "工具层"
        LOOP --> BASH[Bash 工具]
        LOOP --> FILE[文件工具]
        LOOP --> WEB[Web 工具]
        LOOP --> MCP_TOOL[MCP 工具]
    end
    
    subgraph "MCP 层"
        MCP_TOOL --> XBOW[xbow MCP Server]
        XBOW --> CHALLENGES[题目管理]
        XBOW --> SUBMIT[答案提交]
        XBOW --> HINTS[提示获取]
    end
    
    subgraph "LLM 层"
        MODEL_SELECT --> KIMI_API[Kimi API]
        MODEL_SELECT --> DEEPSEEK[DeepSeek]
        MODEL_SELECT --> QWEN[通义千问]
        MODEL_SELECT --> CUSTOM[自定义 API]
    end
    
    subgraph "Session 层"
        CONTEXT --> SESSION[Session 管理]
        SESSION --> HISTORY[历史记录]
        SESSION --> ISOLATION[目录隔离]
    end
```

## 九、总结

### 9.1 主要改造清单

| 改造项 | 文件/目录 | 说明 |
|--------|----------|------|
| CTF Agent | `src/kimi_cli/agents/security/` | 安全分析专家 Agent |
| CTF Agent | `src/kimi_cli/agents/security_beta/` | 带本地知识库的 Agent |
| CTF Agent | `src/kimi_cli/agents/ctfer/` | 轻量级 CTF Agent |
| Daemon 模式 | `src/kimi_cli/cli.py` | 新增 `--daemon` 参数 |
| Daemon 模式 | `src/kimi_cli/ui/shell/__init__.py` | 实现无限循环执行 |
| 防沉迷保护 | `src/kimi_cli/soul/kimisoul.py` | 命令相似度检测与干预 |
| Session 隔离 | `src/kimi_cli/session.py` | 工作目录独立 Session |
| Session 隔离 | `src/kimi_cli/metadata.py` | 元数据管理 |
| 自定义 API | `src/kimi_cli/config.py` | 支持第三方 OpenAI 兼容 API |
| Flag 检测 | `src/kimi_cli/soul/kimisoul.py` | 自动检测正确答案 |
| 启动脚本 | `start.sh` | 带自动重启的守护进程 |

### 9.2 技术亮点

1. **模块化 Agent 设计**：通过 YAML 配置和 Markdown System Prompt 分离，便于扩展和维护
2. **智能防沉迷**：基于字符串相似度的命令模式检测，避免 AI 陷入无效循环
3. **完善的 MCP 集成**：与 xbow 平台无缝对接，实现自动获取题目、提交答案
4. **多模型支持**：兼容多种 OpenAI 兼容 API，灵活选择模型
5. **Session 隔离**：支持多实例并行，提高解题效率

### 9.3 使用建议

1. 配合 [ez-xbow-platform-mcp](https://github.com/m-sec-org/ez-xbow-platform-mcp) 使用
2. 根据题目类型选择合适的 Agent（`security` 或 `security_beta`）
3. 使用 `--verbose` 参数监控执行状态
4. 对于复杂题目，可以多开实例并行尝试不同策略
