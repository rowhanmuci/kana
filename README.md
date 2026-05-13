# 加奈 (Kana) — Discord AI Persona Bot

林加奈，23 歲，國立臺灣科技大學資工所碩士二年級，研究多模態 AI。
這是一個運行在 Discord 上的 AI 角色，有持續記憶、自動運轉的狀態機、主動聯絡能力，並附帶一套完整的 Threads 社群帳號管理流程。

---

## 快速開始

### 環境需求

- Python 3.10+（建議用 conda）
- Discord Bot Token（需開啟 Message Content Intent）
- Anthropic API Key（claude-opus-4-6 / claude-sonnet-4-6）

### 安裝

```bash
git clone https://github.com/rowhanmuci/kana.git
cd kana
pip install -r requirements.txt
```

### 設定

複製 `.env.example` 為 `.env`，填入金鑰：

```env
# 必填
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key

# 選填：Threads 發文功能
THREADS_ACCESS_TOKEN=your_threads_token
THREADS_USER_ID=your_threads_user_id

# 選填：管理員審核頻道
ADMIN_CHANNEL_ID=your_discord_channel_id
OWNER_USER_ID=your_discord_user_id

# 選填：YouTube 留言抓取
YOUTUBE_API_KEY=your_youtube_api_key
```

Discord Developer Portal 設定：Bot → Privileged Gateway Intents → **Message Content Intent** 開啟

### 啟動

```bash
# Windows
start.bat

# Git Bash / Linux
bash start.sh
```

首次啟動自動初始化資料庫。

---

## 核心功能

### Discord 對話

- 只回應 **DM**（私訊），公開伺服器頻道不處理
- 訊息緩衝合併：延遲期間累積的訊息一起回
- 根據當前狀態（活動、心情、體力）決定要不要回、多久回
- 對話歷史：每次對話完整儲存至 `message_log`（含時間戳），作為下次對話 context，讓加奈能正確推算對方說「明天」「今天」等相對時間
- 對話後自動更新關係值、寫摘要、更新記憶

### 狀態機

加奈有一套每天自動運行的狀態系統，記錄她的活動、心情、體力（0–100）。

#### 排程時間表（台北時間）

| 時間 | 事件 | 說明 |
|------|------|------|
| 02:00 | 就寢 | `activity=sleeping`，體力停止消耗 |
| 04:00 | 熟悉度衰減 | 長時間未互動的使用者熟悉度自然下降 |
| 07:00 | 起床 | 體力回滿 100，處理睡眠期間的未讀訊息 |
| 07:30–23:30（每 30 分鐘） | 輕量心跳 | 更新活動、心情、體力 |
| 09, 11, 14, 16, 19, 21 點 | 完整心跳 | 心跳 + 自主瀏覽 + Threads 草稿 + 主動推送 + 消化承諾 |
| 20:00 | ZUTOMAYO 倒數 | 生成演唱會倒數貼文草稿，送管理員審核 |
| 23:59 | 日記 + Reflect-Evolve | 整理今日事件寫日記，接著執行 GLA Reflect-Evolve 更新動態人格 |

#### 活動狀態（`current_activity`）

| 值 | 說明 |
|----|------|
| `sleeping` | 凌晨睡覺中（02:00–07:00 強制） |
| `idle` | 放空 |
| `commuting` | 通勤中 |
| `lab` | 在實驗室 |
| `writing_thesis` | 在寫論文 |
| `reading` | 在讀書 |
| `watching_anime` | 在看動漫 |
| `listening_music` | 在聽音樂（由 dynamic_interests 驅動，heartbeat 可選） |

#### 心情狀態（`current_mood`）

`content` / `focused` / `lazy` / `anxious` / `distracted` / `irritated`

### 回覆判斷邏輯

收到 DM 後，Bot 根據當前狀態決定：

- `sleeping` → 存入 `pending_messages`，起床後再處理
- `energy < 20` → 不回（太累）
- `mood=irritated` + 熟悉度 < 50 → 不回
- 其他 → 以 log-normal 分佈計算延遲秒數

延遲受活動類型、心情（sigma 修正）、好感度（中位數最多縮短 55%）影響。

---

## GLA Reflect-Evolve 引擎

加奈每天 23:59 在寫完日記後，執行一次 Perceive-Reflect-Evolve 循環，讓人格根據真實體驗緩慢演化。

### 流程

```
當日 memory_self（非 heartbeat / daily_event）
+ memory_world 最近 10 筆
        ↓
    [Reflect] 第一人稱自我觀察（150 字，儲存為 type=reflection）
        ↓
    [Evolve] 結構化更新 dynamic_persona
        ↓
  下次對話注入 [加奈近期的樣子] section
```

### 演化原則

興趣的漂移由**真實體驗**驅動（對話觸動、認真分析過的音樂、讀過的東西），被動接收的瀏覽結果不直接成為興趣。Reflect 階段對無感的資訊直接忽略，Evolve 階段只收錄有主動感受的內容。

### `dynamic_persona` 欄位

| 欄位 | 說明 | 上限 |
|------|------|------|
| `current_obsessions` | 最近著迷鑽研的事（具體） | 3 項 |
| `dynamic_interests` | 當前興趣方向 | 5 項 |
| `tone_tendencies` | 最近說話傾向（20 字） | — |
| `recent_sensitivities` | 最近在意或敏感的事 | 3 項 |

---

## 承諾任務佇列（`todo_commitments`）

對話中加奈說「之後會去聽/看/查」的東西，自動存入待辦佇列，完整心跳時消化：

| 類型 | 消化方式 |
|------|----------|
| `music` + YouTube URL | `analyze_youtube_audio()` 真實音訊分析 |
| `url` | fetch 頁面內容後生成感想 |
| `anime` / `book` / 其他 | web search 查資料後生成感想（措辭誠實：「查了一下」） |

消化完成後，加奈會主動傳訊息給當初提到這件事的 user，分享感想。傳出的訊息同步寫入 `message_log`，不造成對話斷層。

---

## Threads 整合

加奈在 Threads 有帳號（`@kana_ssisis`），會自主發文。所有發文皆先送**管理員審核**才真正發出。

### 自主發文（每次完整心跳觸發）

根據當前狀態、最近瀏覽結果、記憶生成日常碎念類貼文，隨機決定是否生成（有新資訊時機率較高）。每日上限 3 篇。

### ZUTOMAYO 演唱會倒數（每日 20:00）

演唱會前 9 天每天生成一篇介紹指定曲目的長文（350–800 字），包含：

- 歌詞雙語對照
- 加奈的樂迷視角評析
- 音訊分析資料輔助（調性、音域、BPM、鼓組特性等）

### 音訊分析（`audio.py`）

對 YouTube 音訊執行多階段分析，供 Threads 文章使用：

| 階段 | 工具 | 輸出 |
|------|------|------|
| 1. 整體分析 | librosa (CPU) | 調性、BPM、音域（fallback）、能量分佈 |
| 2. 音軌分離 | Demucs v4.0.1 htdemucs (GPU) | 鼓組特性、貝斯主根音、器樂層次、人聲音域與動態 |
| 3. 語音轉錄 | Whisper large-v3 (GPU) | 歌詞時間結構輔助 |

各音軌設有能量門檻（鼓 < 6%、貝斯 < 8%、器樂 < 5% mix 比例視為靜音），避免描述實際上不存在的樂器。任何階段失敗都不影響其他階段，回傳已取得的部分結果。

---

## 管理員頻道

設定 `ADMIN_CHANNEL_ID` 和 `OWNER_USER_ID` 後啟用審核流程：

```
草稿生成 → 發到管理員頻道（附 ✅ / ❌ 反應）
  ✅ → 直接發出至 Threads
  ❌ → Bot 詢問修改建議，管理員回覆後重新生成
  
直接在頻道輸入指示 → 生成後送審
含「留言」等關鍵字 → 顯示貼文留言，點 ✉️ 讓加奈生成回覆草稿
```

草稿修改依原稿長度自動選模型：原稿 > 300 字用 Opus（2500 tokens），短文用 Haiku（300 tokens）。長草稿自動拆成多則 Discord 訊息，完整內容存入 DB 不截斷。

---

## 自主瀏覽（`browse.py`）

完整心跳時根據 `dynamic_interests` 動態路由，過濾重複 URL 後存入 `memory_world`：

| 條件 | 來源 |
|------|------|
| interests 含 AI / research / multimodal 等詞 | arXiv（多模態 AI 論文） |
| interests 含 anime / manga / 動漫 等詞 | AniList（趨勢 / 播出中） |
| 其他興趣 | Claude web_search（以英文 / 日文優先搜尋） |

若 `dynamic_interests` 為空，回落原本時段排程（11–13 arXiv、14–16 AniList 趨勢、19–21 AniList 更新中）。

---

## Claude API 整合

所有 LLM 呼叫透過 `claude_client.py` 統一管理：

| 類型 | 模型 | max_tokens | 用途 |
|------|------|-----------|------|
| `chat` | claude-opus-4-6 | 500 | DM 對話回覆（含 web search） |
| `social` | claude-opus-4-6 | 2500 | Threads 長文生成（含 web search） |
| `social_short` | claude-haiku-4-5 | 300 | 留言回覆、短草稿修改 |
| `commitment` | claude-opus-4-6 | 400 | 承諾消化（音訊感想、閱讀感想） |
| `memory` | claude-sonnet-4-6 | 400 | 對話摘要、記憶更新、Reflect-Evolve |
| `browse` | claude-sonnet-4-6 | 300 | 瀏覽資訊消化 |
| `interest_browse` | claude-sonnet-4-6 | 600 | 動態興趣 web search |
| `heartbeat` | claude-haiku-4-5 | 200 | 狀態心跳更新 |
| `proactive` | claude-haiku-4-5 | 200 | 主動推送訊息生成 |
| `diary` | claude-haiku-4-5 | 800 | 日記生成 |

PERSONA_BASE（角色設定）啟用 Prompt Cache，所有 chat 類呼叫共用同一份緩存。

---

## 資料庫結構（SQLite）

位置：`data/kana.db`，用 [DB Browser for SQLite](https://sqlitebrowser.org/) 可以直接查看。

### `persona_state`
加奈目前的狀態（只有一筆，id=1）。

| 欄位 | 說明 |
|------|------|
| current_activity | 目前活動 |
| current_mood | 目前心情 |
| energy_level | 體力（0–100） |

### `dynamic_persona`
GLA Evolve 產生的動態人格狀態（只有一筆，id=1），每日 23:59 更新。

| 欄位 | 說明 |
|------|------|
| current_obsessions | 最近著迷鑽研的事（JSON 陣列） |
| dynamic_interests | 當前興趣方向（JSON 陣列），驅動自主瀏覽路由 |
| tone_tendencies | 最近說話傾向（文字） |
| recent_sensitivities | 最近在意的事（JSON 陣列） |

### `relationship`
與每位使用者的關係紀錄。

| 欄位 | 說明 |
|------|------|
| familiarity | 熟悉度（0–1000） |
| affection | 好感度（-500–1000） |
| relationship_stage | `stranger` / `acquaintance` / `friend` / `close` / `special` |
| known_facts | 加奈記得對方說過的事（JSON） |
| inside_jokes | 兩人之間的梗（JSON） |

關係階段門檻：`close`（affection > 70 且 familiarity > 60）、`special`（affection > 100 且 familiarity > 100）

### `memory_conversation`
每次對話後的摘要與情感事件。

### `message_log`
每次對話的原始訊息紀錄（role: user / assistant），含傳送時間戳，作為對話歷史 context，取最近 20 則。主動推送訊息也寫入此表，避免對話斷層。

### `memory_self`
加奈的自我記憶日誌。

| type | 說明 |
|------|------|
| `heartbeat` | 每次狀態心跳的快照 |
| `diary` | 每日日記（23:59） |
| `reflection` | GLA Reflect 產生的第一人稱自我觀察（23:59，供 Evolve 消化） |
| `daily_event` | 重要事件記錄 |
| `threads_post` | Threads 自主發文記錄 |
| `threads_zutomayo` | ZUTOMAYO 倒數文記錄 |

### `memory_world`
自主瀏覽到的外部資訊（arXiv 論文、AniList 動漫、web search）及加奈的第一人稱反應，依 URL 去重。

### `todo_commitments`
加奈在對話中承諾「之後會去聽/看/查」的事項佇列。

| 欄位 | 說明 |
|------|------|
| type | `music` / `anime` / `book` / `url` / `other` |
| title | 作品或連結名稱 |
| url | 若有連結 |
| status | `pending` / `done` |

### `media_library`
加奈的個人書單與影視清單（書、電影、動漫、漫畫、影集），供「你最近有在看什麼嗎？」類對話使用。

### `pending_messages`
睡眠或低體力期間收到的未讀訊息佇列，起床後依邏輯決定是否回覆。

### `pending_drafts`
Threads 草稿審核佇列，記錄每份草稿的狀態（`pending` / `posted` / `rejected`）。

---

## 專案結構

```
kana/
├── src/
│   ├── bot.py           # Discord Bot 主程式、事件處理、排程
│   ├── database.py      # SQLite CRUD（10 張資料表）
│   ├── memory.py        # System prompt 組裝、對話記憶更新、日記、Reflect-Evolve
│   ├── claude_client.py # Anthropic API 封裝（prompt cache、web search）
│   ├── delay.py         # 回覆延遲計算、主動推送判斷
│   ├── browse.py        # 自主瀏覽（arXiv、AniList、動態興趣 web search）
│   ├── threads.py       # Threads API、發文生成、ZUTOMAYO 倒數
│   ├── audio.py         # YouTube 音訊分析（librosa / Demucs / Whisper）
│   └── admin.py         # 管理員頻道草稿審核流程
├── data/
│   ├── kana.db          # SQLite 資料庫（不進版本控制）
│   ├── thesis.md        # 論文進度追蹤（注入 system prompt）
│   └── threads_style.md # Threads 風格指引與修正紀錄
├── status.py            # 診斷工具
├── start.bat            # Windows 啟動腳本
├── start.sh             # Git Bash / Linux 啟動腳本
├── persona.md           # 角色設定文件（詳細版，供參考用）
├── requirements.txt
└── .env.example
```

---

## 診斷工具

```bash
python status.py          # 查看完整 DB 狀態快照
python status.py --log    # 即時追蹤 log（Ctrl+C 離開）
python status.py --reset  # 重建 DB（危險：清空所有資料）
```
