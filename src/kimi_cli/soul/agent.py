import importlib
import inspect
import string
from pathlib import Path
from typing import Any, NamedTuple

from kosong.tooling import CallableTool, CallableTool2, Toolset

from kimi_cli.agentspec import ResolvedAgentSpec, load_agent_spec
from kimi_cli.config import Config
from kimi_cli.session import Session
from kimi_cli.soul.approval import Approval
from kimi_cli.soul.denwarenji import DenwaRenji
from kimi_cli.soul.runtime import BuiltinSystemPromptArgs, Runtime
from kimi_cli.soul.toolset import CustomToolset
from kimi_cli.tools import SkipThisTool
from kimi_cli.utils.logging import logger


class Agent(NamedTuple):
    """The loaded agent."""

    name: str
    system_prompt: str
    toolset: Toolset


async def load_agent(
    agent_file: Path,
    runtime: Runtime,
    *,
    mcp_configs: list[dict[str, Any]],
) -> Agent:
    """
    Load agent from specification file.

    Raises:
        FileNotFoundError: If the agent spec file does not exist.
        AgentSpecError: If the agent spec is not valid.
    """
    logger.info("Loading agent: {agent_file}", agent_file=agent_file)
    agent_spec = load_agent_spec(agent_file)

    system_prompt = _load_system_prompt(
        agent_spec.system_prompt_path,
        agent_spec.system_prompt_args,
        runtime.builtin_args,
    )

    tool_deps = {
        ResolvedAgentSpec: agent_spec,
        Runtime: runtime,
        Config: runtime.config,
        BuiltinSystemPromptArgs: runtime.builtin_args,
        Session: runtime.session,
        DenwaRenji: runtime.denwa_renji,
        Approval: runtime.approval,
    }
    tools = agent_spec.tools
    combined_exclude_tools = set(agent_spec.exclude_tools or [])
    if runtime.global_exclude_tools:  # 从 runtime 获取全局排除工具
        combined_exclude_tools.update(runtime.global_exclude_tools)
        logger.debug("Global exclude tools: {tools}", tools=runtime.global_exclude_tools)
    
    if combined_exclude_tools:
        logger.debug("Combined excluding tools: {tools}", tools=list(combined_exclude_tools))
        # 过滤内置工具：按工具路径的最后部分（类名）进行匹配
        tools = [
            tool for tool in tools 
            if not any(
                excluded_tool.lower() in tool.lower() or 
                tool.split(":")[-1].lower() == excluded_tool.lower()
                for excluded_tool in combined_exclude_tools
            )
        ]
    
    toolset = CustomToolset()
    bad_tools = _load_tools(toolset, tools, tool_deps)
    if bad_tools:
        raise ValueError(f"Invalid tools: {bad_tools}")

    assert isinstance(toolset, CustomToolset)
    if mcp_configs:
        await _load_mcp_tools(
            toolset, 
            mcp_configs, 
            exclude_tools=combined_exclude_tools  # 传递合并后的排除列表
        )

    return Agent(
        name=agent_spec.name,
        system_prompt=system_prompt,
        toolset=toolset,
    )


def _load_system_prompt(
    path: Path, args: dict[str, str], builtin_args: BuiltinSystemPromptArgs
) -> str:
    logger.info("Loading system prompt: {path}", path=path)
    system_prompt = path.read_text(encoding="utf-8").strip()
    logger.debug(
        "Substituting system prompt with builtin args: {builtin_args}, spec args: {spec_args}",
        builtin_args=builtin_args,
        spec_args=args,
    )
    return string.Template(system_prompt).substitute(builtin_args._asdict(), **args)


type ToolType = CallableTool | CallableTool2[Any]
# TODO: move this to kosong.tooling.simple


def _load_tools(
    toolset: CustomToolset,
    tool_paths: list[str],
    dependencies: dict[type[Any], Any],
) -> list[str]:
    bad_tools: list[str] = []
    for tool_path in tool_paths:
        try:
            tool = _load_tool(tool_path, dependencies)
        except SkipThisTool:
            logger.info("Skipping tool: {tool_path}", tool_path=tool_path)
            continue
        if tool:
            toolset += tool
        else:
            bad_tools.append(tool_path)
    logger.info("Loaded tools: {tools}", tools=[tool.name for tool in toolset.tools])
    if bad_tools:
        logger.error("Bad tools: {bad_tools}", bad_tools=bad_tools)
    return bad_tools


def _load_tool(tool_path: str, dependencies: dict[type[Any], Any]) -> ToolType | None:
    logger.debug("Loading tool: {tool_path}", tool_path=tool_path)
    module_name, class_name = tool_path.rsplit(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    cls = getattr(module, class_name, None)
    if cls is None:
        return None
    args: list[type[Any]] = []
    for param in inspect.signature(cls).parameters.values():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            # once we encounter a keyword-only parameter, we stop injecting dependencies
            break
        # all positional parameters should be dependencies to be injected
        if param.annotation not in dependencies:
            raise ValueError(f"Tool dependency not found: {param.annotation}")
        args.append(dependencies[param.annotation])
    return cls(*args)


async def _load_mcp_tools(
    toolset: CustomToolset,
    mcp_configs: list[dict[str, Any]],
    *,
    exclude_tools: set[str],
):
    """
    尝试加载 MCP 工具。如果连接失败，打印警告并继续。
    
    不再抛出异常，而是记录警告日志。
    """
    import fastmcp

    from kimi_cli.tools.mcp import MCPTool

    for mcp_config in mcp_configs:
        logger.info("Loading MCP tools from: {mcp_config}", mcp_config=mcp_config)
        try:
            client = fastmcp.Client(mcp_config)
            async with client:
                for tool in await client.list_tools():
                    # honor exclude_tools for MCP tools by tool name
                    if tool.name in exclude_tools:
                        logger.info("Excluding MCP tool: {tool}", tool=tool.name)
                        continue
                    toolset += MCPTool(tool, client)
                logger.info("Successfully loaded MCP tools from: {mcp_config}", mcp_config=mcp_config)
        except Exception as e:
            print(
                f"⚠️  Failed to connect to MCP server: {mcp_config}. Error: {e}. Continuing without MCP tools from this server."
            )
            # 继续处理下一个 MCP 配置，不抛出异常
            continue
    return toolset
