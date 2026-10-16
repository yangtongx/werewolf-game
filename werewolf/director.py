"""上帝调度器。

单人行动直接调用座位代理。狼人群是 SelectorGroupChat，可以 pass，
刀口一致或到最大轮数就停。白天发言是 RoundRobin。投票是每人单独交票。
宣判由状态机写成公开事件，不再开一间聊天室。

上一夜的狼队全文只在下一夜的狼人群开场里出现。同一轮里后发言的内容
不会立刻回灌给已经说过话的人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core.models import ChatCompletionClient, UserMessage

from werewolf.actions import ActionRecord, resolve_target
from werewolf.channels import open_public_talk, open_wolf_meeting
from werewolf.machine import Death, GameMachine, Role, role_label, seat_key
from werewolf.model_client import build_traced_client
from werewolf.parse import parse_action
from werewolf.prompts import (
    assert_public,
    guard_task,
    hunter_blocked_task,
    hunter_task,
    last_words_task,
    public_talk_task,
    render_morning,
    seer_result_task,
    seer_task,
    solo_vote_task,
    system_prompt,
    witch_task,
    wolf_task,
)
from werewolf.rules import Rules
from werewolf.seat import SeatAgent

Printer = Callable[[str], None]


@dataclass
class SessionLog:
    channel: str
    banner: str
    participants: list[str]
    messages: list[dict[str, str]]
    kind: str


@dataclass
class GameResult:
    winner: str
    preset: str | None
    seed: int | None
    roles: dict[str, str]
    rules: dict[str, object]
    sessions: list[SessionLog] = field(default_factory=list)
    observer: list[str] = field(default_factory=list)
    transitions: list[dict[str, object]] = field(default_factory=list)
    actions: list[dict[str, object]] = field(default_factory=list)
    llm_calls: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "preset": self.preset,
            "seed": self.seed,
            "roles": self.roles,
            "rules": self.rules,
            "observer": self.observer,
            "transitions": self.transitions,
            "actions": self.actions,
            "llm_calls": self.llm_calls,
            "sessions": [
                {
                    "channel": session.channel,
                    "banner": session.banner,
                    "kind": session.kind,
                    "participants": session.participants,
                    "messages": session.messages,
                }
                for session in self.sessions
            ],
        }


def build_players(
    machine: GameMachine,
    model_client: ChatCompletionClient,
) -> tuple[dict[str, SeatAgent], dict[str, str]]:
    wolf_ids = [player.id for player in machine.wolves(alive_only=False)]
    agents: dict[str, SeatAgent] = {}
    prompts: dict[str, str] = {}
    for player in machine.players:
        teammates = [wolf_id for wolf_id in wolf_ids if wolf_id != player.id] if player.is_wolf else []
        prompt = system_prompt(player, teammates, machine.rules.max_speech_chars)
        prompts[player.id] = prompt
        inner = AssistantAgent(
            name=player.id,
            model_client=model_client,
            system_message=prompt,
            description=f"{player.label} 的座位",
        )
        agents[player.id] = SeatAgent(inner, machine.rules.max_speech_chars)
    return agents, prompts


class GodDirector:
    def __init__(
        self,
        machine: GameMachine,
        agents: dict[str, SeatAgent],
        prompts: dict[str, str],
        model_client: ChatCompletionClient,
        *,
        talk_rounds: int = 1,
        wolf_rounds: int = 2,
        max_nights: int = 12,
        printer: Printer = print,
        llm_calls: list[dict[str, object]] | None = None,
    ) -> None:
        if talk_rounds < 1 or wolf_rounds < 1:
            raise ValueError("发言轮数至少为 1")
        self.machine = machine
        self.agents = agents
        self.prompts = prompts
        self.model_client = model_client
        self.talk_rounds = talk_rounds
        self.wolf_rounds = wolf_rounds
        self.max_nights = max_nights
        self.printer = printer
        self.sessions: list[SessionLog] = []
        self.observer: list[str] = []
        self.action_logs: list[ActionRecord] = []
        self.llm_calls = llm_calls if llm_calls is not None else []
        self._wolf_archive = ""

    async def play(self) -> GameResult:
        self._observer(self.machine.roster_text())
        self._observer("本局规则：" + str(self.machine.rules.to_dict()))
        self.machine.update_winner()
        while self.machine.winner is None and self.machine.night_number < self.max_nights:
            await self._night()
            if self.machine.winner is not None:
                self._observer("夜晚结束后胜负已定，不再进入白天。\n" + self.machine.dawn_text)
                break
            await self._day()
        if self.machine.winner is None:
            self.machine.winner = "draw"
            self._observer("到达夜数上限，本局记为平局。")
        if self.machine.public_queue:
            self._observer("未再宣读的公开结算：\n" + "\n".join(self.machine.public_queue))
        self._observer(self._summary())
        return self._result()

    async def _night(self) -> None:
        night = self.machine.begin_night()
        await self._guard_phase(night)
        await self._wolf_phase(night)
        await self._witch_phase(night)
        await self._seer_phase(night)
        deaths, notes = self.machine.resolve_night()
        for note in notes:
            self._note(note)
        shots = await self._hunter_phase(deaths)
        self.machine.dawn_text = render_morning(
            [(death.label, death.role_label) for death in deaths],
            [(death.label, death.role_label) for death in shots],
            list(self.machine.public_queue),
        )
        self.machine.public_queue.clear()
        self.machine.update_winner()

    async def _day(self) -> None:
        day = self.machine.night_number
        living = self.machine.living_ids()
        if not living:
            return
        talk = public_talk_task(day, self.machine.dawn_text, living, self.machine.graveyard_text())
        talk_result = await self._run_group(
            "public",
            living,
            talk,
            f"全体公聊 · 第{day}天 · 发言",
            open_public_talk(self._seats(living), len(living) * self.talk_rounds),
        )
        transcript = _transcript(talk_result)
        outcome = await self._vote(day, living, transcript, living)
        self._note(outcome.note)
        if outcome.tied_ids:
            outcome = await self._vote(day, self.machine.living_ids(), transcript, outcome.tied_ids, final=True)
            self._note(outcome.note)
        quote = ""
        if outcome.death is not None:
            quote = await self._last_words(outcome.death)
        shots = await self._hunter_phase([outcome.death] if outcome.death is not None else [])
        proclamation = outcome.public + "".join(
            f"猎人开枪带走 {shot.label}，身份是{shot.role_label}。" for shot in shots
        )
        if quote:
            proclamation += quote
        assert_public(proclamation)
        self.machine.public_queue.append(proclamation)
        await self._broadcast(proclamation)
        self.machine.update_winner()

    async def _guard_phase(self, night: int) -> None:
        guards = self.machine.living_with(Role.GUARD)
        if not guards:
            return
        guard = guards[0]
        legal = [player_id for player_id in self.machine.living_ids() if player_id != guard.last_guard]
        task = guard_task(night, self.machine.living_ids(), self.machine.graveyard_text(), guard.last_guard)
        raw = await self._ask("guard", guard.id, task, f"守卫私聊 · 第{night}夜")
        target, record = self._settle(guard.id, "guard", raw, legal)
        step = self.machine.apply_guard(guard.id, target)
        record.applied = target or "pass"
        if not step.ok:
            record.legal = False
            record.degraded = True
            record.applied = "pass"
        self._note(step.detail)

    async def _wolf_phase(self, night: int) -> None:
        wolf_ids = [player.id for player in self.machine.wolves()]
        if not wolf_ids:
            return
        archive = self._trim(self._wolf_archive)
        task = wolf_task(night, self.machine.living_ids(), wolf_ids, self.machine.graveyard_text(), archive)
        if len(wolf_ids) == 1:
            await self._ask("wolf", wolf_ids[0], task, f"狼人群 · 第{night}夜")
            logged = self.sessions[-1].messages
        else:
            logged = await self._run_group(
                "wolf",
                wolf_ids,
                task,
                f"狼人群 · 第{night}夜",
                open_wolf_meeting(
                    self._seats(wolf_ids),
                    self.model_client,
                    max_turns=len(wolf_ids) * self.wolf_rounds,
                ),
            )
        legal = self.machine.living_ids()
        votes: dict[str, str] = {}
        for player_id, raw in _latest_raw(logged, set(wolf_ids)).items():
            target, _record = self._settle(player_id, "kill", raw, legal)
            if target:
                votes[player_id] = target
        step = self.machine.apply_wolf_votes(votes)
        self._note(step.detail)
        self._wolf_archive = _transcript(logged)

    async def _witch_phase(self, night: int) -> None:
        witches = self.machine.living_with(Role.WITCH)
        if not witches:
            return
        witch = witches[0]
        task = witch_task(
            night,
            self.machine.living_ids(),
            self.machine.graveyard_text(),
            antidote=witch.antidote,
            poison=witch.poison_left,
            knife=self.machine.night.wolf_target,
        )
        raw = await self._ask("witch", witch.id, task, f"女巫私聊 · 第{night}夜")
        action = parse_action(raw)
        save = bool(action and action.save)
        poison = action.poison if action else None
        degraded = action is None
        if action is not None and action.passed:
            save = False
            poison = None
        step = self.machine.apply_witch(save, poison)
        self.action_logs.append(
            ActionRecord(
                player_id=witch.id,
                phase="witch",
                raw=raw,
                parsed=None if action is None else action.__dict__.copy(),
                legal=step.ok and not degraded,
                degraded=degraded or not step.ok,
                fallback="pass" if degraded or not step.ok else None,
                applied=step.detail,
            )
        )
        self._note(step.detail)

    async def _seer_phase(self, night: int) -> None:
        seers = self.machine.living_with(Role.SEER)
        if not seers:
            return
        seer = seers[0]
        legal = [player_id for player_id in self.machine.living_ids() if player_id != seer.id]
        task = seer_task(night, self.machine.living_ids(), self.machine.graveyard_text(), list(seer.checks))
        raw = await self._ask("seer", seer.id, task, f"预言家私聊 · 第{night}夜")
        target, _record = self._settle(seer.id, "check", raw, legal)
        step = self.machine.apply_seer(seer.id, target)
        self._note(step.detail)
        if not step.ok:
            return
        follow = seer_result_task(step.detail)
        await self._ask("seer", seer.id, follow, f"预言家私聊 · 第{night}夜 · 查验回复")

    async def _hunter_phase(self, deaths: list[Death]) -> list[Death]:
        shots: list[Death] = []
        for death in deaths:
            player = self.machine.player(death.player_id)
            if player.role != Role.HUNTER or player.alive:
                continue
            if not self.machine.can_shoot(player.id):
                raw = await self._ask(
                    "hunter",
                    player.id,
                    hunter_blocked_task(),
                    f"猎人私聊 · {player.label} · 不能开枪",
                    allow_dead=True,
                )
                self._settle(player.id, "shoot", raw, [])
                if death.cause == "poison":
                    self._note(f"{player.label} 死于毒药，不能开枪。")
                else:
                    self._note(f"{player.label} 不能开枪。")
                continue
            living = self.machine.living_ids()
            if not living:
                self._note(f"{player.label} 没有可以开枪的目标。")
                continue
            raw = await self._ask(
                "hunter",
                player.id,
                hunter_task(living, self.machine.graveyard_text()),
                f"猎人私聊 · {player.label}",
                allow_dead=True,
            )
            target, _record = self._settle(player.id, "shoot", raw, living)
            shot = self.machine.apply_shot(player.id, target)
            if shot is None:
                self._note(f"{player.label} 开枪无效。")
                continue
            shots.append(shot)
            self._note(f"{player.label} 开枪带走 {shot.label}。")
        return shots

    async def _vote(
        self,
        day: int,
        voters: list[str],
        transcript: str,
        allowed: list[str],
        *,
        final: bool = False,
    ):
        votes: dict[str, str] = {}
        graveyard = self.machine.graveyard_text()
        history = self._trim(transcript)
        for player_id in sorted(voters, key=seat_key):
            if not self.machine.is_alive(player_id):
                continue
            task = solo_vote_task(day, self.machine.living_ids(), graveyard, history, allowed)
            raw = await self._ask("vote", player_id, task, f"单独投票 · {player_id}")
            target, _record = self._settle(player_id, "vote", raw, allowed)
            if target:
                votes[player_id] = target
        return self.machine.resolve_vote(votes, allowed=set(allowed), final=final)

    async def _last_words(self, death: Death) -> str:
        raw = await self._ask(
            "last_words",
            death.player_id,
            last_words_task(death.label),
            f"遗言 · {death.label}",
            allow_dead=True,
        )
        try:
            assert_public(raw)
        except RuntimeError:
            self._note("遗言含有私密标记，不写入公聊。")
            return ""
        return f"{death.label} 的遗言：{raw}"

    async def _broadcast(self, text: str) -> None:
        assert_public(text)
        living = self.machine.living_ids()
        self.sessions.append(SessionLog("public", "状态机宣判", living, [{"source": "God", "content": text}], "broadcast"))
        self.printer("\n===== 状态机宣判（不开启聊天） =====")
        self.printer(text)
        self.printer("")
        notice = UserMessage(content=text, source="God")
        for player_id in living:
            await self.agents[player_id].model_context.add_message(notice)

    async def _ask(
        self,
        channel: str,
        player_id: str,
        task: str,
        banner: str,
        *,
        allow_dead: bool = False,
    ) -> str:
        self._check_membership(channel, [player_id], allow_dead=allow_dead)
        self.printer(f"\n===== 上帝单独询问 → {banner} =====")
        result = await self.agents[player_id].run(task=task)
        logged: list[dict[str, str]] = []
        reply = ""
        for message in result.messages:
            if not isinstance(message, TextMessage):
                continue
            logged.append({"source": message.source, "content": message.content})
            if message.source == player_id:
                reply = message.content
            speaker = "上帝" if message.source == "user" else message.source
            self.printer(f"[{speaker}]\n{message.content.strip()}\n")
        self.sessions.append(SessionLog(channel, banner, [player_id], logged, "solo"))
        return reply

    async def _run_group(self, channel: str, player_ids: list[str], task: str, banner: str, team: Any) -> list[dict[str, str]]:
        ordered = sorted(player_ids, key=seat_key)
        self._check_membership(channel, ordered, allow_dead=False)
        self.printer(f"\n===== 上帝切换频道 → {banner} =====")
        self.printer("参与: " + ", ".join(ordered))
        try:
            result = await team.run(task=task)
        except Exception as exc:
            raise RuntimeError(f"频道 {channel} 运行失败") from exc
        logged: list[dict[str, str]] = []
        for message in result.messages:
            if not isinstance(message, TextMessage):
                continue
            logged.append({"source": message.source, "content": message.content})
            speaker = "上帝" if message.source == "user" else message.source
            self.printer(f"[{speaker}]\n{message.content.strip()}\n")
        self.sessions.append(SessionLog(channel, banner, ordered, logged, "group"))
        return logged

    def _settle(self, player_id: str, phase: str, raw: str, legal_ids: list[str]) -> tuple[str | None, ActionRecord]:
        target, record = resolve_target(
            raw,
            player_id=player_id,
            phase=phase,
            legal_ids=legal_ids,
            rng=self.machine.rng,
            fallback=self.machine.rules.action_fallback,
        )
        self.action_logs.append(record)
        if record.degraded:
            self._note(f"{player_id} 的 {phase} 行动已降级为 {record.applied}。")
        return target, record

    def _seats(self, player_ids: list[str]) -> list[SeatAgent]:
        return [self.agents[player_id] for player_id in sorted(player_ids, key=seat_key)]

    def _trim(self, text: str) -> str:
        limit = self.machine.rules.history_chars
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _check_membership(self, channel: str, player_ids: list[str], *, allow_dead: bool) -> None:
        if not player_ids:
            raise RuntimeError(f"频道 {channel} 没有参与者")
        expected = {
            "seer": Role.SEER,
            "witch": Role.WITCH,
            "guard": Role.GUARD,
            "hunter": Role.HUNTER,
        }
        for player_id in player_ids:
            player = self.machine.player(player_id)
            if not allow_dead and not player.alive:
                raise RuntimeError(f"{player_id} 已出局，不能进入 {channel}")
            if channel == "wolf" and not player.is_wolf:
                raise RuntimeError(f"{player_id} 不是狼人，不能进入狼人群")
            if channel in expected and player.role != expected[channel]:
                raise RuntimeError(f"{player_id} 的身份不是 {role_label(expected[channel])}")

    def _note(self, text: str) -> None:
        self._observer(f"【裁判】{text}")

    def _observer(self, text: str) -> None:
        self.observer.append(text)
        self.printer("\n----- 裁判 -----")
        self.printer(text)
        self.printer("")

    def _summary(self) -> str:
        title = {"village": "好人阵营", "werewolf": "狼人阵营", "draw": "平局"}[self.machine.winner or "draw"]
        lines = [f"游戏结束：{title}"]
        cause = {"wolf": "狼刀", "poison": "毒", "vote": "放逐", "shot": "猎枪"}
        for player in self.machine.players:
            if player.alive:
                state = "存活"
            else:
                state = "出局/" + cause.get(player.death_cause or "", player.death_cause or "")
            lines.append(f"- {player.label} {player.id} {role_label(player.role)} {state}")
        return "\n".join(lines)

    def _result(self) -> GameResult:
        return GameResult(
            winner=self.machine.winner or "draw",
            preset=self.machine.preset,
            seed=self.machine.seed,
            roles={player.id: player.role.value for player in self.machine.players},
            rules=self.machine.rules.to_dict(),
            sessions=self.sessions,
            observer=self.observer,
            transitions=list(self.machine.transitions),
            actions=[record.to_dict() for record in self.action_logs],
            llm_calls=self.llm_calls,
        )


def _transcript(messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for message in messages:
        source = message["source"]
        speaker = "上帝" if source in {"user", "God"} else source
        lines.append(f"{speaker}: {message['content'].strip()}")
    return "\n".join(lines)


def _latest_raw(messages: list[dict[str, str]], allowed: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for message in messages:
        if message["source"] in allowed:
            found[message["source"]] = message["content"]
    return found


async def play_match(
    *,
    preset: str,
    seed: int,
    mock: bool,
    model: str,
    api_key: str | None,
    base_url: str | None,
    talk_rounds: int,
    wolf_rounds: int,
    rules: Rules | None = None,
    printer: Printer = print,
) -> GameResult:
    rules = rules or Rules()
    machine = GameMachine.deal(preset, seed, rules)
    calls: list[dict[str, object]] = []
    client = build_traced_client(
        mock=mock,
        model=model,
        speech_model=rules.speech_model,
        decision_model=rules.decision_model,
        api_key=api_key,
        base_url=base_url,
        sink=calls,
    )
    try:
        agents, prompts = build_players(machine, client)
        director = GodDirector(
            machine,
            agents,
            prompts,
            client,
            talk_rounds=talk_rounds,
            wolf_rounds=wolf_rounds,
            printer=printer,
            llm_calls=calls,
        )
        return await director.play()
    finally:
        await client.close()
