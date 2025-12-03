import asyncio
from typing import TYPE_CHECKING, Literal, NamedTuple

import aiohttp
from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from pydantic import SecretStr

from kimi_cli.config import (
    LLMModel,
    LLMProvider,
    MoonshotSearchConfig,
    load_config,
    save_config,
)
from kimi_cli.ui.shell.console import console
from kimi_cli.ui.shell.metacmd import meta_command
from kimi_cli.utils.aiohttp import new_client_session

if TYPE_CHECKING:
    from kimi_cli.ui.shell import ShellApp


class _Platform(NamedTuple):
    id: str
    name: str
    base_url: str
    provider_type: Literal[
        "kimi",
        "openai_legacy",
        "openai_responses",
        "anthropic",
        "_chaos",
    ] = "kimi"
    search_url: str | None = None
    allowed_prefixes: list[str] | None = None


_PLATFORMS = [
    _Platform(
        id="kimi-for-coding",
        name="Kimi For Coding",
        base_url="https://api.kimi.com/coding/v1",
        search_url="https://api.kimi.com/coding/v1/search",
    ),
    _Platform(
        id="moonshot-cn",
        name="Moonshot AI 开放平台 (moonshot.cn)",
        base_url="https://api.moonshot.cn/v1",
        allowed_prefixes=["kimi-k2-"],
    ),
    _Platform(
        id="moonshot-ai",
        name="Moonshot AI Open Platform (moonshot.ai)",
        base_url="https://api.moonshot.ai/v1",
        allowed_prefixes=["kimi-k2-"],
    ),
    _Platform(
        id="Custom",
        name="Custom API",
        base_url="",
    ),
]


@meta_command
async def setup(app: "ShellApp", args: list[str]):
    """Setup Kimi CLI"""
    result = await _setup()
    if not result:
        # error message already printed
        return

    config = load_config()
    config.providers[result.platform.id] = LLMProvider(
        type=result.platform.provider_type,
        base_url=result.platform.base_url,
        api_key=result.api_key,
    )
    config.models[result.model_id] = LLMModel(
        provider=result.platform.id,
        model=result.model_id,
        max_context_size=result.max_context_size,
    )
    config.default_model = result.model_id

    if result.platform.search_url:
        config.services.moonshot_search = MoonshotSearchConfig(
            base_url=result.platform.search_url,
            api_key=result.api_key,
        )

    save_config(config)
    console.print("[green]✓[/green] Kimi CLI has been setup! Reloading...")
    await asyncio.sleep(1)
    console.clear()

    from kimi_cli.exception import Reload

    raise Reload


class _SetupResult(NamedTuple):
    platform: _Platform
    api_key: SecretStr
    model_id: str
    max_context_size: int


async def _setup() -> _SetupResult | None:
    # select the API platform
    platform_name = await _prompt_choice(
        header="Select the API platform",
        choices=[platform.name for platform in _PLATFORMS],
    )
    if not platform_name:
        console.print("[red]No platform selected[/red]")
        return None

    platform = next(
        platform for platform in _PLATFORMS if platform.name == platform_name
    )

    if platform.id == "Custom":
        # Select provider type
        provider_types: list[
            tuple[
                Literal[
                    "kimi",
                    "openai_legacy",
                    "openai_responses",
                    "anthropic",
                    "_chaos",
                ],
                str,
            ]
        ] = [
            ("openai_legacy", "OpenAI Legacy"),
            ("openai_responses", "OpenAI Responses"),
            ("kimi", "Kimi"),
            ("anthropic", "Anthropic"),
        ]
        provider_type_display = await _prompt_choice(
            header="Select provider type",
            choices=[display for _, display in provider_types],
        )
        if not provider_type_display:
            console.print("[red]No provider type selected[/red]")
            return None

        provider_type = next(
            ptype
            for ptype, display in provider_types
            if display == provider_type_display
        )
        platform = platform._replace(provider_type=provider_type)

        # Enter base URL
        base_url = await _prompt_text("Enter your base URL", is_password=False)
        if not base_url:
            return None
        platform = platform._replace(base_url=base_url)

    # enter the API key
    api_key = await _prompt_text("Enter your API key", is_password=True)
    if not api_key:
        return None

    # list models
    models_url = f"{platform.base_url}/models"
    try:
        async with (
            new_client_session() as session,
            session.get(
                models_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                raise_for_status=True,
            ) as response,
        ):
            resp_json = await response.json()
    except aiohttp.ClientError as e:
        console.print(f"[red]Failed to get models: {e}[/red]")
        return None

    model_dict = {model["id"]: model for model in resp_json["data"]}

    # select the model
    model_ids: list[str] = [model["id"] for model in resp_json["data"]]
    if platform.allowed_prefixes is not None:
        model_ids = [
            model_id
            for model_id in model_ids
            if model_id.startswith(tuple(platform.allowed_prefixes))
        ]

    if not model_ids:
        console.print("[red]No models available for the selected platform[/red]")
        return None

    model_id = await _prompt_choice(
        header="Select the model",
        choices=model_ids,
    )
    if not model_id:
        console.print("[red]No model selected[/red]")
        return None

    model = model_dict[model_id]

    # Get context length from model data
    # OpenAI compatible APIs may not have this field, use default 128000
    # Note: This is the total context window size (input + output), not max_tokens per request
    max_context_size = model.get("context_length", 128000)

    return _SetupResult(
        platform=platform,
        api_key=SecretStr(api_key),
        model_id=model_id,
        max_context_size=max_context_size,
    )


async def _prompt_choice(*, header: str, choices: list[str]) -> str | None:
    if not choices:
        return None

    try:
        return await ChoiceInput(
            message=header,
            options=[(choice, choice) for choice in choices],
            default=choices[0],
        ).prompt_async()
    except (EOFError, KeyboardInterrupt):
        return None


async def _prompt_text(prompt: str, *, is_password: bool = False) -> str | None:
    session = PromptSession()
    try:
        return str(
            await session.prompt_async(
                f" {prompt}: ",
                is_password=is_password,
            )
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None


@meta_command
def reload(app: "ShellApp", args: list[str]):
    """Reload configuration"""
    from kimi_cli.exception import Reload

    raise Reload
