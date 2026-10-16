"""行动解析之后的合法性校验和降级。这里不改游戏状态。"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass

from werewolf.game.parse import Action, parse_action

_BREAK = re.compile(
    r"我是\s*AI|作为(?:一个)?(?:AI|人工智能|语言模型)|系统提示|我是语言模型|根据规则我",
    re.IGNORECASE,
)


@dataclass
class ActionRecord:
    player_id: str
    phase: str
    raw: str
    parsed: dict[str, object] | None
    legal: bool
    degraded: bool
    fallback: str | None
    applied: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def sanitize_speech(text: str, max_chars: int) -> tuple[str, bool]:
    """破防或超长时改写成短句，并尽量保留最后一条行动。"""
    action = _last_action_line(text)
    broken = _BREAK.search(text) is not None
    if broken:
        kept = "我先听着。"
        if action:
            kept = f"{kept}\n{action}"
        return kept, True
    if max_chars > 0 and len(text) > max_chars:
        kept = text[:max_chars].rstrip()
        if action and action not in kept:
            kept = f"{kept}\n{action}"
        return kept, True
    return text, False


def resolve_target(
    raw: str,
    *,
    player_id: str,
    phase: str,
    legal_ids: list[str],
    rng: random.Random,
    fallback: str,
) -> tuple[str | None, ActionRecord]:
    """返回最终目标。None 表示放弃。非法或缺失时按 fallback 降级，不抛异常。"""
    action = parse_action(raw)
    legal = set(legal_ids)
    chosen = _chosen_target(action, phase)
    degraded = False
    used: str | None = None
    explicit_pass = action is not None and (
        action.passed
        or action.vote_abstain
        or (chosen is None and re.search(rf"{phase}\s*=\s*(?:none|no|无|pass|abstain|弃票)", raw, re.I))
    )
    if explicit_pass:
        chosen = None
        applied = "pass"
        ok = True
    elif chosen in legal:
        applied = chosen or "pass"
        ok = True
    else:
        degraded = True
        ok = False
        if fallback == "random" and legal_ids:
            chosen = rng.choice(list(legal_ids))
            used = "random"
            applied = chosen
        else:
            chosen = None
            used = "pass"
            applied = "pass"
    record = ActionRecord(
        player_id=player_id,
        phase=phase,
        raw=raw,
        parsed=_parsed(action),
        legal=ok,
        degraded=degraded,
        fallback=used,
        applied=applied,
    )
    return chosen, record


def _chosen_target(action: Action | None, phase: str) -> str | None:
    if action is None:
        return None
    if phase == "kill":
        return action.kill
    if phase == "guard":
        return action.guard
    if phase == "check":
        return action.check
    if phase == "vote":
        return None if action.vote_abstain else action.vote
    if phase == "shoot":
        return action.shoot
    return None


def _parsed(action: Action | None) -> dict[str, object] | None:
    if action is None:
        return None
    return asdict(action)


def _last_action_line(text: str) -> str:
    matches = re.findall(r"\[ACTION\][^\n\r]*", text, flags=re.IGNORECASE)
    return matches[-1].strip() if matches else ""
