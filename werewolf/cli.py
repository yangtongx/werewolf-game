"""命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from werewolf.play.director import play_match
from werewolf.game.machine import PRESETS
from werewolf.game.rules import load_rules
from werewolf.play.vendors import load_vendors


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    _load_env(Path(".env"))
    logging.getLogger("autogen_core").setLevel(logging.WARNING)
    logging.getLogger("autogen_agentchat").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="基于 AutoGen 多 GroupChat 的狼人杀")
    parser.add_argument("--mock", action="store_true", help="不调用模型，用确定性发言跑完整局")
    parser.add_argument("--preset", default="classic6", choices=sorted(PRESETS))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="保留参数。玩家模型改由 config.yaml 的 vendors 按人数洗牌",
    )
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL") or None)
    parser.add_argument("--talk-rounds", type=int, default=1, help="白天每人发言几轮")
    parser.add_argument("--wolf-rounds", type=int, default=2, help="夜里每个狼人发言几轮")
    parser.add_argument("--config", default="config.yaml", help="牌规、字数和模型分级。文件不存在时用默认规则")
    parser.add_argument("--log", default="logs/latest.json", help="对局记录。空字符串表示不写文件")
    parser.add_argument("--web", action="store_true", help="打开牌桌页面，而不是直接跑一局")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.web:
        from werewolf.web import serve

        serve(args.host, args.port, args.config)
        return
    rules = load_rules(args.config)
    vendors = load_vendors(args.config)

    result = asyncio.run(
        play_match(
            preset=args.preset,
            seed=args.seed,
            mock=args.mock,
            model=args.model,
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=args.base_url,
            talk_rounds=args.talk_rounds,
            wolf_rounds=args.wolf_rounds,
            rules=rules,
            vendors=vendors,
        )
    )
    if args.log:
        path = Path(args.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n记录已写入 {path}")


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    main()
