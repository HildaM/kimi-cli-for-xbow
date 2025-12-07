# Python 语言特性速查手册

本文档记录 Python 中常见的语言特性和语法糖，便于快速查阅。

> 示例代码主要来源于 `kimi-cli` 项目的 `src/kimi_cli/soul/` 目录。

---

## 目录

- [函数参数](#函数参数)
  - [强制关键字参数 `*`](#强制关键字参数-)
  - [可变参数 `*args` 和 `**kwargs`](#可变参数-args-和-kwargs)
- [异步编程](#异步编程)
  - [`async/await` 基础](#asyncawait-基础)
  - [为什么需要 `await`](#为什么需要-await)
  - [并发执行](#并发执行)
  - [`asyncio.to_thread` 同步转异步](#asyncioto_thread-同步转异步)
  - [`async for` 异步迭代](#async-for-异步迭代)
  - [`async with` 异步上下文管理器](#async-with-异步上下文管理器)
  - [`asyncio.Queue` 泛型队列](#asyncioqueue-泛型队列)
  - [`asyncio.wait` 等待多个任务](#asynciowait-等待多个任务)
- [类型注解](#类型注解)
  - [基础类型注解](#基础类型注解)
  - [泛型和复杂类型](#泛型和复杂类型)
  - [`TYPE_CHECKING` 条件导入](#type_checking-条件导入)
  - [Protocol 协议类](#protocol-协议类)
  - [`NamedTuple` 命名元组](#namedtuple-命名元组)
  - [`type` 类型别名语句](#type-类型别名语句)
- [常用语法糖](#常用语法糖)
  - [海象运算符 `:=`](#海象运算符-)
  - [f-string 格式化](#f-string-格式化)
  - [列表/字典推导式](#列表字典推导式)
  - [`for-else` 循环](#for-else-循环)
- [ContextVar 上下文变量](#contextvar-上下文变量)
- [反射与内省](#反射与内省)
- [Pydantic 数据模型](#pydantic-数据模型)
- [functools 函数工具](#functools-函数工具)
- [Match 语句](#match-语句python-310)
- [装饰器](#装饰器)
- [异常处理进阶](#异常处理进阶)
- [`string.Template` 字符串模板](#stringtemplate-字符串模板)

---

## 函数参数

### 强制关键字参数 `*`

`*` 单独出现在参数列表中，表示其后的参数**必须使用关键字传递**。

```python
def __init__(
    self,
    agent: Agent,      # 位置参数或关键字参数
    runtime: Runtime,  # 位置参数或关键字参数
    *,                 # 分隔符
    context: Context,  # 仅关键字参数（keyword-only）
):
    pass
```

**调用方式**：

```python
# ✅ 正确
obj = MyClass(agent, runtime, context=context)
obj = MyClass(agent=agent, runtime=runtime, context=context)

# ❌ 错误：context 不能作为位置参数
obj = MyClass(agent, runtime, context)  # TypeError!
```

**设计目的**：
1. 提高可读性：强制写出参数名
2. 防止顺序错误：避免位置参数传错
3. API 稳定性：可在 `*` 后添加新参数而不破坏现有调用

---

### 可变参数 `*args` 和 `**kwargs`

```python
def func(*args, **kwargs):
    print(args)    # 元组：收集所有位置参数
    print(kwargs)  # 字典：收集所有关键字参数

func(1, 2, 3, name="test", value=42)
# args = (1, 2, 3)
# kwargs = {'name': 'test', 'value': 42}
```

**解包用法**：

```python
def greet(name, age, city):
    print(f"{name}, {age}, {city}")

data = ["Alice", 25, "Beijing"]
greet(*data)  # 解包列表 → greet("Alice", 25, "Beijing")

info = {"name": "Bob", "age": 30, "city": "Shanghai"}
greet(**info)  # 解包字典 → greet(name="Bob", age=30, city="Shanghai")
```

---

## 异步编程

### `async/await` 基础

Python 的异步函数（`async def`）调用时**不会立即执行**，而是返回一个协程对象。

```python
# 普通函数：调用即执行
def normal():
    print("执行了")
    return 42

result = normal()  # ✅ 立即执行，result = 42
```

```python
# 异步函数：调用只创建协程对象
async def async_func():
    print("执行了")
    return 42

coro = async_func()   # ❌ 不执行！coro 是 coroutine 对象
result = await async_func()  # ✅ 现在才执行，result = 42
```

---

### 为什么需要 `await`

Python 的 `async def` 定义的是**协程生成器**，调用它只是创建任务描述，不是执行任务。

| 操作 | 含义 |
|------|------|
| `async_func()` | 创建任务（写菜谱） |
| `await async_func()` | 执行任务（做菜） |

**与 Go 的对比**：

| 语言 | 调用函数 | 并发 |
|------|---------|------|
| Go | 直接执行 | `go func()` 启动协程 |
| Python | `async` 函数只创建任务 | `await` 执行，`gather` 并发 |

**顺序执行示例**：

```python
async def run(self):
    await self._checkpoint()           # 1. 等待完成
    await self._context.append_message(...)  # 2. 等待完成
    await self._agent_loop()           # 3. 等待完成
```

---

### 并发执行

```python
import asyncio

# 方式1：顺序执行（慢）
result1 = await fetch_user()
result2 = await fetch_orders()
result3 = await fetch_products()

# 方式2：并发执行（快）
results = await asyncio.gather(
    fetch_user(),
    fetch_orders(),
    fetch_products(),
)

# 方式3：创建任务后并发
task1 = asyncio.create_task(fetch_user())
task2 = asyncio.create_task(fetch_orders())
# ... 做其他事情 ...
result1 = await task1
result2 = await task2
```

**`asyncio.shield`**：保护任务不被取消

```python
# 即使外部取消，也要完成这个操作
await asyncio.shield(self._grow_context(result, results))
```

---

### `asyncio.to_thread` 同步转异步

将同步阻塞函数放到线程池中执行，避免阻塞事件循环。

```python
# 来源：runtime.py
import asyncio

def _list_work_dir(work_dir: Path) -> str:
    """同步阻塞函数：执行 shell 命令"""
    ls = subprocess.run(["ls", "-la", work_dir], capture_output=True, text=True)
    return ls.stdout.strip()

# 在异步函数中调用同步函数
async def create():
    # 将同步函数包装为异步，在线程池中执行
    ls_output = await asyncio.to_thread(_list_work_dir, session.work_dir)
    
    # 可以与其他异步操作并发执行
    ls_output, agents_md = await asyncio.gather(
        asyncio.to_thread(_list_work_dir, session.work_dir),
        asyncio.to_thread(load_agents_md, session.work_dir),
    )
```

**适用场景**：文件 I/O、调用外部命令、数据库同步驱动、CPU 密集型操作

---

### `async for` 异步迭代

```python
# 来源：context.py
async with aiofiles.open(self._file_backend, encoding="utf-8") as f:
    async for line in f:  # 异步逐行读取
        if not line.strip():
            continue
        line_json = json.loads(line)
```

---

### `async with` 异步上下文管理器

```python
# 来源：context.py - 多个异步上下文管理器
async with (
    aiofiles.open(rotated_file_path, encoding="utf-8") as old_file,
    aiofiles.open(self._file_backend, "w", encoding="utf-8") as new_file,
):
    async for line in old_file:
        await new_file.write(line)
```

---

### `asyncio.Queue` 泛型队列

```python
# 来源：approval.py
import asyncio

class Approval:
    def __init__(self):
        # 带类型参数的异步队列
        self._request_queue = asyncio.Queue[ApprovalRequest]()
    
    async def request(self, ...):
        self._request_queue.put_nowait(request)  # 非阻塞放入
    
    async def fetch_request(self) -> ApprovalRequest:
        return await self._request_queue.get()  # 阻塞获取
```

---

### `asyncio.wait` 等待多个任务

```python
# 来源：__init__.py
await asyncio.wait(
    [soul_task, cancel_event_task],
    return_when=asyncio.FIRST_COMPLETED,  # 第一个完成就返回
)

# return_when 选项：
# - FIRST_COMPLETED: 任一完成
# - FIRST_EXCEPTION: 任一异常
# - ALL_COMPLETED: 全部完成（默认）
```

---

## 类型注解

### 基础类型注解

```python
# 变量注解
name: str = "Alice"
age: int = 25
scores: list[int] = [90, 85, 92]
config: dict[str, Any] = {"key": "value"}

# 函数注解
def greet(name: str, age: int) -> str:
    return f"Hello, {name}!"

# 可选类型
def find(id: int) -> User | None:  # Python 3.10+
    pass

from typing import Optional
def find(id: int) -> Optional[User]:  # 旧写法
    pass
```

---

### 泛型和复杂类型

```python
from typing import TypeVar, Generic, Sequence, Callable, Coroutine, Any

# 泛型
T = TypeVar('T')

class Container(Generic[T]):
    def __init__(self, item: T):
        self.item = item

# 序列类型
def process(items: Sequence[Message]) -> None:
    pass

# 可调用类型
type UILoopFn = Callable[[WireUISide], Coroutine[Any, Any, None]]
```

---

### `TYPE_CHECKING` 条件导入

避免循环导入，仅在类型检查时导入。

```python
# 来源：__init__.py, kimisoul.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 这些导入只在类型检查器运行时生效，运行时不执行
    from kimi_cli.llm import LLM, ModelCapability

# 使用时需要用字符串形式（前向引用）
def __init__(self, llm: "LLM", capabilities: "list[ModelCapability]"):
    pass
```

---

### Protocol 协议类

定义结构化子类型（鸭子类型的静态版本）。

```python
# 来源：__init__.py, compaction.py
from typing import Protocol, runtime_checkable

@runtime_checkable  # 允许运行时使用 isinstance() 检查
class Soul(Protocol):
    """定义 Soul 必须具有的接口"""
    
    @property
    def name(self) -> str:
        ...  # ... 表示抽象方法
    
    async def run(self, user_input: str | list[ContentPart]):
        ...

# 任何实现了这些方法的类都被视为 Soul
class KimiSoul(Soul):
    @property
    def name(self) -> str:
        return self._agent.name
```

**Protocol vs ABC**：

| 特性 | Protocol | ABC |
|------|----------|-----|
| 继承要求 | 不需要继承 | 必须继承 |
| 检查方式 | 结构化（鸭子类型） | 名义化（显式继承） |

---

### `NamedTuple` 命名元组

不可变的数据类，支持类型注解。

```python
# 来源：runtime.py, agent.py
from typing import NamedTuple

class Agent(NamedTuple):
    name: str
    system_prompt: str
    toolset: Toolset

# 使用
agent = Agent(name="kimi", system_prompt="...", toolset=toolset)
print(agent.name)       # 像对象一样访问
name, prompt, tools = agent  # 像元组一样解包
agent._asdict()         # 转换为字典
```

---

### `type` 类型别名语句

Python 3.12+ 新语法。

```python
# 来源：__init__.py, agent.py
type UILoopFn = Callable[[WireUISide], Coroutine[Any, Any, None]]
type ToolType = CallableTool | CallableTool2[Any]

# 旧写法（Python 3.9+）
from typing import TypeAlias
UILoopFn: TypeAlias = Callable[[WireUISide], Coroutine[Any, Any, None]]
```

---

## 常用语法糖

### 海象运算符 `:=`

在表达式中赋值（Python 3.8+）。

```python
# 来源：kimisoul.py
if dmail := self._denwa_renji.fetch_pending_dmail():
    process(dmail)

# 循环中使用
while (line := file.readline()):
    process(line)
```

---

### f-string 格式化

```python
name = "Alice"
age = 25

print(f"Name: {name}, Age: {age}")
print(f"Pi: {3.14159:.2f}")  # Pi: 3.14
print(f"{name=}, {age=}")    # 调试：name='Alice', age=25
```

---

### 列表/字典推导式

```python
# 列表推导式
squares = [x**2 for x in range(10)]
evens = [x for x in range(10) if x % 2 == 0]

# 字典推导式（来源：kimisoul.py）
id_to_call_info: dict[str, tuple[str, str]] = {
    getattr(call, "id"): (
        getattr(getattr(call, "function", None), "name", "") or "",
        getattr(getattr(call, "function", None), "arguments", "") or "",
    )
    for call in (getattr(result, "tool_calls", []) or [])
    if getattr(call, "id", None)
}
```

---

### `for-else` 循环

`for` 循环正常完成（没有 `break`）时执行 `else` 块。

```python
# 来源：kimisoul.py
for tool in agent.toolset.tools:
    if tool.name == SendDMail_NAME:
        self._checkpoint_with_user_message = True
        break  # 找到了，不执行 else
else:
    # 循环正常结束（没有 break），说明没找到
    self._checkpoint_with_user_message = False
```

---

## ContextVar 上下文变量

线程安全的上下文变量，常用于异步编程中传递请求级别的状态。

```python
# 来源：__init__.py, toolset.py
from contextvars import ContextVar

# 定义上下文变量
_current_wire = ContextVar[Wire | None]("current_wire", default=None)

# 获取当前值
def get_wire_or_none() -> Wire | None:
    return _current_wire.get()

# 设置值，返回 token 用于后续重置
wire_token = _current_wire.set(wire)

# 使用 token 重置为之前的值
_current_wire.reset(wire_token)

# 完整使用模式（来源：toolset.py）
class CustomToolset(SimpleToolset):
    def handle(self, tool_call: ToolCall) -> HandleResult:
        token = current_tool_call.set(tool_call)
        try:
            return super().handle(tool_call)
        finally:
            current_tool_call.reset(token)
```

---

## 反射与内省

### `getattr` 动态获取属性

```python
# 来源：kimisoul.py
# getattr(obj, name, default)
value = getattr(call, "id", None)  # 获取 call.id，不存在返回 None
```

### `inspect` 模块

```python
# 来源：agent.py
import inspect

# 获取函数签名
for param in inspect.signature(cls).parameters.values():
    if param.kind == inspect.Parameter.KEYWORD_ONLY:
        break
    # 使用 param.annotation 进行依赖注入
```

### `importlib` 动态导入

```python
# 来源：agent.py
import importlib

module_name, class_name = "kimi_cli.tools.bash:Bash".rsplit(":", 1)
module = importlib.import_module(module_name)
cls = getattr(module, class_name, None)
```

---

## Pydantic 数据模型

```python
# 来源：denwarenji.py
from pydantic import BaseModel, Field

class DMail(BaseModel):
    message: str = Field(description="The message to send.")
    checkpoint_id: int = Field(description="...", ge=0)  # ge=0 大于等于0

# 使用
dmail = DMail(message="Hello", checkpoint_id=1)
dmail.model_dump()       # 转字典
dmail.model_dump_json()  # 转 JSON
DMail.model_validate({"message": "Hi", "checkpoint_id": 0})  # 从字典创建
```

---

## functools 函数工具

### `partial` 偏函数

```python
# 来源：kimisoul.py
from functools import partial

def _retry_log(name: str, retry_state: RetryCallState):
    logger.info(f"Retrying {name}...")

# 固定第一个参数
@tenacity.retry(before_sleep=partial(self._retry_log, "step"))
async def _kosong_step_with_retry():
    pass
```

---

## Match 语句（Python 3.10+）

```python
# 来源：approval.py
match response:
    case ApprovalResponse.APPROVE:
        return True
    case ApprovalResponse.APPROVE_FOR_SESSION:
        self._auto_approve_actions.add(action)
        return True
    case ApprovalResponse.REJECT:
        return False

# 来源：message.py - 结合海象运算符
match output := result.output:
    case str(text):
        content.append(TextPart(text=text))
    case ContentPart():
        content.append(output)
    case _:  # 默认情况
        content.extend(output)
```

---

## 装饰器

### `@override` 装饰器（Python 3.12+）

```python
# 来源：toolset.py
from typing import override

class CustomToolset(SimpleToolset):
    @override  # 明确表示重写父类方法
    def handle(self, tool_call: ToolCall) -> HandleResult:
        ...
```

### `@staticmethod` 静态方法

```python
# 来源：kimisoul.py
class KimiSoul:
    @staticmethod
    def _is_retryable_error(exception: BaseException) -> bool:
        """不需要访问 self"""
        return isinstance(exception, APIConnectionError)
```

### `@property` 属性装饰器

```python
class MyClass:
    @property
    def name(self) -> str:
        return self._name
    
    @name.setter
    def name(self, value: str):
        self._name = value
```

---

## 异常处理进阶

### `raise ... from None` 隐藏异常链

```python
# 来源：__init__.py
try:
    await soul_task
except asyncio.CancelledError:
    raise RunCancelled from None  # 隐藏原始异常
```

### `contextlib.suppress` 忽略异常

```python
# 来源：__init__.py
from contextlib import suppress

with suppress(asyncio.CancelledError):
    await cancel_event_task
```

---

## `string.Template` 字符串模板

```python
# 来源：agent.py, compaction.py
from string import Template

template = Template("Hello, $name!")
result = template.substitute(name="Alice")

# 实际应用：系统提示词模板
return Template(system_prompt).substitute(
    builtin_args._asdict(),
    **args
)
```

---

## 持续更新中...

如有新的语法特性需要记录，请在此文档中补充。
