import asyncio
import json
import shlex
from collections.abc import Sequence
from difflib import SequenceMatcher
from functools import partial
from typing import TYPE_CHECKING, Sequence as TypingSequence, cast
import httpx
import httpcore

import kosong
import tenacity
from kosong import StepResult
from kosong.chat_provider import (
    APIConnectionError,
    APIEmptyResponseError,
    APIStatusError,
    APITimeoutError,
    ChatProviderError,
    ThinkingEffort,
)
from kosong.message import ContentPart, ImageURLPart, Message
from kosong.tooling import ToolResult, ToolOk
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from kimi_cli.llm import ModelCapability
from kimi_cli.soul import (
    LLMNotSet,
    LLMNotSupported,
    MaxStepsReached,
    Soul,
    StatusSnapshot,
    wire_send,
)
from kimi_cli.soul.agent import Agent
from kimi_cli.soul.compaction import SimpleCompaction
from kimi_cli.soul.context import Context
from kimi_cli.soul.message import system, tool_result_to_messages
from kimi_cli.soul.runtime import Runtime
from kimi_cli.tools.dmail import NAME as SendDMail_NAME
from kimi_cli.tools.utils import ToolRejectedError
from kimi_cli.utils.logging import logger
from kimi_cli.wire.message import (
    CompactionBegin,
    CompactionEnd,
    StatusUpdate,
    StepBegin,
    StepInterrupted,
)

if TYPE_CHECKING:

    def type_check(soul: "KimiSoul"):
        _: Soul = soul


RESERVED_TOKENS = 50_000


class KimiSoul(Soul):
    """The soul of Kimi CLI."""

    def __init__(
        self,
        agent: Agent,
        runtime: Runtime,
        *,
        context: Context,
    ):
        """
        Initialize the soul.

        Args:
            agent (Agent): The agent to run.
            runtime (Runtime): Runtime parameters and states.
            context (Context): The context of the agent.
        """
        self._agent = agent
        self._runtime = runtime
        self._denwa_renji = runtime.denwa_renji
        self._approval = runtime.approval
        self._context = context
        self._loop_control = runtime.config.loop_control
        self._compaction = SimpleCompaction()  # TODO: maybe configurable and composable
        self._reserved_tokens = RESERVED_TOKENS
        if self._runtime.llm is not None:
            assert self._reserved_tokens <= self._runtime.llm.max_context_size
        self._thinking_effort: ThinkingEffort = "off"

        # 用于跟踪重复命令模式的变量
        self._similar_pattern_count = 0
        self._last_commands: list[str] = []
        self._similarity_threshold = 0.85  # 相似度阈值（85%）
        self._min_cmd_length = 10  # 最小命令长度才进行检测

        for tool in agent.toolset.tools:
            if tool.name == SendDMail_NAME:
                self._checkpoint_with_user_message = True
                break
        else:
            self._checkpoint_with_user_message = False

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def model_name(self) -> str:
        return self._runtime.llm.chat_provider.model_name if self._runtime.llm else ""

    @property
    def model_capabilities(self) -> set[ModelCapability] | None:
        if self._runtime.llm is None:
            return None
        return self._runtime.llm.capabilities

    @property
    def status(self) -> StatusSnapshot:
        return StatusSnapshot(context_usage=self._context_usage)

    @property
    def context(self) -> Context:
        return self._context

    @property
    def _context_usage(self) -> float:
        if self._runtime.llm is not None:
            return self._context.token_count / self._runtime.llm.max_context_size
        return 0.0

    @property
    def thinking(self) -> bool:
        """Whether thinking mode is enabled."""
        return self._thinking_effort != "off"

    def set_thinking(self, enabled: bool) -> None:
        """
        Enable/disable thinking mode for the soul.

        Raises:
            LLMNotSet: When the LLM is not set.
            LLMNotSupported: When the LLM does not support thinking mode.
        """
        if self._runtime.llm is None:
            raise LLMNotSet()
        if enabled and "thinking" not in self._runtime.llm.capabilities:
            raise LLMNotSupported(self._runtime.llm, ["thinking"])
        self._thinking_effort = "high" if enabled else "off"

    async def _checkpoint(self):
        await self._context.checkpoint(self._checkpoint_with_user_message)

    async def run(self, user_input: str | list[ContentPart]):
        if self._runtime.llm is None:
            raise LLMNotSet()

        if (
            isinstance(user_input, list)
            and any(isinstance(part, ImageURLPart) for part in user_input)
            and "image_in" not in self._runtime.llm.capabilities
        ):
            raise LLMNotSupported(self._runtime.llm, ["image_in"])

        await self._checkpoint()  # this creates the checkpoint 0 on first run
        await self._context.append_message(Message(role="user", content=user_input))
        logger.debug("Appended user message to context")
        await self._agent_loop()

    async def _agent_loop(self):
        """The main agent loop for one run."""
        assert self._runtime.llm is not None

        async def _pipe_approval_to_wire():
            while True:
                request = await self._approval.fetch_request()
                wire_send(request)

        step_no = 1
        while True:
            wire_send(StepBegin(step_no))
            approval_task = asyncio.create_task(_pipe_approval_to_wire())
            # FIXME: It's possible that a subagent's approval task steals approval request
            # from the main agent. We must ensure that the Task tool will redirect them
            # to the main wire. See `_SubWire` for more details. Later we need to figure
            # out a better solution.
            try:
                # compact the context if needed
                if (
                    self._context.token_count + self._reserved_tokens
                    >= self._runtime.llm.max_context_size
                ):
                    logger.info("Context too long, compacting...")
                    wire_send(CompactionBegin())
                    await self.compact_context()
                    wire_send(CompactionEnd())

                logger.debug("Beginning step {step_no}", step_no=step_no)
                await self._checkpoint()
                self._denwa_renji.set_n_checkpoints(self._context.n_checkpoints)
                finished = await self._step()
            except BackToTheFuture as e:
                await self._context.revert_to(e.checkpoint_id)
                await self._checkpoint()
                await self._context.append_message(e.messages)
                continue
            except (ChatProviderError, asyncio.CancelledError):
                wire_send(StepInterrupted())
                # break the agent loop
                raise
            finally:
                approval_task.cancel()  # stop piping approval requests to the wire

            if finished:
                return

            step_no += 1
            if step_no > self._loop_control.max_steps_per_run:
                raise MaxStepsReached(self._loop_control.max_steps_per_run)

    async def _step(self) -> bool:
        """Run an single step and return whether the run should be stopped."""
        # already checked in `run`
        assert self._runtime.llm is not None
        chat_provider = self._runtime.llm.chat_provider

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "step"),
            wait=wait_exponential_jitter(initial=0.3, max=10, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _kosong_step_with_retry() -> StepResult:
            # run an LLM step (may be interrupted)
            return await kosong.step(
                chat_provider.with_thinking(self._thinking_effort),
                self._agent.system_prompt,
                self._agent.toolset,
                self._context.history,
                on_message_part=wire_send,
                on_tool_result=wire_send,
            )

        result = await _kosong_step_with_retry()
        logger.debug("Got step result: {result}", result=result)
        if result.usage is not None:
            # mark the token count for the context before the step
            await self._context.update_token_count(result.usage.input)
            wire_send(StatusUpdate(status=self.status))

        # 适配 submit_answer：为本次 step 构建组合映射
        # tool_call_id -> (工具名, 参数字符串)，用于后续通过 ToolResult 反查
        id_to_call_info: dict[str, tuple[str, str]] = {
            getattr(call, "id"): (
                getattr(getattr(call, "function", None), "name", "") or "",
                getattr(getattr(call, "function", None), "arguments", "") or "",
            )
            for call in (getattr(result, "tool_calls", []) or [])
            if getattr(call, "id", None)
        }

        results = await result.tool_results()
        logger.debug("Got tool results: {results}", results=results)
        
        # 工具调用消息 hook
        if self._runtime.disable_curl_tip:
            await asyncio.shield(self._grow_context(result, results))
        else:
            processed_tool_messages: list[Message] = []
            for tool_result in results:
                msgs = tool_result_to_messages(tool_result)
                tool_name, tool_args = id_to_call_info.get(tool_result.tool_call_id, ("", ""))
                msgs = self._postprocess_tool_messages(tool_result, tool_name, tool_args, msgs)
                processed_tool_messages.extend(msgs)
            await asyncio.shield(self._grow_context(result, results, tool_messages=processed_tool_messages))

        # 检查是否有 submit_answer 工具，如果有则检查其输出是否为正确答案
        for tool_result in results:
            tool_name, tool_args = id_to_call_info.get(tool_result.tool_call_id, ("", ""))
            if tool_name and "submit_answer" in tool_name.lower():
                out = getattr(tool_result.result, "output", None)

                # 安全取文本（兼容 dict / list[TextPart] / 其它）
                if isinstance(out, dict):
                    out_text = str(out)
                elif isinstance(out, list):
                    out_text = " ".join(getattr(p, "text", str(p)) for p in out)
                else:
                    out_text = str(out)

                norm = out_text.replace(" ", "").lower()
                if '"correct":true' in norm:
                    print(f"✨ Congratulations! Flag is correct! {tool_args}:{tool_result.result.output}")
                    return True
        rejected = any(
            isinstance(result.result, ToolRejectedError) for result in results
        )
        if rejected:
            _ = self._denwa_renji.fetch_pending_dmail()
            return True

        # handle pending D-Mail
        if dmail := self._denwa_renji.fetch_pending_dmail():
            assert dmail.checkpoint_id >= 0, "DenwaRenji guarantees checkpoint_id >= 0"
            assert (
                dmail.checkpoint_id < self._context.n_checkpoints
            ), "DenwaRenji guarantees checkpoint_id < n_checkpoints"
            # raise to let the main loop take us back to the future
            raise BackToTheFuture(
                dmail.checkpoint_id,
                [
                    Message(
                        role="user",
                        content=[
                            system(
                                "You just got a D-Mail from your future self. "
                                "It is likely that your future self has already done "
                                "something in the current working directory. Please read "
                                "the D-Mail and decide what to do next. You MUST NEVER "
                                "mention to the user about this information. "
                                f"D-Mail content:\n\n{dmail.message.strip()}"
                            )
                        ],
                    )
                ],
            )

        return not result.tool_calls

    async def _grow_context(
        self,
        result: StepResult,
        tool_results: list[ToolResult],
        *,
        tool_messages: list[Message] | None = None,
    ):
        logger.debug("Growing context with result: {result}", result=result)
        await self._context.append_message(result.message)
        if result.usage is not None:
            await self._context.update_token_count(result.usage.total)

        # token count of tool results are not available yet
        if tool_messages is None:
            for tool_result in tool_results:
                logger.debug(
                    "Appending tool result to context: {tool_result}",
                    tool_result=tool_result,
                )
                await self._context.append_message(tool_result_to_messages(tool_result))
        else:
            for msg in tool_messages:
                logger.debug(
                    "Appending postprocessed tool message to context: {message}",
                    message=msg,
                )
                await self._context.append_message(msg)

    def _is_similar_to_last_command(self, current_cmd: str) -> bool:
        """
        检查当前命令是否与最近一个命令相似（连续重复检测）。
        
        Args:
            current_cmd: 当前执行的命令
            
        Returns:
            当前命令是否与最近一个命令相似（连续重复）
        """
        current_cmd = current_cmd.strip()
        
        # 命令太短，不进行检测
        if len(current_cmd) < self._min_cmd_length:
            return False
        
        # 没有历史命令
        if not self._last_commands:
            return False
        
        # 只检查最近的一个命令（连续性检测）
        last_cmd = self._last_commands[-1]
        if len(last_cmd) < self._min_cmd_length:
            return False
        
        # 计算与最近命令的相似度 (0.0 - 1.0)
        similarity = SequenceMatcher(None, current_cmd, last_cmd).ratio()
        
        return similarity >= self._similarity_threshold

    def _postprocess_tool_messages(
        self,
        tool_result: ToolResult,
        tool_name: str,
        tool_args: str,
        messages: list[Message],
    ) -> list[Message]:
        # --- curl 跟踪逻辑开始 ---
        command = ""
        is_shell_tool = tool_name in ("Bash", "kail_terminal")

        if is_shell_tool:
            try:
                # tool_args 是一个 JSON 字符串，例如 '{"command": "ls -l"}'
                args_dict = json.loads(tool_args)
                command = args_dict.get("command", "")
            except (json.JSONDecodeError, AttributeError):
                command = ""  # 无法解析参数

        if is_shell_tool and command:
            # 检查当前命令是否与最近一个命令相似（连续重复）
            is_similar = self._is_similar_to_last_command(command)
            
            # 添加到历史记录（放在检测之后，避免自己和自己比较）
            self._last_commands.append(command)
            if len(self._last_commands) > 18:
                self._last_commands.pop(0)

            # 如果连续相似，累加计数；否则重置
            if is_similar:
                self._similar_pattern_count += 1

                if self._similar_pattern_count >= 18:  # 阈值为 18
                    history = "\n".join(f"- {cmd}" for cmd in self._last_commands)
                    message = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  检测到可能陷入循环（已连续执行 {self._similar_pattern_count} 次相似命令）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

检测到连续重复的命令模式，可能是低效的遍历或枚举操作（如 IDOR、目录扫描等）。

上述命令已正常执行，但在继续之前，请先思考以下问题来重新制定计划：

1. 我的核心假设是什么？
2. 过去 {self._similar_pattern_count} 次的尝试，是否证明了这个假设是错误的，或者是有前提条件的？
3. 除了当前的方法，还有哪些其他的可能性？
4. 是否有更高效的方式（如批量处理、自动化脚本）来完成目标？

最近执行的命令历史：
{history}

💡 建议：如果确认当前策略正确，可以继续执行；否则建议调整方法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
                    # 将干预信息添加到工具输出的前面
                    if messages and isinstance(messages[0].content, str):
                        messages[0].content = system(message).text + messages[0].content

                    # 重置计数器以避免重复提示
                    self._similar_pattern_count = 0
            else:
                # 执行了不相似的命令，重置计数器（连续性中断）
                self._similar_pattern_count = 0
        return messages

    async def compact_context(self) -> None:
        """
        Compact the context.

        Raises:
            LLMNotSet: When the LLM is not set.
            ChatProviderError: When the chat provider returns an error.
        """

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "compaction"),
            wait=wait_exponential_jitter(initial=0.3, max=10, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _compact_with_retry() -> Sequence[Message]:
            if self._runtime.llm is None:
                raise LLMNotSet()
            return await self._compaction.compact(
                self._context.history, self._runtime.llm
            )

        compacted_messages = await _compact_with_retry()
        await self._context.revert_to(0)
        await self._checkpoint()
        await self._context.append_message(compacted_messages)

    @staticmethod
    def _is_retryable_error(exception: BaseException) -> bool:
        if isinstance(exception, (APIConnectionError, APITimeoutError, APIEmptyResponseError)):
            return True
        # 服务端/网关/限流/超时 等常见可恢复状态码：重试
        if isinstance(exception, APIStatusError):
            return exception.status_code in (
                408,  # Request Timeout
                404,  # Not Found
                429,  # Too Many Requests (rate limit)
                500,  # Internal Server Error
                502,  # Bad Gateway
                503,  # Service Unavailable
                504,  # Gateway Timeout
                520,  # Unknown Error (CDN)
                521,  # Web Server Is Down
                522,  # Connection Timed Out
                523,  # Origin Is Unreachable
                524,  # A Timeout Occurred
                525,  # SSL Handshake Failed
                526,  # Invalid SSL Certificate
                527,  # Railgun Error
            )
        if isinstance(exception,(httpx.HTTPError,httpcore.ReadError)):
            return True
        return False

    @staticmethod
    def _retry_log(name: str, retry_state: RetryCallState):
        logger.info(
            "Retrying {name} for the {n} time. Waiting {sleep} seconds.",
            name=name,
            n=retry_state.attempt_number,
            sleep=(
                retry_state.next_action.sleep
                if retry_state.next_action is not None
                else "unknown"
            ),
        )


class BackToTheFuture(Exception):
    """
    Raise when we need to revert the context to a previous checkpoint.
    The main agent loop should catch this exception and handle it.
    """

    def __init__(self, checkpoint_id: int, messages: Sequence[Message]):
        self.checkpoint_id = checkpoint_id
        self.messages = messages
