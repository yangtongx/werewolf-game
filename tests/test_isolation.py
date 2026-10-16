import asyncio
import unittest

from autogen_core.models import AssistantMessage, SystemMessage, UserMessage

from werewolf.director import GodDirector, build_players, play_match
from werewolf.machine import CAUSE_POISON, GameMachine, Role
from werewolf.model_client import ScriptedWerewolfClient
from werewolf.prompts import PRIVATE_MARKERS, assert_public


class ChannelIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_night_channel_never_enters_day_chat_or_other_memories(self) -> None:
        machine = GameMachine.deal("classic6", seed=7)
        client = ScriptedWerewolfClient()
        agents, prompts = build_players(machine, client)
        director = GodDirector(
            machine,
            agents,
            prompts,
            client,
            talk_rounds=1,
            wolf_rounds=1,
            printer=lambda _line: None,
        )
        result = await director.play()
        await client.close()

        self.assertIn(result.winner, {"village", "werewolf"})
        wolves = [player.id for player in machine.players if player.role == Role.WEREWOLF]
        seer = next(player.id for player in machine.players if player.role == Role.SEER)
        witch = next(player.id for player in machine.players if player.role == Role.WITCH)
        guard = next(player.id for player in machine.players if player.role == Role.GUARD)
        goods = [player.id for player in machine.players if player.role != Role.WEREWOLF]

        self.assertTrue(any(session.channel == "wolf" for session in result.sessions))
        self.assertTrue(any(session.channel == "public" for session in result.sessions))

        wolf_lines = _utterances(result, "wolf", speakers=set(wolves))
        self.assertTrue(wolf_lines)
        self.assertTrue(all("（狼队密谈）" in line for line in wolf_lines))

        public_blob = _blob(result, "public")
        observer_blob = "\n".join(result.observer)
        self.assertIn("身份底牌", observer_blob)
        self.assertIn("游戏结束：", observer_blob)

        for session in result.sessions:
            if session.channel == "wolf":
                self.assertTrue(set(session.participants).issubset(wolves))
            elif session.channel == "seer":
                self.assertEqual(session.kind, "solo")
                self.assertEqual(session.participants, [seer])
            elif session.channel == "witch":
                self.assertEqual(session.kind, "solo")
                self.assertEqual(session.participants, [witch])
            elif session.channel == "guard":
                self.assertEqual(session.kind, "solo")
                self.assertEqual(session.participants, [guard])
            elif session.channel == "public" and session.kind == "group":
                for message in session.messages:
                    if message["source"] == "user":
                        assert_public(message["content"])
            elif session.channel in {"seer", "witch", "guard", "vote", "last_words"}:
                self.assertEqual(session.kind, "solo")
                self.assertEqual(len(session.participants), 1)

        vote_sessions = [session for session in result.sessions if session.channel == "vote"]
        self.assertTrue(vote_sessions)
        broadcasts = [session for session in result.sessions if session.kind == "broadcast"]
        self.assertTrue(broadcasts)
        for session in broadcasts:
            self.assertTrue(all(message["source"] == "God" for message in session.messages))
            assert_public(session.messages[0]["content"])
        self.assertFalse(any(session.kind == "group" and "投票" in session.banner for session in result.sessions))
        self.assertFalse(any(session.kind == "group" and "宣判" in session.banner for session in result.sessions))

        for channel in ("public", "seer", "witch", "guard", "hunter"):
            blob = _blob(result, channel)
            self.assertNotIn("（狼队密谈）", blob)
            self.assertNotIn("身份底牌", blob)
            self.assertNotIn("【裁判】", blob)
            self.assertNotIn("本频道狼人:", blob)

        self.assertIn("查验结果:", _blob(result, "seer"))
        self.assertNotIn("查验结果:", public_blob)
        self.assertIn("刀口:", _blob(result, "witch"))
        self.assertNotIn("刀口:", public_blob)

        wolf_sessions = [session for session in result.sessions if session.channel == "wolf"]
        if len(wolf_sessions) >= 2:
            opening = "\n".join(
                message["content"] for message in wolf_sessions[1].messages if message["source"] == "user"
            )
            self.assertIn("上一夜狼队记录", opening)
            self.assertIn("（狼队密谈）", opening)

        for player_id in wolves:
            self.assertIn("狼队友:", prompts[player_id])
        for player_id in goods:
            context = await _context(agents[player_id])
            for line in wolf_lines:
                self.assertNotIn(line, context)
            self.assertNotIn("狼队友:", prompts[player_id])
            self.assertNotIn("本频道狼人:", context)
            self.assertNotIn("上一夜狼队记录", context)
            if player_id != seer:
                self.assertNotIn("查验结果:", context)
            if player_id != witch:
                self.assertNotIn("刀口:", context)
            for marker in ("身份底牌", "【裁判】", "女巫解救了", "女巫毒杀了", "同守同救"):
                self.assertNotIn(marker, context)

        seer_context = await _context(agents[seer])
        self.assertIn("查验结果:", seer_context)
        for marker in PRIVATE_MARKERS:
            if marker in {"已查验:", "查验结果:"}:
                continue
            self.assertNotIn(marker, seer_context)

    async def test_script_reads_the_current_task_and_still_remembers_checks(self) -> None:
        client = ScriptedWerewolfClient()
        system = SystemMessage(content="你的座位: p6\n你的身份: seer\n")
        old_result = UserMessage(
            content="【频道：预言家】\n查验结果: p2 是狼人\n请只回复：收到。",
            source="God",
        )
        remembered = AssistantMessage(content="收到。", source="p6")
        next_night = UserMessage(
            content="【频道：预言家】\n在场: p2, p5, p6\n已查验: p2=狼人\n环节: 夜间\n",
            source="God",
        )
        check = await client.create([system, old_result, remembered, next_night])
        self.assertIn("[ACTION] check=p5", str(check.content))

        vote = UserMessage(
            content="【频道：全体公聊】\n在场: p2, p5, p6\n环节: 投票\n",
            source="God",
        )
        ballot = await client.create([system, old_result, remembered, vote])
        self.assertIn("[ACTION] vote=p2", str(ballot.content))
        await client.close()

    async def test_classic9_finishes_with_hunter_channel_closed(self) -> None:
        machine = GameMachine.deal("classic9", seed=3)
        client = ScriptedWerewolfClient()
        agents, prompts = build_players(machine, client)
        director = GodDirector(
            machine,
            agents,
            prompts,
            client,
            talk_rounds=1,
            wolf_rounds=1,
            printer=lambda _line: None,
        )
        result = await director.play()
        await client.close()
        self.assertIn(result.winner, {"village", "werewolf"})
        hunter = next(player.id for player in machine.players if player.role == Role.HUNTER)
        for session in result.sessions:
            if session.channel == "hunter":
                self.assertEqual(session.participants, [hunter])
            if session.channel == "public":
                blob = "\n".join(message["content"] for message in session.messages)
                self.assertNotIn("死于毒药", blob)
                self.assertNotIn("（狼队密谈）", blob)

    async def test_hunter_shot_is_not_spoken_in_the_public_channel(self) -> None:
        machine = GameMachine([Role.WEREWOLF, Role.HUNTER, Role.VILLAGER])
        client = ScriptedWerewolfClient()
        agents, prompts = build_players(machine, client)
        director = GodDirector(
            machine,
            agents,
            prompts,
            client,
            talk_rounds=1,
            wolf_rounds=1,
            printer=lambda _line: None,
        )
        result = await director.play()
        await client.close()

        self.assertEqual(result.winner, "village")
        hunter_sessions = [session for session in result.sessions if session.channel == "hunter"]
        self.assertEqual(len(hunter_sessions), 1)
        self.assertEqual(hunter_sessions[0].participants, ["p2"])
        self.assertIn("[ACTION] shoot=p1", _blob(result, "hunter"))
        self.assertNotIn("[ACTION] shoot=", _blob(result, "public"))
        self.assertNotIn("[ACTION] shoot=", _blob(result, "wolf"))
        self.assertIn("猎人开枪带走", "\n".join(result.observer))

    async def test_same_night_wolf_lines_are_not_backfilled(self) -> None:
        machine = GameMachine([Role.WEREWOLF, Role.WEREWOLF, Role.VILLAGER])
        client = ScriptedWerewolfClient()
        agents, _prompts = build_players(machine, client)
        director = GodDirector(machine, agents, {}, client, wolf_rounds=2, printer=lambda _line: None)
        machine.begin_night()
        await director._wolf_phase(1)
        first = await _context(agents["p1"])
        second = await _context(agents["p2"])
        self.assertIn("发言人=p1", second)
        self.assertNotIn("发言人=p2", first)
        self.assertNotIn("上一夜狼队记录", first)

        machine.begin_night()
        await director._wolf_phase(2)
        first_later = await _context(agents["p1"])
        self.assertIn("上一夜狼队记录", first_later)
        self.assertIn("发言人=p2", first_later)
        villager = await _context(agents["p3"])
        self.assertNotIn("上一夜狼队记录", villager)
        self.assertNotIn("（狼队密谈）", villager)
        await client.close()

    async def test_poisoned_hunter_is_told_to_pass_without_the_reason(self) -> None:
        machine = GameMachine([Role.WEREWOLF, Role.WITCH, Role.HUNTER])
        client = ScriptedWerewolfClient()
        agents, _prompts = build_players(machine, client)
        director = GodDirector(machine, agents, {}, client, printer=lambda _line: None)
        machine.begin_night()
        machine.apply_witch(False, "p3")
        deaths, _notes = machine.resolve_night()
        self.assertEqual(deaths[0].cause, CAUSE_POISON)
        shots = await director._hunter_phase(deaths)
        self.assertEqual(shots, [])
        hunter_text = "\n".join(
            message["content"]
            for session in director.sessions
            if session.channel == "hunter"
            for message in session.messages
        )
        self.assertIn("你不能开枪", hunter_text)
        self.assertIn("[ACTION] pass", hunter_text)
        self.assertNotIn("毒药", hunter_text)
        self.assertNotIn("死于毒药", hunter_text)
        self.assertTrue(any("死于毒药" in line for line in director.observer))
        await client.close()

    async def test_mock_match_records_calls_transitions_and_actions(self) -> None:
        result = await play_match(
            preset="classic6",
            seed=7,
            mock=True,
            model="mock",
            api_key=None,
            base_url=None,
            talk_rounds=1,
            wolf_rounds=1,
            printer=lambda _line: None,
        )
        self.assertTrue(result.transitions)
        self.assertTrue(result.actions)
        self.assertTrue(result.llm_calls)
        call = result.llm_calls[0]
        for key in ("route", "latency_ms", "prompt_tokens", "completion_tokens", "prompt", "response"):
            self.assertIn(key, call)
        self.assertTrue(any("raw" in record and "degraded" in record for record in result.actions))


def _utterances(result, channel: str, speakers: set[str]) -> list[str]:
    lines: list[str] = []
    for session in result.sessions:
        if session.channel != channel:
            continue
        for message in session.messages:
            if message["source"] in speakers:
                lines.append(message["content"])
    return lines


def _blob(result, channel: str) -> str:
    parts: list[str] = []
    for session in result.sessions:
        if session.channel != channel:
            continue
        parts.extend(message["content"] for message in session.messages)
    return "\n".join(parts)


async def _context(agent) -> str:
    messages = await agent.model_context.get_messages()
    chunks: list[str] = []
    for message in messages:
        content = getattr(message, "content", "")
        chunks.append(content if isinstance(content, str) else str(content))
    return "\n".join(chunks)


if __name__ == "__main__":
    asyncio.run(unittest.main())
