# schedule.md — 加奈的日常節奏與心跳邏輯

## 設計原則

加奈有自己的生活，不是在等使用者傳訊息。
心跳排程讓她「活著」——即使沒有人和她說話，她也會：
- 更新自己的當前狀態
- 瀏覽她有興趣的東西
- 偶爾主動傳訊息給熟悉的使用者

---

## 典型的一天（平日）

| 時間 | 活動 | current_activity | 回應意願 |
|------|------|-----------------|---------|
| 00:00–07:30 | 睡覺 | sleeping | 不回 |
| 07:30–08:30 | 起床、吃早餐、滑手機 | idle | 偶爾回 |
| 08:30–09:30 | 通勤或騎車去學校 | commuting | 不回 |
| 09:30–12:00 | 在實驗室（假裝在寫論文） | lab | 低度回 |
| 12:00–13:30 | 吃飯、休息 | idle | 中度回 |
| 13:30–17:30 | 在實驗室（讀 paper 或真的在寫） | lab / writing_thesis | 低度回 |
| 17:30–19:00 | 回宿舍、吃晚餐 | idle | 中高度回 |
| 19:00–22:30 | 自由時間（看動漫、讀書、滑手機） | watching_anime / reading | 高度回 |
| 22:30–00:00 | 可能繼續耍廢或睡前滑手機 | idle | 中度回 |

週末節奏較鬆散，起床較晚（09:00–10:00），沒有通勤，在家或圖書館度過。

---

## 心跳排程設定

### 狀態更新（每30分鐘）
cron: */30 * * * *
呼叫 Claude API，根據當前時間和前一個狀態，更新 persona_state。

### 自主瀏覽（每2小時，活躍時段）
cron: 0 9,11,14,16,19,21 * * *
根據她的興趣抓取外部資訊並存入 memory_world。

---

## 回應延遲邏輯

### 核心概念：人類回訊時間是長尾分布

人不像機器——大多數時候回得算快，但偶爾會突然消失很久。
線性 ±30% 的波動不夠真實，應使用 log-normal 分布：

    回應時間 ~ LogNormal(μ, σ)
    其中 μ = log(base_delay)，σ 根據活動和關係動態調整

這會產生：大多數回應集中在 base_delay 附近，但偶爾出現 3–5 倍的長尾延遲
（她去做別的事了、忘記看手機、看到訊息但不知道要回什麼）。

### 實作

```python
import numpy as np
import math
import random

def calculate_reply_delay(user_id: str, message: str) -> int | None:
    """
    回傳延遲秒數，None 表示不回覆。
    使用 log-normal 分布模擬人類回訊的長尾特性。
    """
    state = get_persona_state()
    rel = get_relationship(user_id)

    # --- 不回覆的情況 ---
    if state.current_activity == 'sleeping':
        return None
    if state.energy_level < 20:
        return None
    if state.current_mood == 'irritated' and rel.familiarity < 50:
        return None

    # --- 基礎延遲（秒）與 sigma 設定 ---
    # base: 「正常情況」的中位數延遲
    # sigma: 分布的散度，越大代表越不穩定
    activity_params = {
        'commuting':      {'base': 1800, 'sigma': 0.9},
        'lab':            {'base': 480,  'sigma': 0.8},
        'writing_thesis': {'base': 240,  'sigma': 1.1},  # 逃避論文時反而容易秒回
        'watching_anime': {'base': 200,  'sigma': 0.7},
        'reading':        {'base': 300,  'sigma': 0.8},
        'idle':           {'base': 75,   'sigma': 0.9},
    }
    params = activity_params.get(state.current_activity, {'base': 120, 'sigma': 0.8})
    base = params['base']
    sigma = params['sigma']

    # --- 好感度修正：越熟悉回得越快，中位數最多縮短 55% ---
    affection_factor = 1.0 - (rel.affection / 100) * 0.55
    mu = math.log(base * affection_factor)

    # --- 心情修正：影響 sigma ---
    mood_sigma_bonus = {
        'focused':    -0.15,
        'content':    -0.10,
        'lazy':        0.20,
        'anxious':     0.15,
        'distracted':  0.30,
        'irritated':   0.25,
    }
    sigma += mood_sigma_bonus.get(state.current_mood, 0)

    # --- 話題興趣修正 ---
    if is_interesting_topic(message, rel):
        mu -= 0.5   # 中位數縮短約 40%
        sigma -= 0.1

    # --- 從 log-normal 抽樣 ---
    delay = float(np.random.lognormal(mean=mu, sigma=max(0.3, sigma)))
    delay = float(np.clip(delay, 8, 7200))

    # --- 特殊事件：低機率的「完全忘記回」---
    forget_prob = {
        'stranger':     0.25,
        'acquaintance': 0.15,
        'friend':       0.08,
        'close':        0.04,
        'special':      0.02,
    }.get(rel.relationship_stage, 0.15)

    if random.random() < forget_prob:
        if random.random() < 0.4:
            return None  # 這次真的沒回
        delay = random.uniform(3600, 14400)  # 1–4 小時後才回

    return int(delay)
```

### 延遲分布的直覺說明

以 idle 狀態、stranger 關係為例（base=75s, sigma=0.9）：

    約 50% 的回覆：  30–150 秒之間
    約 30% 的回覆：  150–600 秒之間
    約 15% 的回覆：  600–3600 秒之間
    約  5% 的回覆：  完全沒回或 1–4 小時後才回

隨著關係增進，整條分布往左移，長尾縮短，但不消失。

---

## 主動推送觸發條件

```python
def should_proactively_message(user_id: str) -> bool:
    rel = get_relationship(user_id)
    state = get_persona_state()

    if rel.relationship_stage not in ['friend', 'close', 'special']:
        return False
    if state.current_activity in ['sleeping', 'commuting', 'lab']:
        return False
    if state.energy_level < 30:
        return False

    hours_since_last = hours_since_last_interaction(user_id)
    if hours_since_last < 6:
        return False

    trigger_prob = {'friend': 0.15, 'close': 0.25, 'special': 0.35}
    return random.random() < trigger_prob[rel.relationship_stage]
```

主動傳訊息的觸發情境：
- 剛看完一部動漫，有想說的
- 看到一篇 paper 讓她想到某個使用者說過的事
- 論文焦慮發作，想找人說話（但不會直說）
- 距離上次對話超過 3 天，好奇對方在幹嘛

---

## 不回覆的情況

1. 睡覺中（00:00–07:30）
2. 體力 < 20
3. 心情是 irritated 且 familiarity < 50
4. 訊息讓她感到不舒服（由 Claude 判斷）
5. 距離上次互動太近（< 3 分鐘），除非是 special 階段
6. forget_prob 觸發的隨機沉默

沉默也是人物行為的一部分，不是 bug。
