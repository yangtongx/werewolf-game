"""外部游戏状态机。

这里没有模型，也没有聊天记录。它只保存身份、存活、药水、守护和胜负，
并按固定规则把一夜的行动结算成死亡名单。频道之间能共享的，只有这里
标成公开的事实（谁出局、公开身份、还剩谁）。
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from werewolf.rules import Rules


class Role(str, Enum):
    WEREWOLF = "werewolf"
    SEER = "seer"
    WITCH = "witch"
    GUARD = "guard"
    HUNTER = "hunter"
    VILLAGER = "villager"


ROLE_LABEL = {
    Role.WEREWOLF: "狼人",
    Role.SEER: "预言家",
    Role.WITCH: "女巫",
    Role.GUARD: "守卫",
    Role.HUNTER: "猎人",
    Role.VILLAGER: "村民",
}

CAUSE_WOLF = "wolf"
CAUSE_POISON = "poison"
CAUSE_VOTE = "vote"
CAUSE_SHOT = "shot"

PRESETS: dict[str, tuple[Role, ...]] = {
    "classic6": (
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.SEER,
        Role.WITCH,
        Role.GUARD,
        Role.VILLAGER,
    ),
    "classic9": (
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.SEER,
        Role.WITCH,
        Role.GUARD,
        Role.HUNTER,
        Role.VILLAGER,
        Role.VILLAGER,
    ),
    "classic12": (
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.WEREWOLF,
        Role.SEER,
        Role.WITCH,
        Role.GUARD,
        Role.HUNTER,
        Role.VILLAGER,
        Role.VILLAGER,
        Role.VILLAGER,
        Role.VILLAGER,
    ),
}


def role_label(role: Role) -> str:
    return ROLE_LABEL[role]


def seat_key(player_id: str) -> int:
    return int(player_id[1:])


@dataclass
class Player:
    id: str
    label: str
    role: Role
    alive: bool = True
    antidote: bool = False
    poison_left: bool = False
    last_guard: str | None = None
    checks: list[tuple[str, str]] = field(default_factory=list)
    death_cause: str | None = None

    @property
    def is_wolf(self) -> bool:
        return self.role == Role.WEREWOLF


@dataclass(frozen=True)
class Death:
    player_id: str
    label: str
    role_label: str
    cause: str


@dataclass
class NightPlan:
    guard_target: str | None = None
    wolf_target: str | None = None
    witch_save: bool = False
    witch_poison: str | None = None
    seer_target: str | None = None


@dataclass
class StepResult:
    ok: bool
    detail: str


GOD_ROLES = {Role.SEER, Role.WITCH, Role.GUARD, Role.HUNTER}


@dataclass
class VoteOutcome:
    death: Death | None
    note: str
    public: str
    tied_ids: list[str] = field(default_factory=list)


class GameMachine:
    """一局牌的规则内核。上帝调度器调用它，模型不能改这里的字段。"""

    def __init__(
        self,
        roles: list[Role],
        *,
        seed: int | None = None,
        preset: str | None = None,
        rules: Rules | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if len(roles) < 2:
            raise ValueError("至少需要 2 名玩家")
        self.seed = seed
        self.preset = preset
        self.rules = rules or Rules()
        self.rules.validate()
        self.rng = rng or random.Random(seed)
        self.players: list[Player] = []
        for index, role in enumerate(roles, start=1):
            player = Player(id=f"p{index}", label=f"{index}号", role=role)
            if role == Role.WITCH:
                player.antidote = True
                player.poison_left = True
            self.players.append(player)
        self._by_id = {player.id: player for player in self.players}
        self.night_number = 0
        self.night = NightPlan()
        self.graveyard: list[Death] = []
        self.public_queue: list[str] = []
        self.dawn_text = ""
        self.winner: str | None = None
        self.transitions: list[dict[str, object]] = []

    @classmethod
    def deal(cls, preset: str, seed: int, rules: Rules | None = None) -> GameMachine:
        if preset not in PRESETS:
            known = ", ".join(PRESETS)
            raise ValueError(f"未知牌局 {preset}，可选：{known}")
        rng = random.Random(seed)
        roles = list(PRESETS[preset])
        rng.shuffle(roles)
        return cls(roles, seed=seed, preset=preset, rules=rules, rng=rng)

    def player(self, player_id: str) -> Player:
        try:
            return self._by_id[player_id]
        except KeyError as exc:
            raise KeyError(f"没有玩家 {player_id}") from exc

    def living(self) -> list[Player]:
        return [player for player in self.players if player.alive]

    def living_ids(self) -> list[str]:
        return [player.id for player in self.living()]

    def living_with(self, role: Role) -> list[Player]:
        return [player for player in self.living() if player.role == role]

    def is_alive(self, player_id: str) -> bool:
        return player_id in self._by_id and self._by_id[player_id].alive

    def wolves(self, *, alive_only: bool = True) -> list[Player]:
        wolves = [player for player in self.players if player.is_wolf]
        if alive_only:
            wolves = [player for player in wolves if player.alive]
        return wolves

    def graveyard_text(self) -> str:
        if not self.graveyard:
            return "已出局: 无"
        body = ", ".join(f"{death.label}({death.role_label})" for death in self.graveyard)
        return f"已出局: {body}"

    def roster_text(self) -> str:
        lines = ["身份底牌（只写在裁判控制台，不进入任何玩家频道）:"]
        for player in self.players:
            lines.append(f"- {player.id} {player.label} = {role_label(player.role)}")
        return "\n".join(lines)

    def begin_night(self) -> int:
        self.night_number += 1
        self.night = NightPlan()
        return self.night_number

    def update_winner(self) -> str | None:
        if self.winner is not None:
            return self.winner
        wolves = self.wolves()
        goods = [player for player in self.living() if not player.is_wolf]
        if self.rules.win_condition == "slaughter_side":
            if not wolves:
                self.winner = "village"
            else:
                gods = [player for player in goods if player.role in GOD_ROLES]
                villagers = [player for player in goods if player.role == Role.VILLAGER]
                if not gods or not villagers:
                    self.winner = "werewolf"
        elif not wolves:
            self.winner = "village"
        elif len(wolves) >= len(goods):
            self.winner = "werewolf"
        if self.winner is not None:
            self._transition("winner", self.winner)
        return self.winner

    def apply_guard(self, guard_id: str, target_id: str | None) -> StepResult:
        guard = self.player(guard_id)
        if not guard.alive or guard.role != Role.GUARD:
            return StepResult(False, "守卫不能行动。")
        if target_id is None or not self.is_alive(target_id):
            return StepResult(False, "守护目标无效，今晚空守。")
        if target_id == guard.last_guard:
            return StepResult(False, f"不能连续守护 {self.player(target_id).label}，今晚空守。")
        guard.last_guard = target_id
        self.night.guard_target = target_id
        return StepResult(True, f"守卫守护 {self.player(target_id).label}。")

    def apply_wolf_votes(self, votes: dict[str, str]) -> StepResult:
        living = set(self.living_ids())
        wolf_ids = {player.id for player in self.wolves()}
        chosen: list[str] = []
        for voter, target in votes.items():
            if voter not in wolf_ids or target not in living:
                continue
            chosen.append(target)
        target = _majority(chosen)
        self.night.wolf_target = target
        if target is None:
            return StepResult(True, "狼人未能形成多数，空刀。")
        return StepResult(True, f"狼人刀口定为 {self.player(target).label}。")

    def apply_witch(self, save: bool, poison: str | None) -> StepResult:
        witch = next((player for player in self.players if player.role == Role.WITCH), None)
        if witch is None or not witch.alive:
            return StepResult(False, "女巫不能行动。")
        notes: list[str] = []
        if save and poison is not None and not self.rules.witch_double_potion:
            poison = None
            notes.append("同一夜不能既救又毒，毒药作废且不消耗。")
        if save and self.night.wolf_target == witch.id and not self.rules.witch_self_save:
            save = False
            notes.append("规则不允许女巫自救，解药不消耗。")
        if save:
            if not witch.antidote:
                notes.append("解药已经用过。")
            elif self.night.wolf_target is None:
                notes.append("今晚空刀，解药不消耗。")
            else:
                witch.antidote = False
                self.night.witch_save = True
                notes.append(f"女巫对 {self.player(self.night.wolf_target).label} 使用解药。")
        if poison is not None:
            if not witch.poison_left:
                notes.append("毒药已经用过。")
            elif poison == witch.id or not self.is_alive(poison):
                notes.append("毒药目标无效，不消耗。")
            else:
                witch.poison_left = False
                self.night.witch_poison = poison
                notes.append(f"女巫对 {self.player(poison).label} 使用毒药。")
        if not notes:
            notes.append("女巫没有使用药水。")
        return StepResult(True, "".join(notes))

    def apply_seer(self, seer_id: str, target_id: str | None) -> StepResult:
        seer = self.player(seer_id)
        if not seer.alive or seer.role != Role.SEER:
            return StepResult(False, "预言家不能行动。")
        if target_id is None or target_id == seer.id or not self.is_alive(target_id):
            return StepResult(False, "查验目标无效。")
        target = self.player(target_id)
        faction = "狼人" if target.is_wolf else "好人"
        seer.checks.append((target_id, faction))
        self.night.seer_target = target_id
        return StepResult(True, f"查验结果: {target_id} 是{faction}")

    def resolve_night(self) -> tuple[list[Death], list[str]]:
        """结算顺序：狼刀对照守护和解药，毒药单独结算且守护挡不住毒。"""
        notes: list[str] = []
        target = self.night.wolf_target
        guarded = self.night.guard_target
        saved = self.night.witch_save
        poisoned = self.night.witch_poison
        wolf_victim: str | None = None

        if target and self.is_alive(target):
            label = self.player(target).label
            both = guarded == target and saved
            if both and self.rules.guard_witch_same_target == "live":
                notes.append(f"守卫守了{label}，女巫也救了{label}，同守同救，{label}存活。")
            elif both:
                notes.append(f"守卫守了{label}，女巫也救了{label}，同守同救，{label}死亡。")
                wolf_victim = target
            elif guarded == target:
                notes.append(f"守卫守住了{label}，狼人袭击失败。")
            elif saved:
                notes.append(f"女巫解救了{label}。")
            else:
                notes.append(f"狼人袭击{label}成功。")
                wolf_victim = target
        elif target:
            notes.append("狼人选择的目标已经出局，狼人空刀。")
        else:
            notes.append("狼人空刀。")

        if guarded and guarded != target:
            notes.append(f"守卫守了{self.player(guarded).label}，刀没有落在此人身上。")

        poison_victim: str | None = None
        if poisoned and self.is_alive(poisoned):
            label = self.player(poisoned).label
            if poisoned == wolf_victim:
                notes.append(f"毒药同时落在{label}身上，按毒杀结算。")
                wolf_victim = None
                poison_victim = poisoned
            else:
                notes.append(f"女巫毒杀了{label}。")
                poison_victim = poisoned

        deaths: list[Death] = []
        if wolf_victim is not None:
            deaths.append(self._kill(wolf_victim, CAUSE_WOLF))
        if poison_victim is not None:
            deaths.append(self._kill(poison_victim, CAUSE_POISON))
        self._transition("night", "；".join(notes) if notes else "无结算")
        return deaths, notes

    def can_shoot(self, player_id: str) -> bool:
        player = self.player(player_id)
        if player.role != Role.HUNTER or player.alive:
            return False
        if player.death_cause == CAUSE_POISON:
            return self.rules.hunter_poison_shoot
        return player.death_cause in {CAUSE_WOLF, CAUSE_VOTE}

    def apply_shot(self, hunter_id: str, target_id: str | None) -> Death | None:
        if not self.can_shoot(hunter_id):
            return None
        if target_id is None or not self.is_alive(target_id):
            return None
        return self._kill(target_id, CAUSE_SHOT)

    def resolve_vote(
        self,
        votes: dict[str, str],
        *,
        allowed: set[str] | None = None,
        final: bool = False,
    ) -> VoteOutcome:
        """最高票唯一才放逐。平票按 rules.vote_tie 处理。"""
        living = set(self.living_ids())
        ballot = allowed if allowed is not None else living
        counts: Counter[str] = Counter()
        for voter, target in votes.items():
            if voter in living and target in ballot:
                counts[target] += 1
        if not counts:
            self._transition("vote", "没有有效投票")
            return VoteOutcome(None, "没有有效投票。", "没有有效投票，无人出局。")
        ranking = counts.most_common()
        tally = "，".join(f"{self.player(pid).label} {votes_for}票" for pid, votes_for in ranking)
        tied = len(ranking) > 1 and ranking[0][1] == ranking[1][1]
        if tied:
            tied_ids = [pid for pid, votes_for in ranking if votes_for == ranking[0][1]]
            if self.rules.vote_tie == "pk" and not final:
                note = f"票型 {tally}，进入 PK。"
                self._transition("vote", note)
                return VoteOutcome(None, note, f"票型：{tally}。平票，进入 PK。", tied_ids)
            public = f"票型：{tally}。平票，无人出局。"
            self._transition("vote", f"票型 {tally}，平票")
            return VoteOutcome(None, f"票型 {tally}，平票。", public)
        death = self._kill(ranking[0][0], CAUSE_VOTE)
        public = f"票型：{tally}。{death.label} 被放逐，身份是{death.role_label}。"
        self._transition("vote", f"放逐 {death.player_id}")
        return VoteOutcome(death, f"票型 {tally}，放逐 {death.label}。", public)

    def _kill(self, player_id: str, cause: str) -> Death:
        player = self.player(player_id)
        if not player.alive:
            raise RuntimeError(f"{player_id} 已经出局")
        player.alive = False
        player.death_cause = cause
        death = Death(player.id, player.label, role_label(player.role), cause)
        self.graveyard.append(death)
        self._transition("death", f"{player_id}:{cause}")
        return death

    def _transition(self, kind: str, detail: str) -> None:
        self.transitions.append({"night": self.night_number, "kind": kind, "detail": detail})


def _majority(values: list[str]) -> str | None:
    if not values:
        return None
    ranking = Counter(values).most_common()
    if len(ranking) > 1 and ranking[0][1] == ranking[1][1]:
        return None
    return ranking[0][0]
