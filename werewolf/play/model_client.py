"""模型客户端。

真实对局走 OpenAI 兼容接口。--mock 使用脚本客户端，方便没有密钥时
把整局频道切换跑完，并在狼人群留下不会出现在公聊里的口令。
"""

from __future__ import annotations

import os
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

from werewolf.game.parse import canonicalize, parse_id_list
from werewolf.play.vendors import Vendor
from werewolf.play.prompts import (
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
            target = _knife_target(seat, mates, living, messages)
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
            return _public_line(seat, role, mates, living, history, task, messages)
        return "收到。"


# 避免在回复函数里再导入枚举。
RoleWerewolf = "werewolf"


def _public_line(
    seat: str,
    role: str,
    mates: list[str],
    living: list[str],
    history: str,
    task: str,
    messages: Sequence[LLMMessage],
) -> str:
    """演练房的白天发言。狼人看已经听到的话再决定跳、藏或质疑。"""
    del task
    target = _point_at(living, {seat, *mates}, seat)
    if role == RoleWerewolf:
        return _wolf_public_line(seat, mates, target, messages)
    if role == "seer":
        known = re.findall(r"查验结果:\s*(p\d+)\s*是(狼人|好人)", history)
        if known:
            player_id, side = known[-1]
            return f"我是真预言家。{player_id}是{side}。谁再跳预言家，今天就对跳。"
        return f"我是预言家，先不报验人。{target}的发言我听着像在抢身份。"
    if role == "witch":
        return f"药水我自己知道。{target}今天的话在带风向，我怀疑他是狼。"
    if role == "guard":
        return f"我守过，对象不说。{target}发言太顺，我这轮盯他。"
    if role == "hunter":
        return f"枪还在，身份先不亮。{target}要么是狼，要么在替狼开路。"
    return f"我没有技能。{target}这轮要么是狼，要么在悍跳，我跟怀疑他的人。"


def _wolf_public_line(seat: str, mates: list[str], target: str, messages: Sequence[LLMMessage]) -> str:
    own = "\n".join(text for source, text in _spoken(messages) if source == seat)
    if "我跳预言家" in own or "我还是预言家" in own:
        return f"我还是预言家。{target}的票型不对，我今天继续押他。"
    if "我跳守卫" in own or "守卫还在" in own:
        return f"守卫还在。{target}昨天的发言对不上，我今天投他。"
    if "我跳女巫" in own or "我还是女巫" in own:
        return f"我还是女巫。{target}今天的话我听着假，我怀疑他。"
    heard = [(source, text) for source, text in _spoken(messages) if source != seat]
    seer_from_mate = any(source in mates and _claims_seer(text) for source, text in heard)
    seer_from_outside = any(source not in mates and _claims_seer(text) for source, text in heard)
    if seer_from_mate:
        return f"已经有人跳预言家了。我先不亮身份，{target}的发言对不上，我怀疑他。"
    if seer_from_outside:
        return f"我跳预言家。昨晚验了{target}，是狼人。今天跟他对跳。"
    pushed = any(seat in text and "怀疑" in text for _source, text in heard)
    if pushed:
        return f"我跳守卫。守护对象不公开。{target}在带节奏，我怀疑他。"
    return f"我先不亮身份。{target}的发言对不上，我怀疑他。"


def _knife_target(seat: str, mates: list[str], living: list[str], messages: Sequence[LLMMessage]) -> str | None:
    """同伴被当众指成狼人才自刀。否则刀座位号最小的好人。狼队用同一条规则，刀口才会一致。"""
    accused = [
        mate
        for mate in (*mates, seat)
        if mate in living and any(re.search(rf"{mate}是狼人", text) for _source, text in _spoken(messages))
    ]
    if accused:
        return min(set(accused), key=lambda player_id: int(player_id[1:]))
    return _lowest(living, {seat, *mates})


def _spoken(messages: Sequence[LLMMessage]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        text = _text(message).strip()
        if not text or text.startswith("【频道："):
            continue
        rows.append((getattr(message, "source", "") or "", text))
    return rows


def _claims_seer(text: str) -> bool:
    return "跳预言家" in text or "我是真预言家" in text or "我是预言家" in text


def _point_at(living: list[str], excluded: set[str], seat: str) -> str:
    pool = [player_id for player_id in living if player_id not in excluded]
    if not pool:
        pool = list(living) or ["p1"]
    shift = int(seat[1:]) if len(seat) > 1 and seat[1:].isdigit() else 0
    return pool[shift % len(pool)]


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
    """包住一个座位的厂家模型，并记下每次调用。"""

    def __init__(
        self,
        inner: ChatCompletionClient,
        sink: list[dict[str, object]],
        *,
        vendor_id: str,
        model_name: str,
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._vendor_id = vendor_id
        self._model_name = model_name

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
        started = time.perf_counter()
        result = await self._inner.create(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            json_output=json_output,
            extra_create_args=extra_create_args,
            cancellation_token=cancellation_token,
        )
        record = _call_record(self._vendor_id, messages, result, started)
        record["model"] = self._model_name
        self._sink.append(record)
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
        await self._inner.close()

    def actual_usage(self) -> RequestUsage:
        return self._inner.actual_usage()

    def total_usage(self) -> RequestUsage:
        return self._inner.total_usage()

    def count_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        return self._inner.count_tokens(messages, tools=tools)

    def remaining_tokens(self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []) -> int:
        return self._inner.remaining_tokens(messages, tools=tools)

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._inner.capabilities

    @property
    def model_info(self) -> ModelInfo:
        return self._inner.model_info


async def build_seat_clients(
    assignment: dict[str, Vendor],
    *,
    mock: bool,
    sink: list[dict[str, object]],
    keys: dict[str, str] | None = None,
) -> tuple[dict[str, TracingClient], list[ChatCompletionClient]]:
    """每个座位一个厂家。mock 时共用一个脚本客户端，只记下分配到的厂家。

    keys 不为空时只认这份内存里的密钥，不读环境变量。
    """
    missing = [
        f"{player_id} 的{vendor.label}"
        for player_id, vendor in assignment.items()
        if not mock and not _seat_key(player_id, vendor, keys)
    ]
    if missing:
        names = "、".join(dict.fromkeys(missing))
        raise RuntimeError(f"本局用到的厂家还没有密钥：{names}")
    owned: list[ChatCompletionClient] = []
    shared = ScriptedWerewolfClient() if mock else None
    if shared is not None:
        owned.append(shared)
    clients: dict[str, TracingClient] = {}
    try:
        for player_id, vendor in assignment.items():
            if shared is not None:
                inner: ChatCompletionClient = shared
            else:
                inner = build_model_client(
                    mock=False,
                    model=vendor.model,
                    api_key=_seat_key(player_id, vendor, keys),
                    base_url=vendor.base_url or None,
                )
                owned.append(inner)
            clients[player_id] = TracingClient(
                inner,
                sink,
                vendor_id=vendor.id,
                model_name=vendor.model,
            )
    except Exception:
        await close_clients(owned)
        raise
    return clients, owned


def _seat_key(player_id: str, vendor: Vendor, keys: dict[str, str] | None) -> str:
    """keys 按座位给。没有这份字典时，才回退去读该厂家的环境变量。"""
    if keys is not None:
        return (keys.get(player_id) or "").strip()
    return (os.environ.get(vendor.api_key_env) or "").strip()


async def close_clients(clients: list[ChatCompletionClient]) -> None:
    seen: set[int] = set()
    for client in clients:
        if id(client) in seen:
            continue
        seen.add(id(client))
        await client.close()


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
