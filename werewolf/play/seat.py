"""座位代理。发出去的话先过滤破防和超长，再进入 GroupChat。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from autogen_agentchat.agents import AssistantAgent, BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_core.model_context import ChatCompletionContext

from werewolf.game.actions import sanitize_speech


class SeatAgent(BaseChatAgent):
    def __init__(self, inner: AssistantAgent, max_speech_chars: int) -> None:
        super().__init__(name=inner.name, description=inner.description)
        self._inner = inner
        self._max_speech_chars = max_speech_chars

    @property
    def model_context(self) -> ChatCompletionContext:
        return self._inner.model_context

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return [TextMessage]

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        response = await self._inner.on_messages(messages, cancellation_token)
        original = response.chat_message.content
        text = original if isinstance(original, str) else str(original)
        cleaned, _filtered = sanitize_speech(text, self._max_speech_chars)
        return Response(chat_message=TextMessage(content=cleaned, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        await self._inner.on_reset(cancellation_token)

    async def save_state(self) -> Mapping[str, Any]:
        return await self._inner.save_state()

    async def load_state(self, state: Mapping[str, Any]) -> None:
        await self._inner.load_state(state)
