"""牌规。默认值就是 config.yaml 里写死的那一版。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rules:
    guard_witch_same_target: str = "die"
    witch_self_save: bool = True
    witch_double_potion: bool = False
    hunter_poison_shoot: bool = False
    vote_tie: str = "no_exile"
    win_condition: str = "slaughter_town"
    action_fallback: str = "pass"
    max_speech_chars: int = 200
    history_chars: int = 4000
    speech_model: str = ""
    decision_model: str = ""

    def validate(self) -> None:
        if self.guard_witch_same_target not in {"die", "live"}:
            raise ValueError("guard_witch_same_target 只能是 die 或 live")
        if self.vote_tie not in {"no_exile", "pk"}:
            raise ValueError("vote_tie 只能是 no_exile 或 pk")
        if self.win_condition not in {"slaughter_town", "slaughter_side"}:
            raise ValueError("win_condition 只能是 slaughter_town 或 slaughter_side")
        if self.action_fallback not in {"pass", "random"}:
            raise ValueError("action_fallback 只能是 pass 或 random")
        if self.max_speech_chars < 1 or self.history_chars < 1:
            raise ValueError("字数上限必须大于 0")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_rules(path: str | Path | None) -> Rules:
    if path is None:
        return Rules()
    file = Path(path)
    if not file.exists():
        return Rules()
    data = _load_simple_yaml(file.read_text(encoding="utf-8"))
    rules = data.get("rules") or {}
    limits = data.get("limits") or {}
    models = data.get("models") or {}
    if not isinstance(rules, dict) or not isinstance(limits, dict) or not isinstance(models, dict):
        raise ValueError(f"{file} 的结构应为 rules / limits / models 三段")
    loaded = Rules(
        guard_witch_same_target=str(rules.get("guard_witch_same_target", "die")),
        witch_self_save=_as_bool(rules.get("witch_self_save", True)),
        witch_double_potion=_as_bool(rules.get("witch_double_potion", False)),
        hunter_poison_shoot=_as_bool(rules.get("hunter_poison_shoot", False)),
        vote_tie=str(rules.get("vote_tie", "no_exile")),
        win_condition=str(rules.get("win_condition", "slaughter_town")),
        action_fallback=str(rules.get("action_fallback", "pass")),
        max_speech_chars=int(limits.get("max_speech_chars", 200)),
        history_chars=int(limits.get("history_chars", 4000)),
        speech_model=str(models.get("speech") or ""),
        decision_model=str(models.get("decision") or ""),
    )
    loaded.validate()
    return loaded


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _load_simple_yaml(text: str) -> dict[str, dict[str, object]]:
    """只解析本项目这种两层映射，不引入 YAML 库。"""
    root: dict[str, dict[str, object]] = {}
    section: dict[str, object] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line[0] not in {" ", "\t"}:
            key = line.split(":", 1)[0].strip()
            section = {}
            root[key] = section
            continue
        if section is None or ":" not in line:
            raise ValueError(f"无法解析配置行: {raw}")
        key, value = line.strip().split(":", 1)
        section[key.strip()] = _scalar(value.strip())
    return root


def _scalar(value: str) -> object:
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    return value.strip('"').strip("'")
