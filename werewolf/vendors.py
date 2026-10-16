"""厂家模型。人数是几，就从这份名单里取前几家，再按本局种子洗给座位。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from werewolf.rules import _load_simple_yaml


@dataclass(frozen=True)
class Vendor:
    id: str
    label: str
    model: str
    base_url: str
    api_key_env: str

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "model": self.model}


# 顺序就是取用顺序：6 人用前 6 家，9 人用前 9 家，12 人用全部。
DEFAULT_VENDORS: tuple[Vendor, ...] = (
    Vendor("openai", "OpenAI", "gpt-6-astra", "", "OPENAI_API_KEY"),
    Vendor("deepseek", "DeepSeek", "deepseek-v4-pro", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    Vendor("qwen", "通义千问", "qwen3.8-max", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    Vendor("zhipu", "智谱", "glm-5.3", "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY"),
    Vendor("moonshot", "月之暗面", "kimi-k3", "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY"),
    Vendor("gemini", "Gemini", "gemini-3.8-flash", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    Vendor("minimax", "MiniMax", "MiniMax-M3", "https://api.minimaxi.com/v1", "MINIMAX_API_KEY"),
    Vendor("doubao", "豆包", "doubao-seed-2.0-pro", "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY"),
    Vendor("baichuan", "百川", "Baichuan4", "https://api.baichuan-ai.com/v1", "BAICHUAN_API_KEY"),
    Vendor("stepfun", "阶跃星辰", "step-3.7-flash", "https://api.stepfun.com/v1", "STEPFUN_API_KEY"),
    Vendor("hunyuan", "腾讯混元", "hy3", "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY"),
    Vendor("spark", "讯飞星火", "spark-x2.5", "https://spark-api-open.xf-yun.com/v1", "SPARK_API_KEY"),
)


def default_vendors() -> list[Vendor]:
    return list(DEFAULT_VENDORS)


def load_vendors(path: str | Path | None) -> list[Vendor]:
    if path is None:
        return default_vendors()
    file = Path(path)
    if not file.exists():
        return default_vendors()
    raw = _load_simple_yaml(file.read_text(encoding="utf-8")).get("vendors")
    if not raw:
        return default_vendors()
    if not isinstance(raw, list):
        raise ValueError("vendors 应该是列表")
    vendors: list[Vendor] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("vendors 的每一项都要写 id、label、model、api_key_env")
        vendor = Vendor(
            id=str(item.get("id") or "").strip(),
            label=str(item.get("label") or item.get("id") or "").strip(),
            model=str(item.get("model") or "").strip(),
            base_url=str(item.get("base_url") or "").strip(),
            api_key_env=str(item.get("api_key_env") or "").strip(),
        )
        if not vendor.id or not vendor.model or not vendor.api_key_env:
            raise ValueError(f"厂家配置不完整: {item}")
        vendors.append(vendor)
    ids = [vendor.id for vendor in vendors]
    if len(ids) != len(set(ids)):
        raise ValueError("vendors 里的 id 不能重复")
    return vendors


def assign_vendors(player_ids: list[str], vendors: list[Vendor], seed: int) -> dict[str, Vendor]:
    """取前 N 家，用本局种子打乱后按座位顺序发出去。同一厂家不会出现两次。"""
    count = len(player_ids)
    if len(vendors) < count:
        raise ValueError(f"这一局有 {count} 人，厂家模型只有 {len(vendors)} 个")
    pool = list(vendors[:count])
    random.Random(seed).shuffle(pool)
    return {player_id: vendor for player_id, vendor in zip(player_ids, pool)}
