"""狼人群用 SelectorGroupChat，白天发言用 RoundRobin。

单人行动（守卫、女巫、预言家、猎人、投票、遗言）不进这里。
那些调用直接走 AssistantAgent，避免一个人的 GroupChat。
"""

from __future__ import annotations

from typing import Sequence

from autogen_agentchat.base import ChatAgent, TerminationCondition, TerminatedException
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, StopMessage, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
from autogen_core.models import ChatCompletionClient

from werewolf.parse import parse_action


class SeatOrder:
    """按座位轮流点名。pass 只表示这轮没内容，不把人移出下一轮。"""

    def __init__(self, order: list[str]) -> None:
        self._order = list(order)
        self._index = 0

    def __call__(self, messages: Sequence[BaseAgentEvent | BaseChatMessage]) -> str:
        del messages
        speaker = self._order[self._index % len(self._order)]
        self._index += 1
        return speaker


class WolfConsensusTermination(TerminationCondition):
    """全体最新行动都是同一个刀口，或全体 pass，就结束。否则继续到最大轮数。"""

    def __init__(self, wolf_ids: list[str]) -> None:
        self._wolf_ids = list(wolf_ids)
        self._seen: list[TextMessage] = []
        self._terminated = False

    @property
    def terminated(self) -> bool:
        return self._terminated

    async def reset(self) -> None:
        self._seen.clear()
        self._terminated = False

    async def __call__(self, messages: Sequence[BaseAgentEvent | BaseChatMessage]) -> StopMessage | None:
        if self._terminated:
            raise TerminatedException("狼队终止条件已经触发")
        for message in messages:
            if isinstance(message, TextMessage) and message.source in self._wolf_ids:
                self._seen.append(message)
        latest: dict[str, str] = {}
        for message in self._seen:
            action = parse_action(message.content)
            if action is None:
                continue
            if action.passed:
                latest[message.source] = "pass"
            elif action.kill:
                latest[message.source] = action.kill
        if set(latest) != set(self._wolf_ids):
            return None
        values = list(latest.values())
        if all(value == "pass" for value in values):
            self._terminated = True
            return StopMessage(content="全体狼人选择沉默，空刀。", source="WolfConsensus")
        kills = [value for value in values if value != "pass"]
        if len(kills) == len(self._wolf_ids) and len(set(kills)) == 1:
            self._terminated = True
            return StopMessage(content=f"狼人就刀口 {kills[0]} 达成一致。", source="WolfConsensus")
        return None


def open_public_talk(participants: list[ChatAgent], max_turns: int) -> RoundRobinGroupChat:
    if not participants:
        raise ValueError("公聊没有参与者")
    return RoundRobinGroupChat(
        participants,
        name="public-talk",
        description="daytime public speeches",
        max_turns=max_turns,
    )


def open_wolf_meeting(
    participants: list[ChatAgent],
    model_client: ChatCompletionClient,
    *,
    max_turns: int,
) -> SelectorGroupChat:
    if not participants:
        raise ValueError("狼人群没有参与者")
    order = [agent.name for agent in participants]
    return SelectorGroupChat(
        participants,
        model_client=model_client,
        name="wolf-meeting",
        description="werewolf night meeting",
        termination_condition=WolfConsensusTermination(order),
        max_turns=max_turns,
        allow_repeated_speaker=True,
        selector_func=SeatOrder(order),
    )
