"""从玩家发言里抽出最后一条 [ACTION]。状态机只认解析结果，不认散文。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"```[a-zA-Z]*")
_ACTION_LINE = re.compile(r"\[ACTION\]\s*([^\n\r]+)", re.IGNORECASE)
_PAIR = re.compile(r"([A-Za-z_]+)\s*=\s*([^\s*]+)")
_NONE = {"none", "no", "无", "skip", "abstain", "弃票"}
_PASS = {"pass", "silent", "沉默", "跳过"}


@dataclass
class Action:
    kill: str | None = None
    check: str | None = None
    guard: str | None = None
    vote: str | None = None
    shoot: str | None = None
    save: bool | None = None
    poison: str | None = None
    vote_abstain: bool = False
    passed: bool = False
    match_count: int = 0


def canonicalize(token: str) -> str | None:
    """把 p3 / 3号 / seat3 收成 p3。弃权词返回 None。"""
    raw = token.strip().strip("。．，,.")
    if raw.lower() in _NONE:
        return None
    matched = re.fullmatch(r"[pP](\d+)", raw)
    if matched:
        return f"p{int(matched.group(1))}"
    matched = re.fullmatch(r"(\d+)号", raw)
    if matched:
        return f"p{int(matched.group(1))}"
    matched = re.fullmatch(r"seat(\d+)", raw, re.IGNORECASE)
    if matched:
        return f"p{int(matched.group(1))}"
    return None


def parse_id_list(value: str) -> list[str]:
    text = value.strip()
    if text.lower() in _NONE or text == "":
        return []
    found: list[str] = []
    for part in re.split(r"[,，]\s*", text):
        player_id = canonicalize(part.strip())
        if player_id is not None:
            found.append(player_id)
    return found


def parse_action(text: str) -> Action | None:
    """去掉代码块和加粗后，只认最后一条 [ACTION]。"""
    lines = _ACTION_LINE.findall(_plain(text))
    if not lines:
        return None
    action = Action(match_count=len(lines))
    last = lines[-1].strip().strip("*").strip()
    if last.lower() in _PASS:
        action.passed = True
        return action
    recognized = False
    for key, raw_value in _PAIR.findall(last):
        key = key.lower()
        if key == "save":
            action.save = raw_value.lower() in {"yes", "y", "true", "1", "救"}
            recognized = True
            continue
        if key == "vote" and raw_value.lower() in _NONE:
            action.vote_abstain = True
            recognized = True
            continue
        if key == "poison" and raw_value.lower() in _NONE:
            action.poison = None
            recognized = True
            continue
        player_id = canonicalize(raw_value)
        if key == "kill":
            action.kill = player_id
            recognized = True
        elif key == "check":
            action.check = player_id
            recognized = True
        elif key == "guard":
            action.guard = player_id
            recognized = True
        elif key == "vote":
            action.vote = player_id
            recognized = True
        elif key == "shoot":
            action.shoot = player_id
            recognized = True
        elif key == "poison":
            action.poison = player_id
            recognized = True
    return action if recognized else None


def _plain(text: str) -> str:
    text = _FENCE.sub("", text)
    return text.replace("**", "").replace("__", "").replace("`", "")
