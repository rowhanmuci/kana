# memory_system.md — 加奈的記憶架構

## 設計原則

加奈的記憶分為三個層次：
1. **她自己的生活**（全局，不依賴使用者）
2. **她對世界的觀察**（全局，來自自主瀏覽）
3. **她和每個使用者的關係**（per-user，獨立存在）

---

## SQLite 資料庫結構

### `persona_state` — 她當下的狀態（全局唯一，隨時更新）

```sql
CREATE TABLE persona_state (
  id INTEGER PRIMARY KEY DEFAULT 1,
  current_activity TEXT,
  -- 值：'sleeping' | 'commuting' | 'lab' | 'reading' | 'watching_anime' | 'idle' | 'writing_thesis'
  current_mood TEXT,
  -- 值：'focused' | 'lazy' | 'anxious' | 'content' | 'irritated' | 'distracted'
  thesis_progress INTEGER DEFAULT 0,
  -- 0~100，非常緩慢地增長
  energy_level INTEGER DEFAULT 70,
  -- 0~100，影響回覆意願
  last_updated TIMESTAMP
);
```

### `memory_self` — 她自己的生活記憶（全局）

```sql
CREATE TABLE memory_self (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  type TEXT,
  -- 值：'watched_anime' | 'read_book' | 'read_paper' | 'thesis_work' | 'thought' | 'daily_event'
  content TEXT,
  -- 簡短描述，例：「看完了 Frieren 最新集，覺得結尾的對白有點意思」
  mood_after TEXT,
  -- 事件後的心情
  tags TEXT
  -- JSON array，例：["anime", "frieren", "感動"]
);
```

### `memory_world` — 她瀏覽到的外部資訊（全局）

```sql
CREATE TABLE memory_world (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source TEXT,
  -- 例：'arxiv' | 'news' | 'twitter' | 'ptt' | 'anilist'
  url TEXT,
  title TEXT,
  summary TEXT,
  -- 她的理解，不是原文
  her_reaction TEXT,
  -- 她對這個資訊的態度，例：「有點意思但不確定實不實用」
  tags TEXT
  -- JSON array
);
```

### `relationship` — 她和每個使用者的關係（per-user）

```sql
CREATE TABLE relationship (
  user_id TEXT PRIMARY KEY,
  -- Discord user ID
  display_name TEXT,
  first_met TIMESTAMP,
  last_interaction TIMESTAMP,
  familiarity INTEGER DEFAULT 0,
  -- 0~100，熟悉度，隨時間自然衰減
  affection INTEGER DEFAULT 0,
  -- -50~100，好感度
  relationship_stage TEXT DEFAULT 'stranger',
  -- 'stranger' | 'acquaintance' | 'friend' | 'close' | 'special'
  inside_jokes TEXT DEFAULT '[]',
  -- JSON array，只有加奈和這個使用者懂的東西
  known_facts TEXT DEFAULT '[]',
  -- JSON array，加奈記得的這個人的事
  last_mood_toward TEXT DEFAULT 'neutral'
  -- 加奈對這個使用者當下的情緒：'neutral' | 'warm' | 'curious' | 'flustered' | 'annoyed' | 'distant'
);
```

### `memory_conversation` — 對話摘要（per-user）

```sql
CREATE TABLE memory_conversation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  summary TEXT,
  -- 這次對話的摘要，100字以內
  emotional_moment TEXT,
  -- 這次對話有沒有特別的情感事件，沒有就 NULL
  familiarity_delta INTEGER DEFAULT 0,
  -- 這次對話熟悉度的變化量
  affection_delta INTEGER DEFAULT 0,
  -- 這次對話好感度的變化量
  FOREIGN KEY (user_id) REFERENCES relationship(user_id)
);
```

---

## 記憶注入格式（System Prompt 模板）

每次呼叫 Claude API 前，從資料庫撈資料，組成以下 system prompt 注入：

```
你是加奈。以下是你目前的狀態和記憶。

[當前狀態]
現在時間：{datetime}
你正在做的事：{current_activity}
你的心情：{current_mood}
體力：{energy_level}/100
論文進度：{thesis_progress}/100

[最近的生活（最近7筆 memory_self）]
{formatted_recent_memories}

[最近看到的東西（最近5筆 memory_world）]
{formatted_world_memories}

[和這個使用者的關係]
名字：{display_name}
認識多久：{days_since_first_met} 天
熟悉度：{familiarity}/100
好感度：{affection}/100
關係階段：{relationship_stage}
你記得他說過的事：{known_facts}
你們之間的梗：{inside_jokes}
你現在對他的感覺：{last_mood_toward}

[最近的對話記錄（最近5筆摘要）]
{formatted_conversation_history}

根據以上狀態，用加奈的方式回應。
```

---

## 按需讀取原則

**所有資料表的資料平常不存在於 prompt 中。**
每次呼叫 Claude API 前，才從 SQLite 動態撈取需要的部分注入。

| 資料表 | 注入時機 | 撈取量 |
|--------|---------|--------|
| `persona_state` | 每次都注入 | 全部（1筆） |
| `relationship` | 每次都注入 | 該 user 的 1 筆 |
| `memory_conversation` | 每次都注入 | 最近 5 筆摘要 |
| `memory_self` | 每次都注入 | 最近 7 筆 |
| `memory_world` | 每次都注入 | 最近 5 筆 |
| `media_library` | **按需注入** | 相關 3–5 筆 |

### media_library 的按需邏輯

```python
def query_relevant_media(message: str) -> list[dict]:
    """只在對話涉及作品時才撈，平常不注入。"""
    
    trigger_keywords = ['動漫', '電影', '書', '漫畫', '看了', '推薦',
                        '最近在', '讀', '追', '好看', '有沒有']
    
    if not any(k in message for k in trigger_keywords):
        return []  # 不注入，節省 token
    
    # 判斷要撈哪種類型
    type_map = {
        'anime': ['動漫', '番', '追'],
        'film':  ['電影', '看了'],
        'book':  ['書', '讀', '小說'],
        'manga': ['漫畫', '看了'],
    }
    matched_types = [t for t, kws in type_map.items()
                     if any(k in message for k in kws)]
    
    # 沒有明確類型就全撈高分的
    if not matched_types:
        return db.query(
            "SELECT title, type, author, score, her_note FROM media_library "
            "WHERE score >= 6 ORDER BY RANDOM() LIMIT 5"
        )
    
    placeholders = ','.join('?' * len(matched_types))
    return db.query(
        f"SELECT title, type, author, score, her_note FROM media_library "
        f"WHERE type IN ({placeholders}) AND score >= 5 "
        f"ORDER BY score DESC LIMIT 5",
        matched_types
    )
```

這樣一般對話的 system prompt 維持在約 **900–1000 tokens**，
只有聊到作品時才增加 3–5 筆資料（約 +200 tokens）。

---

## 記憶更新邏輯

### 每次對話結束後
呼叫 Claude API 進行「對話後評估」：

```
請根據以下對話，輸出 JSON：
{
  "summary": "這次對話的摘要（100字以內）",
  "emotional_moment": "有無特別情感事件（沒有填 null）",
  "familiarity_delta": 熟悉度變化（-5 到 +10），
  "affection_delta": 好感度變化（-10 到 +15），
  "new_known_facts": ["這次得知的新事實"],
  "new_inside_jokes": ["新產生的梗（如果有的話）"],
  "mood_toward_user": "加奈現在對這個使用者的感覺"
}
```

### 熟悉度自然衰減
每天 cron job：
- 超過 7 天沒互動：familiarity -= 2
- 超過 30 天沒互動：familiarity -= 5（每天）
- familiarity 最低降到 0，不會變負

### 好感度邊界
- 最高 100，最低 -50
- affection > 70 且 familiarity > 60 才能進入 'close' 階段
- affection > 85 且 familiarity > 75 才能進入 'special' 階段
- 好感度降到 -20 以下：relationship_stage 退回 'acquaintance' 或 'stranger'

---

## 關係階段行為差異

| 階段 | 主動找話題 | 回覆速度 | 說話距離感 | 分享私事 |
|------|-----------|---------|----------|---------|
| stranger | 不會 | 慢到不一定回 | 禮貌但保持距離 | 不會 |
| acquaintance | 偶爾 | 不一定 | 普通 | 偶爾提一下 |
| friend | 有時候 | 比較快 | 比較放鬆 | 會說一些 |
| close | 會 | 通常不太慢 | 不拘謹 | 會說比較私的事 |
| special | 會，而且更自然 | 明顯比較快 | 很自在，偶爾撒嬌 | 會 |
