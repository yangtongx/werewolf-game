import asyncio
import unittest
from pathlib import Path

from werewolf.game.actions import resolve_target
from werewolf.game.machine import (
    CAUSE_POISON,
    CAUSE_WOLF,
    GameMachine,
    Role,
)
from werewolf.game.parse import parse_action
from werewolf.play.prompts import audit_leaks, public_talk_task, system_prompt, wolf_task
from werewolf.play.model_client import build_seat_clients
from werewolf.game.rules import Rules, load_rules
from werewolf.play.vendors import Vendor, assign_vendors, load_vendors


def board(*roles: str) -> GameMachine:
    return GameMachine([Role(role) for role in roles])


class ParseTests(unittest.TestCase):
    def test_last_action_wins_and_accepts_seat_aliases(self):
        text = "先随便说说。\n[ACTION] kill=p1\n改主意了\n[ACTION] kill=3号 save=yes poison=none"
        action = parse_action(text)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.kill, "p3")
        self.assertTrue(action.save)
        self.assertIsNone(action.poison)

    def test_vote_abstain(self):
        action = parse_action("[ACTION] vote=abstain")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertTrue(action.vote_abstain)
        self.assertIsNone(action.vote)

    def test_missing_action(self):
        self.assertIsNone(parse_action("我还在想。"))

    def test_last_action_survives_markup_and_pass(self):
        text = "例如：\n```\n[ACTION] kill=p1\n```\n最终是 **[ACTION] kill=p8**"
        action = parse_action(text)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.kill, "p8")
        self.assertGreater(action.match_count, 1)
        passed = parse_action("先不说。\n[ACTION] pass")
        self.assertIsNotNone(passed)
        assert passed is not None
        self.assertTrue(passed.passed)

    def test_illegal_action_falls_back_without_raising(self):
        target, record = resolve_target(
            "我随便写 [ACTION] guard=p9",
            player_id="p5",
            phase="guard",
            legal_ids=["p1", "p2"],
            rng=__import__("random").Random(1),
            fallback="pass",
        )
        self.assertIsNone(target)
        self.assertTrue(record.degraded)
        self.assertEqual(record.applied, "pass")
        target, record = resolve_target(
            "没有行动",
            player_id="p5",
            phase="guard",
            legal_ids=["p1", "p2"],
            rng=__import__("random").Random(1),
            fallback="random",
        )
        self.assertIn(target, {"p1", "p2"})
        self.assertEqual(record.fallback, "random")

    def test_same_guard_and_witch_can_be_configured_to_live(self):
        game = board("werewolf", "werewolf", "villager", "witch", "guard", "villager")
        game.rules = Rules(guard_witch_same_target="live")
        game.begin_night()
        game.apply_guard("p5", "p6")
        game.apply_wolf_votes({"p1": "p6", "p2": "p6"})
        game.apply_witch(True, None)
        deaths, notes = game.resolve_night()
        self.assertEqual(deaths, [])
        self.assertTrue(any("存活" in note for note in notes))

    def test_slaughter_side_wins_when_the_village_side_is_gone(self):
        roles = ("werewolf", "werewolf", "seer", "villager", "villager", "villager")
        side = board(*roles)
        side.rules = Rules(win_condition="slaughter_side")
        side.begin_night()
        side.apply_wolf_votes({"p1": "p3", "p2": "p3"})
        side.resolve_night()
        self.assertEqual(side.update_winner(), "werewolf")

        town = board(*roles)
        town.begin_night()
        town.apply_wolf_votes({"p1": "p3", "p2": "p3"})
        town.resolve_night()
        self.assertIsNone(town.update_winner())

    def test_pk_returns_the_tied_seats_once(self):
        game = board("werewolf", "villager", "villager")
        game.rules = Rules(vote_tie="pk")
        outcome = game.resolve_vote({"p1": "p2", "p2": "p1"})
        self.assertEqual(set(outcome.tied_ids), {"p1", "p2"})
        self.assertTrue(game.player("p1").alive)
        again = game.resolve_vote({"p1": "p2", "p2": "p1"}, allowed={"p1", "p2"}, final=True)
        self.assertIsNone(again.death)
        self.assertEqual(again.tied_ids, [])

    def test_config_file_loads_the_explicit_rules(self):
        rules = load_rules(Path(__file__).resolve().parents[1] / "config.yaml")
        self.assertEqual(rules.guard_witch_same_target, "die")
        self.assertEqual(rules.win_condition, "slaughter_town")
        self.assertEqual(rules.vote_tie, "no_exile")
        self.assertFalse(rules.hunter_poison_shoot)
        vendors = load_vendors(Path(__file__).resolve().parents[1] / "config.yaml")
        self.assertEqual(len(vendors), 12)
        self.assertEqual(
            [vendor.id for vendor in vendors[:6]],
            ["openai", "deepseek", "qwen", "zhipu", "moonshot", "gemini"],
        )
        models = {vendor.id: vendor.model for vendor in vendors}
        self.assertEqual(models["openai"], "gpt-6-astra")
        self.assertEqual(models["deepseek"], "deepseek-v4-pro")
        self.assertEqual(models["qwen"], "qwen3.8-max")
        self.assertEqual(models["zhipu"], "glm-5.3")
        self.assertEqual(models["moonshot"], "kimi-k3")
        self.assertEqual(models["gemini"], "gemini-3.8-flash")


class MachineTests(unittest.TestCase):
    def test_same_seed_deals_the_same_board(self):
        left = GameMachine.deal("classic6", 7)
        right = GameMachine.deal("classic6", 7)
        self.assertEqual([player.role for player in left.players], [player.role for player in right.players])
        self.assertEqual(
            [player.role for player in left.players].count(Role.WEREWOLF),
            2,
        )

    def test_classic12_is_four_wolves_and_the_standard_gods(self):
        game = GameMachine.deal("classic12", 1)
        counts = {role: 0 for role in Role}
        for player in game.players:
            counts[player.role] += 1
        self.assertEqual(len(game.players), 12)
        self.assertEqual(counts[Role.WEREWOLF], 4)
        self.assertEqual(counts[Role.SEER], 1)
        self.assertEqual(counts[Role.WITCH], 1)
        self.assertEqual(counts[Role.GUARD], 1)
        self.assertEqual(counts[Role.HUNTER], 1)
        self.assertEqual(counts[Role.VILLAGER], 4)
        self.assertIsNone(game.update_winner())

    def test_guard_blocks_knife_and_cannot_repeat(self):
        game = board("werewolf", "werewolf", "villager", "witch", "guard", "villager")
        game.begin_night()
        self.assertTrue(game.apply_guard("p5", "p6").ok)
        game.apply_wolf_votes({"p1": "p6", "p2": "p6"})
        game.apply_witch(False, None)
        deaths, notes = game.resolve_night()
        self.assertEqual(deaths, [])
        self.assertTrue(any("守卫守住了" in note for note in notes))

        game.begin_night()
        blocked = game.apply_guard("p5", "p6")
        self.assertFalse(blocked.ok)
        self.assertIsNone(game.night.guard_target)

    def test_milk_through_kills_and_spends_antidote(self):
        game = board("werewolf", "werewolf", "villager", "witch", "guard", "villager")
        game.begin_night()
        game.apply_guard("p5", "p6")
        game.apply_wolf_votes({"p1": "p6", "p2": "p6"})
        game.apply_witch(True, "p3")
        self.assertFalse(game.player("p4").antidote)
        self.assertTrue(game.player("p4").poison_left)
        self.assertIsNone(game.night.witch_poison)
        deaths, notes = game.resolve_night()
        self.assertEqual([death.player_id for death in deaths], ["p6"])
        self.assertEqual(deaths[0].cause, CAUSE_WOLF)
        self.assertTrue(any("同守同救" in note for note in notes))

    def test_poison_ignores_guard(self):
        game = board("werewolf", "werewolf", "villager", "witch", "guard", "villager")
        game.begin_night()
        game.apply_guard("p5", "p3")
        game.apply_wolf_votes({"p1": "p6", "p2": "p6"})
        game.apply_witch(False, "p3")
        deaths, _notes = game.resolve_night()
        self.assertEqual({death.player_id for death in deaths}, {"p3", "p6"})
        by_id = {death.player_id: death.cause for death in deaths}
        self.assertEqual(by_id["p3"], CAUSE_POISON)
        self.assertEqual(by_id["p6"], CAUSE_WOLF)

    def test_poisoned_hunter_cannot_shoot_and_wolf_killed_hunter_can(self):
        poisoned = board("werewolf", "witch", "hunter", "villager")
        poisoned.begin_night()
        poisoned.apply_wolf_votes({"p1": "p4"})
        poisoned.apply_witch(False, "p3")
        deaths, _notes = poisoned.resolve_night()
        hunter_death = next(death for death in deaths if death.player_id == "p3")
        self.assertEqual(hunter_death.cause, CAUSE_POISON)
        self.assertFalse(poisoned.can_shoot("p3"))

        shot = board("werewolf", "villager", "hunter")
        shot.begin_night()
        shot.apply_wolf_votes({"p1": "p3"})
        deaths, _notes = shot.resolve_night()
        self.assertTrue(shot.can_shoot("p3"))
        killed = shot.apply_shot("p3", "p1")
        self.assertIsNotNone(killed)
        shot.update_winner()
        self.assertEqual(shot.winner, "village")

    def test_tied_vote_exiles_nobody_and_majority_ends_the_game(self):
        game = board("werewolf", "villager", "villager")
        game.begin_night()
        game.resolve_night()
        first = game.resolve_vote({"p1": "p2", "p2": "p1"})
        self.assertIsNone(first.death)
        self.assertIn("平票", first.public)
        self.assertIsNone(game.update_winner())

        second = game.resolve_vote({"p2": "p1", "p3": "p1", "p1": "p2"})
        self.assertIsNotNone(second.death)
        assert second.death is not None
        self.assertEqual(second.death.player_id, "p1")
        self.assertIn("身份是狼人", second.public)
        self.assertEqual(game.update_winner(), "village")

    def test_equal_numbers_are_a_wolf_win(self):
        game = board("werewolf", "villager")
        self.assertEqual(game.update_winner(), "werewolf")

    def test_wolf_vote_tie_is_an_empty_knife(self):
        game = board("werewolf", "werewolf", "villager", "villager")
        game.begin_night()
        result = game.apply_wolf_votes({"p1": "p3", "p2": "p4"})
        self.assertIsNone(game.night.wolf_target)
        self.assertIn("空刀", result.detail)

    def test_public_task_rejects_private_markers(self):
        with self.assertRaises(RuntimeError):
            public_talk_task(1, "刀口: p1", ["p2"], "已出局: 无")

    def test_public_speech_asks_for_a_stance_without_the_wolf_plan(self):
        text = public_talk_task(1, "昨夜平安，无人出局。", ["p1", "p2"], "已出局: 无")
        self.assertIn("点出一个在场座位", text)
        self.assertNotIn("悍跳预言家", text)
        self.assertNotIn("狼队友", text)

    def test_wolf_prompt_offers_choices_and_goods_do_not_see_them(self):
        wolves = board("werewolf", "werewolf", "villager")
        prompts = {
            player.id: system_prompt(
                player,
                [other.id for other in wolves.wolves() if other.id != player.id],
            )
            for player in wolves.players
        }
        for wolf_id in ("p1", "p2"):
            self.assertIn("可以悍跳预言家", prompts[wolf_id])
            self.assertIn("也可以深藏", prompts[wolf_id])
            self.assertIn("也可以不质疑", prompts[wolf_id])
            self.assertIn("自刀", prompts[wolf_id])
            self.assertNotIn("由你悍跳", prompts[wolf_id])
            self.assertNotIn("座位号", prompts[wolf_id])
        self.assertNotIn("悍跳预言家", prompts["p3"])
        self.assertNotIn("自刀", prompts["p3"])
        self.assertNotIn("狼队友:", prompts["p3"])
        self.assertIn("点出一个座位", prompts["p3"])

    def test_leak_audit_flags_markers_and_named_mates(self):
        self.assertEqual(audit_leaks("我跳预言家。昨晚验了p3，是好人。"), [])
        self.assertIn("狼队友:", audit_leaks("狼队友: p2"))
        self.assertIn("点名同伴", audit_leaks("队友是p2"))
        self.assertIn("（狼队密谈）", audit_leaks("（狼队密谈）刀口=p3"))

    def test_wolf_task_names_only_the_wolf_channel(self):
        text = wolf_task(1, ["p1", "p2", "p3"], ["p1", "p2"], "已出局: 无")
        self.assertIn("【频道：狼人群】", text)
        self.assertIn("本频道狼人: p1, p2", text)
        self.assertIn("自刀", text)
        self.assertIn("包括同伴", text)
        self.assertNotIn("必须自刀", text)
        self.assertNotIn("【频道：全体公聊】", text)


class VendorTests(unittest.TestCase):
    def test_player_count_takes_that_many_distinct_vendors(self) -> None:
        vendors = load_vendors(Path(__file__).resolve().parents[1] / "config.yaml")
        for count in (6, 9, 12):
            seats = [f"p{index}" for index in range(1, count + 1)]
            dealt = assign_vendors(seats, vendors, 7)
            self.assertEqual(len(dealt), count)
            self.assertEqual({vendor.id for vendor in dealt.values()}, {vendor.id for vendor in vendors[:count]})

    def test_same_seed_keeps_the_model_assignment(self) -> None:
        vendors = load_vendors(Path(__file__).resolve().parents[1] / "config.yaml")
        seats = [f"p{index}" for index in range(1, 7)]
        left = [assign_vendors(seats, vendors, 7)[seat].id for seat in seats]
        right = [assign_vendors(seats, vendors, 7)[seat].id for seat in seats]
        other = [assign_vendors(seats, vendors, 8)[seat].id for seat in seats]
        self.assertEqual(left, right)
        self.assertNotEqual(left, other)

    def test_too_few_vendors_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            assign_vendors(["p1", "p2"], [Vendor("only", "只有一家", "m", "", "ONLY_KEY")], 1)

    def test_missing_key_names_the_vendor(self) -> None:
        vendor = Vendor("fake", "假厂家", "fake-model", "", "WEREWOLF_MISSING_KEY")

        async def run() -> None:
            await build_seat_clients({"p1": vendor}, mock=False, sink=[])

        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(run())
        self.assertIn("假厂家", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
