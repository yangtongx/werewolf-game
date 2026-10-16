"""模型客户端。

真实对局走 OpenAI 兼容接口。--mock 使用脚本客户端，方便没有密钥时
把整局频道切换跑完，并在狼人群留下不会出现在公聊里的口令。
"""

from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator, Literal, Mapping, Optional, Sequence, Union

from autogen_core import CancellationToken
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelFamily,
    ModelInfo,
    RequestUsage,
    SystemMessage,
)
from autogen_core.tools import Tool, ToolSchema
from pydantic import BaseModel

from werewolf.parse import canonicalize, parse_id_list
from werewolf.prompts import (
    CHANNEL_GUARD,
    CHANNEL_HUNTER,
    CHANNEL_PUBLIC,
    CHANNEL_SEER,
    CHANNEL_WITCH,
    CHANNEL_WOLF,
)

_MOCK_INFO: ModelInfo = {
    "vision": False,
    "function_calling": False,
    "json_output": False,
    "family": ModelFamily.UNKNOWN,
    "structured_output": False,
}


def _text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _latest_task(messages: Sequence[LLMMessage]) -> str:
    found = ""
    for message in messages:
        text = _text(message).strip()
        if text.startswith("【频道："):
            found = text
    return found


def _last_value(blob: str, prefix: str) -> str | None:
    found: str | None = None
    for line in blob.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            found = stripped[len(prefix) :].strip()
    return found


def _lowest(player_ids: list[str], excluded: set[str]) -> str | None:
    choices = [player_id for player_id in player_ids if player_id not in excluded]
    if not choices:
        return None
    return min(choices, key=lambda player_id: int(player_id[1:]))


class ScriptedWerewolfClient(ChatCompletionClient):
    """按频道标记做确定性发言，用来演示和测试隔离，不调用网络。"""

    def __init__(self) -> None:
        self._usage = RequestUsage(prompt_tokens=0, completion_tokens=0)

    async def create(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: Optional[bool | type[BaseModel]] = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Optional[CancellationToken] = None,
    ) -> CreateResult:
        del tools, tool_choice, json_output, extra_create_args, cancellation_token
        content = self._reply(messages)
        self._usage = RequestUsage(prompt_tokens=1, completion_tokens=1)
        return CreateResult(finish_reason="stop", content=content, usage=self._usage, cached=False)

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: Optional[bool | type[BaseModel]] = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Optional[CancellationToken] = None,
    ) -> AsyncGenerator[Union[str, CreateResult], None]:
        yield await self.create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )

    async def close(self) -> None:
        return None

    def actual_usage(self) -> RequestUsage:
        return self._usage

    def total_usage(self) -> RequestUsage:
        return self._usage

    def count_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        del messages, tools
        return 1

    def remaining_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        del messages, tools
        return 100_000

    @property
    def capabilities(self) -> ModelCapabilities:
        return _MOCK_INFO

    @property
    def model_info(self) -> ModelInfo:
        return _MOCK_INFO

    def _reply(self, messages: Sequence[LLMMessage]) -> str:
        system = "\n".join(_text(message) for message in messages if isinstance(message, SystemMessage))
        history = "\n".join(_text(message) for message in messages)
        # 只把「当前这轮上帝任务」当频道。旧夜的查验结论还在记忆里，但不能把新的一夜误当成回复环节。
        task = _latest_task(messages)
        seat = _last_value(system, "你的座位:") or ""
        role = _last_value(system, "你的身份:") or ""
        channel = ""
        matched = re.search(r"【频道：([^】]+)】", task)
        if matched:
            channel = matched.group(1)
        stage = _last_value(task, "环节:") or ""
        living = parse_id_list(_last_value(task, "在场:") or "")
        mates = parse_id_list(_last_value(system, "狼队友:") or "")

        if channel == CHANNEL_WOLF:
            excluded = {seat, *mates}
            target = _lowest(living, excluded)
            if target is None:
                return "[ACTION] kill=none"
            return (
                f"今晚就定这个目标。\n"
                f"（狼队密谈）刀口={target} 发言人={seat}\n"
                f"[ACTION] kill={target}"
            )
        if channel == CHANNEL_GUARD:
            last = canonicalize(_last_value(task, "昨夜守卫:") or "")
            target = _lowest(living, {seat, last} if last else {seat})
            if target is None:
                return "[ACTION] guard=none"
            return f"我守这一位。\n[ACTION] guard={target}"
        if channel == CHANNEL_WITCH:
            knife = canonicalize(_last_value(task, "刀口:") or "")
            antidote = _last_value(task, "解药:") == "有"
            poison_left = _last_value(task, "毒药:") == "有"
            if antidote and knife:
                return "[ACTION] save=yes poison=none"
            if poison_left:
                target = _lowest(living, {seat})
                if target is not None:
                    return f"[ACTION] save=no poison={target}"
            return "[ACTION] save=no poison=none"
        if channel == CHANNEL_SEER:
            if "查验结果:" in task:
                return "收到。"
            checked = set(re.findall(r"(p\d+)=", _last_value(task, "已查验:") or ""))
            target = _lowest(living, {seat, *checked})
            if target is None:
                return "[ACTION] check=none"
            return f"我查这一位。\n[ACTION] check={target}"
        if "你不能开枪" in task:
            return "[ACTION] pass"
        if channel == CHANNEL_HUNTER:
            target = _lowest(living, set())
            if target is None:
                return "[ACTION] shoot=none"
            return f"[ACTION] shoot={target}"
        if stage == "遗言":
            return "我没有要补充的。"
        if stage == "投票" or channel == "投票":
            known = re.findall(r"查验结果:\s*(p\d+)\s*是狼人", history)
            target = next((player_id for player_id in known if player_id in living), None)
            if role == RoleWerewolf and target is None:
                target = _lowest(living, {seat, *mates})
            if target is None:
                target = _lowest(living, {seat})
            if target is None:
                return "[ACTION] vote=abstain"
            return f"我投票。\n[ACTION] vote={target}"
        if channel == CHANNEL_PUBLIC:
            return f"我是{seat}，这轮先听发言，暂时不站边。"
        return "收到。"


# 避免在回复函数里再导入枚举。
RoleWerewolf = "werewolf"


def build_model_client(*, mock: bool, model: str, api_key: str | None, base_url: str | None) -> ChatCompletionClient:
    if mock:
        return ScriptedWerewolfClient()
    if not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY。没有密钥时请加 --mock，先看频道隔离的完整对局。")
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
        # 多数兼容接口不接受 OpenAI 的 name 字段，把发言人写进正文。
        kwargs["include_name_in_message"] = False
        kwargs["add_name_prefixes"] = True
    try:
        return OpenAIChatCompletionClient(**kwargs)
    except ValueError as exc:
        if "model_info" not in str(exc):
            raise
        kwargs["model_info"] = {
            "vision": False,
            "function_calling": False,
            "json_output": False,
            "family": "unknown",
            "structured_output": False,
        }
        return OpenAIChatCompletionClient(**kwargs)


class TracingClient(ChatCompletionClient):
    """按环节把发言和决策分到两个模型，并记下每次调用。"""

    def __init__(
        self,
        speech: ChatCompletionClient,
        decision: ChatCompletionClient,
        sink: list[dict[str, object]],
    ) -> None:
        self._speech = speech
        self._decision = decision
        self._sink = sink

    def _pick(self, messages: Sequence[LLMMessage]) -> tuple[str, ChatCompletionClient]:
        task = _latest_task(messages)
        stage = _last_value(task, "环节:") or ""
        if stage == "发言":
            return "speech", self._speech
        return "decision", self._decision

    async def create(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: Optional[bool | type[BaseModel]] = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Optional[CancellationToken] = None,
    ) -> CreateResult:
        route, client = self._pick(messages)
        started = time.perf_counter()
        result = await client.create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )
        self._sink.append(_call_record(route, messages, result, started))
        return result

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Tool | Literal["auto", "required", "none"] = "auto",
        json_output: Optional[bool | type[BaseModel]] = None,
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: Optional[CancellationToken] = None,
    ) -> AsyncGenerator[Union[str, CreateResult], None]:
        result = await self.create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )
        yield result

    async def close(self) -> None:
        await self._speech.close()
        if self._decision is not self._speech:
            await self._decision.close()

    def actual_usage(self) -> RequestUsage:
        return self._decision.actual_usage()

    def total_usage(self) -> RequestUsage:
        return self._decision.total_usage()

    def count_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        return self._decision.count_tokens(messages, tools=tools)

    def remaining_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        return self._decision.remaining_tokens(messages, tools=tools)

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._decision.capabilities

    @property
    def model_info(self) -> ModelInfo:
        return self._decision.model_info


def build_traced_client(
    *,
    mock: bool,
    model: str,
    speech_model: str,
    decision_model: str,
    api_key: str | None,
    base_url: str | None,
    sink: list[dict[str, object]],
) -> TracingClient:
    speech_name = speech_model or model
    speech = build_model_client(mock=mock, model=speech_name, api_key=api_key, base_url=base_url)
    if mock or not decision_model or decision_model == speech_name:
        decision = speech
    else:
        decision = build_model_client(mock=False, model=decision_model, api_key=api_key, base_url=base_url)
    return TracingClient(speech, decision, sink)


def _call_record(
    route: str,
    messages: Sequence[LLMMessage],
    result: CreateResult,
    started: float,
) -> dict[str, object]:
    content = result.content if isinstance(result.content, str) else str(result.content)
    return {
        "route": route,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "prompt": [{"type": getattr(message, "type", ""), "content": _text(message)} for message in messages],
        "response": content,
    }
