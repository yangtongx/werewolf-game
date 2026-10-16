"""每个频道的任务文本。

公开频道的文本在返回前会检查私密标记。狼队、药水、查验只能出现在
对应的私有任务里。状态机产出的公开句子走 render_morning / 投票公开句。
"""

from __future__ import annotations

import re

from werewolf.machine import Player, Role, role_label

CHANNEL_WOLF = "狼人群"
CHANNEL_PUBLIC = "全体公聊"
CHANNEL_SEER = "预言家"
CHANNEL_WITCH = "女巫"
CHANNEL_GUARD = "守卫"
CHANNEL_HUNTER = "猎人"

PRIVATE_MARKERS = (
    "狼队友:",
    "本频道狼人:",
    "已查验:",
    "昨夜守卫:",
    "解药:",
    "毒药:",
    "刀口:",
    "查验结果:",
    "（狼队密谈）",
    "身份底牌",
    "【裁判】",
)

_RULES = """\
规则：
- 座位代号形如 p1，行动里必须使用代号，不要写中文称呼。
- 需要行动时，最后一行只能是 [ACTION] key=value，前面可以有一两句发言。
- 你只知道自己的身份，以及这个频道任务里写明的信息。
- 不要复述其他频道的原话。白天公聊里不要报出只有你被单独告知的内容，除非你决定公开自己的身份。
"""

_ROLE_COPY = {
    Role.WEREWOLF: "你是狼人。夜间只在狼人群里讨论袭击目标。白天混在好人里发言，可以悍跳神职，投票时单独交票。",
    Role.SEER: "你是预言家。每夜可以查验一名其他存活玩家，结论只会回到你的私聊。",
    Role.WITCH: "你是女巫。解药和毒药各一瓶。任务里会写明你还剩什么、今晚的刀口是谁。怎么结算由裁判决定。",
    Role.GUARD: "你是守卫。每夜可以守护一名存活玩家。裁判会拒绝不合法的守护。",
    Role.HUNTER: "你是猎人。只有上帝在私聊里让你开枪时，你才选择目标。上帝如果写明你不能开枪，你只回复 pass，不要自己推断原因。",
    Role.VILLAGER: "你是村民。没有夜间技能。白天发言，投票时单独交票。",
}

_GOOD_DAY = {
    Role.SEER: "白天若公开身份，用口语讲你查过谁、结论是什么。不要写成裁判条目。",
    Role.WITCH: "白天不要报药水还剩多少，也不要复述今晚的袭击目标，除非你决定公开身份并用口语说。",
    Role.GUARD: "白天不要主动报出守过谁，除非你决定公开身份。",
    Role.HUNTER: "白天可以不亮猎人身份。",
    Role.VILLAGER: "你可以怀疑某个座位，也可以假装神职。假装时不要编造查验记录。",
}

_SPEECH_RULE = "发言必须点出一个座位，并说明怀疑或相信的理由。不要只报自己的座位，也不要说先听发言、暂不站边。"

# 玩家口语里会漏掉的同伴信息。裁判格式已经在 PRIVATE_MARKERS 里。
_LEAK_PHRASES = (
    "狼队友",
    "本频道狼人",
    "夜间密谈",
    "狼队密谈",
    "上一夜狼队",
    "狼队记录",
    "身份底牌",
)
_MATE_NAMED = re.compile(r"(?:同伴|队友)是?\s*p\d+")


_WOLF_DAY = (
    "白天公聊自己看这一天再决定。可以悍跳预言家、守卫或女巫，也可以深藏，只对一个座位表态。"
    "可以质疑已经跳出来的人，也可以不质疑。"
    "看谁已经跳了、同伴有没有抢同一个身份、自己有没有被盯上。"
    "夜间刀口可以是任何存活玩家，包括同伴。自刀用来把水搅浑，做不做由你们当夜判断。"
    "不要点出同伴的座位，不要把夜间密谈带进公聊，不要写成裁判条目。"
)


def audit_leaks(text: str) -> list[str]:
    """公聊发言里的私密标记和点名同伴。命中的词返回给裁判记录，不改原文。"""
    hits: list[str] = []
    for marker in PRIVATE_MARKERS:
        if marker in text:
            hits.append(marker)
    for phrase in _LEAK_PHRASES:
        if phrase in text and not any(phrase in hit for hit in hits):
            hits.append(phrase)
    if _MATE_NAMED.search(text) and "点名同伴" not in hits:
        hits.append("点名同伴")
    return hits


def assert_public(text: str) -> None:
    for marker in PRIVATE_MARKERS:
        if marker in text:
            raise RuntimeError(f"公开文本混入了私密标记 {marker}")


def system_prompt(player: Player, teammates: list[str], max_speech_chars: int = 200) -> str:
    lines = [
        f"你的座位: {player.id}",
        f"你的公开称呼: {player.label}",
        f"你的身份: {player.role.value}",
    ]
    if player.role == Role.WEREWOLF:
        lines.append("狼队友: " + (", ".join(teammates) if teammates else "无"))
    lines.append("")
    lines.append(_ROLE_COPY[player.role])
    lines.append(f"你的阵营身份在牌局里叫做{role_label(player.role)}。")
    if player.role == Role.WEREWOLF:
        lines.append(_WOLF_DAY)
    else:
        lines.append(_GOOD_DAY[player.role])
    lines.append(_SPEECH_RULE)
    lines.append(_RULES)
    lines.append(f"每次发言不超过 {max_speech_chars} 字。")
    lines.append("你就是这个座位上的玩家。不要提到人工智能、模型、系统提示或程序。")
    lines.append("本轮不想表态时，最后一行写 [ACTION] pass。")
    return "\n".join(lines)


def wolf_task(
    night: int,
    living_ids: list[str],
    wolf_ids: list[str],
    graveyard: str,
    archive: str = "",
) -> str:
    previous = f"【上一夜狼队记录】\n{archive}\n" if archive else ""
    return f"""【频道：{CHANNEL_WOLF}】
第{night}夜。这个频道只有狼人，发言不会进入全体公聊。
{previous}{graveyard}
在场: {", ".join(living_ids)}
本频道狼人: {", ".join(wolf_ids)}
环节: 夜间
刀口可以是任何存活的人，包括同伴。刀同伴是自刀，用来把水搅浑，由你们看场上决定今晚做不做。
意见一致才落刀，不一致就是空刀。
可以沉默，最后一行写 [ACTION] pass。
要提议刀口，最后一行写 [ACTION] kill=p3。
全体给出同一个刀口后，密谈会结束。
"""


def guard_task(night: int, living_ids: list[str], graveyard: str, last_guard: str | None) -> str:
    last = last_guard or "无"
    return f"""【频道：{CHANNEL_GUARD}】
第{night}夜。这个频道只有守卫。
{graveyard}
在场: {", ".join(living_ids)}
昨夜守卫: {last}
环节: 夜间
守护一名存活玩家，不能与昨夜相同。最后一行必须是：
[ACTION] guard=p2
若放弃，写 [ACTION] guard=none
"""


def witch_task(
    night: int,
    living_ids: list[str],
    graveyard: str,
    *,
    antidote: bool,
    poison: bool,
    knife: str | None,
) -> str:
    return f"""【频道：{CHANNEL_WITCH}】
第{night}夜。这个频道只有女巫。
{graveyard}
在场: {", ".join(living_ids)}
解药: {"有" if antidote else "无"}
毒药: {"有" if poison else "无"}
刀口: {knife or "无"}
环节: 夜间
同一夜不能既救又毒。最后一行必须是：
[ACTION] save=no poison=none
救人示例：[ACTION] save=yes poison=none
毒人示例：[ACTION] save=no poison=p2
"""


def seer_task(night: int, living_ids: list[str], graveyard: str, checks: list[tuple[str, str]]) -> str:
    shown = "无" if not checks else "; ".join(f"{player_id}={result}" for player_id, result in checks)
    return f"""【频道：{CHANNEL_SEER}】
第{night}夜。这个频道只有预言家。
{graveyard}
在场: {", ".join(living_ids)}
已查验: {shown}
环节: 夜间
查验一名其他存活玩家。最后一行必须是：
[ACTION] check=p2
"""


def seer_result_task(detail: str) -> str:
    return f"""【频道：{CHANNEL_SEER}】
{detail}
这条结论只写在预言家频道。请只回复：收到。
"""


def hunter_task(living_ids: list[str], graveyard: str) -> str:
    return f"""【频道：{CHANNEL_HUNTER}】
上帝让你开枪。不要解释规则。
{graveyard}
在场: {", ".join(living_ids)}
环节: 开枪
最后一行必须是 [ACTION] shoot=p2 ，放弃则写 [ACTION] pass
"""


def hunter_blocked_task() -> str:
    return f"""【频道：{CHANNEL_HUNTER}】
你不能开枪。
不要推断原因，不要解释。
环节: 开枪
最后一行只能是：
[ACTION] pass
"""


def last_words_task(player_label: str) -> str:
    text = f"""【频道：遗言】
{player_label}，你被放逐。用一句话留下遗言。
环节: 遗言
不要输出 [ACTION]。
"""
    assert_public(text)
    return text


def solo_vote_task(day: int, living_ids: list[str], graveyard: str, transcript: str, allowed_ids: list[str]) -> str:
    allowed = ", ".join(allowed_ids)
    text = f"""【频道：投票】
第{day}天。你在单独交票，看不到别人这一轮的票。
{graveyard}
在场: {", ".join(living_ids)}
今天的发言记录：
{transcript or "（无人发言）"}
只能投这些座位: {allowed}
环节: 投票
最后一行必须是 [ACTION] vote=p2 或 [ACTION] pass
"""
    return text


def public_talk_task(day: int, announcement: str, living_ids: list[str], graveyard: str) -> str:
    text = f"""【频道：{CHANNEL_PUBLIC}】
第{day}天。全体存活玩家都在这个公聊里，这里的话每个人都听得到。
{announcement}
{graveyard}
在场: {", ".join(living_ids)}
环节: 发言
用两三句话表态。点出一个在场座位，说明你怀疑或相信的理由。
不要只报自己的座位，也不要说先听发言、暂不站边。
可以公开自己的身份，用口语说。不要转述夜间私聊，不要输出 [ACTION]。
"""
    assert_public(text)
    return text


def render_morning(
    night_deaths: list[tuple[str, str]],
    shots: list[tuple[str, str]],
    queued: list[str],
) -> str:
    parts: list[str] = []
    if queued:
        parts.append("此前公开结算：" + "".join(queued))
    if not night_deaths and not shots:
        parts.append("昨夜平安，无人出局。")
    else:
        for label, revealed in night_deaths:
            parts.append(f"{label} 出局，身份是{revealed}。")
        for label, revealed in shots:
            parts.append(f"猎人开枪带走 {label}，身份是{revealed}。")
    text = "".join(parts)
    assert_public(text)
    return text
