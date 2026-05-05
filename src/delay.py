"""
delay.py — 回應延遲計算

使用 log-normal 分布模擬人類回訊的長尾特性。
大多數回應集中在 base_delay 附近，但偶爾出現 3–5 倍的長尾延遲。
"""

import math
import random
import logging
from typing import Optional

import numpy as np

from database import get_persona_state, get_relationship

logger = logging.getLogger(__name__)

# ── 各活動的基礎延遲參數 ──────────────────────────────────────────────────────
ACTIVITY_PARAMS: dict[str, dict] = {
    "commuting":      {"base": 1800, "sigma": 0.9},
    "lab":            {"base": 480,  "sigma": 0.8},
    "writing_thesis": {"base": 240,  "sigma": 1.1},  # 逃避論文時反而容易秒回
    "watching_anime": {"base": 200,  "sigma": 0.7},
    "reading":        {"base": 300,  "sigma": 0.8},
    "idle":           {"base": 75,   "sigma": 0.9},
}

# ── 心情對 sigma 的影響 ───────────────────────────────────────────────────────
MOOD_SIGMA_BONUS: dict[str, float] = {
    "focused":    -0.15,
    "content":    -0.10,
    "lazy":        0.20,
    "anxious":     0.15,
    "distracted":  0.30,
    "irritated":   0.25,
}

# ── 關係階段的「忘記回覆」機率 ────────────────────────────────────────────────
FORGET_PROB: dict[str, float] = {
    "stranger":     0.25,
    "acquaintance": 0.15,
    "friend":       0.08,
    "close":        0.04,
    "special":      0.02,
}

# ── 判斷「有趣話題」的關鍵字 ──────────────────────────────────────────────────
INTERESTING_TOPIC_KEYWORDS = [
    # 學術相關
    "多模態", "大語言模型", "LLM", "transformer", "attention",
    "paper", "論文", "研究", "模型", "訓練",
    # 加奈喜歡的作品
    "村上春樹", "卡繆", "王家衛", "今敏", "藍色恐懼",
    "重慶森林", "少女終末旅行", "電腦線圈", "攻殼", "福音戰士",
    # 觸動她的話題
    "孤獨", "記憶", "真實", "虛構", "意義",
]


def is_interesting_topic(message: str, rel: dict) -> bool:
    """
    判斷訊息是否觸及加奈有興趣的話題。
    若使用者提到她之前說過的事（known_facts），也算有趣。
    """
    import json

    # 全域關鍵字檢查
    if any(kw.lower() in message.lower() for kw in INTERESTING_TOPIC_KEYWORDS):
        return True

    # 是否提到 known_facts 裡的事
    if rel:
        known_facts = json.loads(rel.get("known_facts") or "[]")
        for fact in known_facts:
            if len(fact) > 3 and fact[:10] in message:
                return True

    return False


def calculate_reply_delay(user_id: str, message: str) -> Optional[int]:
    """
    計算回覆延遲秒數。返回 None 表示這次不回覆。

    使用 log-normal 分布：
        delay ~ LogNormal(μ, σ)
        μ = log(base_delay × affection_factor)

    大多數回應集中在 base_delay 附近，但偶爾出現 3–5 倍的長尾延遲。
    """
    state = get_persona_state()
    rel = get_relationship(user_id)

    # ── 不回覆的情況 ──────────────────────────────────────
    activity = state.get("current_activity", "idle")
    mood = state.get("current_mood", "content")
    energy = state.get("energy_level", 70)
    stage = (rel or {}).get("relationship_stage", "stranger")
    familiarity = (rel or {}).get("familiarity", 0)

    if activity == "sleeping":
        logger.info("不回覆：sleeping（體力=%d）", energy)
        return None

    if energy < 20:
        logger.info("不回覆：體力耗盡（energy=%d < 20）", energy)
        return None

    if mood == "irritated" and familiarity < 50:
        logger.info("不回覆：心情煩躁且不熟（mood=irritated, familiarity=%d）", familiarity)
        return None

    # ── 取得活動參數 ──────────────────────────────────────
    params = ACTIVITY_PARAMS.get(activity, {"base": 120, "sigma": 0.8})
    base = params["base"]
    sigma = params["sigma"]

    # ── 好感度修正：越熟悉回得越快，中位數最多縮短 55% ──
    affection = (rel or {}).get("affection", 0)
    affection_factor = 1.0 - (max(0, affection) / 100) * 0.55
    mu = math.log(base * affection_factor)

    # ── 心情修正：影響 sigma ──────────────────────────────
    sigma += MOOD_SIGMA_BONUS.get(mood, 0)

    # ── 話題興趣修正 ──────────────────────────────────────
    if rel and is_interesting_topic(message, rel):
        mu -= 0.5    # 中位數縮短約 40%
        sigma -= 0.1

    # ── 從 log-normal 抽樣 ────────────────────────────────
    delay = float(np.random.lognormal(mean=mu, sigma=max(0.3, sigma)))
    delay = float(np.clip(delay, 8, 7200))

    # ── 特殊事件：低機率的「完全忘記回」─────────────────
    forget_prob = FORGET_PROB.get(stage, 0.15)

    if random.random() < forget_prob:
        if random.random() < 0.4:
            logger.info("不回覆：漏看了（stage=%s, forget_prob=%.0f%%）", stage, forget_prob * 100)
            return None
        # 1–4 小時後才回
        delay = random.uniform(3600, 14400)
        logger.info("延遲回覆：暫時沒看到，%.0f 分鐘後才回", delay / 60)

    result = int(delay)
    logger.info(
        "計算延遲：activity=%s mood=%s energy=%d affection=%d stage=%s → %d 秒後回覆",
        activity, mood, energy, affection, stage, result
    )
    return result


def hours_since_last_interaction(user_id: str) -> float:
    """返回距離上次互動的小時數。"""
    from datetime import datetime, timezone

    rel = get_relationship(user_id)
    if not rel or not rel.get("last_interaction"):
        return float("inf")

    last = datetime.fromisoformat(rel["last_interaction"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - last).total_seconds() / 3600


def should_proactively_message(user_id: str) -> bool:
    """
    判斷加奈是否應該主動傳訊息給這個使用者。
    只有達到 friend 以上的關係才會觸發。
    """
    rel = get_relationship(user_id)
    state = get_persona_state()

    if not rel:
        return False

    stage = rel.get("relationship_stage", "stranger")
    if stage not in ("friend", "close", "special"):
        return False

    activity = state.get("current_activity", "idle")
    if activity in ("sleeping", "commuting", "lab"):
        return False

    energy = state.get("energy_level", 70)
    if energy < 30:
        return False

    if hours_since_last_interaction(user_id) < 6:
        return False

    trigger_prob = {"friend": 0.15, "close": 0.25, "special": 0.35}
    return random.random() < trigger_prob.get(stage, 0)


def determine_trigger_context(user_id: str, world_memory: list) -> str:
    """
    決定加奈主動傳訊的觸發情境，回傳給 proactive pipeline 作為 prompt 提示。
    """
    import json

    rel = get_relationship(user_id)
    state = get_persona_state()

    candidates = []

    # 剛看完什麼有感想
    activity = state.get("current_activity", "idle")
    if activity in ("watching_anime", "reading"):
        candidates.append(f"你剛才在{activity}，有點想說說看到的東西")

    # 看到外部資訊讓她想到對方
    if world_memory:
        item = world_memory[0]
        candidates.append(
            f"你看到這個：《{item.get('title', '')}》，讓你想到對方說過的事"
        )

    # 論文焦慮
    mood = state.get("current_mood", "content")
    if mood == "anxious":
        candidates.append("論文焦慮發作，你有點想找人說話（但不會直接說是這個原因）")

    # 好久沒說話
    hours = hours_since_last_interaction(user_id)
    if hours > 72:
        candidates.append(f"距離上次對話已經 {int(hours)} 小時，你有點好奇對方在幹嘛")

    if candidates:
        return random.choice(candidates)
    return "沒有特別原因，就是想說話"
