# 加奈 (Kana) — Discord AI Persona Bot

林加奈，23 歲，CS 研究生，正在寫多模態 AI 的論文。
這是一個運行在 Discord 上的 AI 角色，有記憶、有狀態、會主動聯絡你。

---

## 快速開始

### 環境需求
- Python 3.10+（建議用 conda）
- Discord Bot Token（需開啟 Message Content Intent）
- Anthropic API Key（需有餘額）

### 安裝

```bash
git clone https://github.com/rowhanmuci/kana.git
cd kana
pip install -r requirements.txt
```

### 設定

複製 `.env.example` 為 `.env`，填入金鑰：

```
DISCORD_BOT_TOKEN=你的 Discord Bot Token
ANTHROPIC_API_KEY=你的 Anthropic API Key
```

Discord Developer Portal 設定：
- Bot → Privileged Gateway Intents → **Message Content Intent** 開啟

### 啟動

```bash
# Windows（雙擊或在命令提示字元）
start.bat

# Git Bash / Linux
bash start.sh
```

首次啟動會自動初始化資料庫。

---

## 狀態機

加奈有一套每天自動運行的狀態系統，記錄她的活動、心情、體力。

### 排程時間表（台北時間）

| 時間 | 事件 | 說明 |
|------|------|------|
| 02:00 | 就寢 | 強制 `activity=sleeping`，停止心跳 |
| 04:00 | 熟悉度衰減 | 長時間未互動的使用者熟悉度自然下降 |
| 07:00 | 起床 | 體力回滿 100，處理睡眠期間的未讀訊息 |
| 07:30–23:30 | 心跳（每 30 分鐘） | 更新活動、心情、體力 |
| 09, 11, 14, 16, 19, 21 點 | 完整心跳 | 心跳 + 自主瀏覽 + 主動推送 |
| 23:59 | 寫日記 | 整理今日事件，存入記憶 |

### 活動狀態（`current_activity`）

```
sleeping        凌晨睡覺中（02:00–07:00 強制）
idle            放空、什麼都沒做
commuting       通勤中
lab             在實驗室
writing_thesis  在寫論文
reading         在讀書
watching_anime  在看動漫
```

### 心情狀態（`current_mood`）

```
content     平靜
focused     專注
lazy        懶散
anxious     焦慮（論文壓力）
distracted  分心
irritated   煩躁
```

### 體力（`energy_level`，0–100）

| 活動 | 每次心跳變化 |
|------|-------------|
| idle / watching_anime / reading | -3 到 +5 |
| commuting | -5 到 -8 |
| lab | -8 到 -12 |
| writing_thesis | -10 到 -15 |
| **起床** | **強制回滿 100** |

### 回覆判斷邏輯

收到 DM 後，Bot 根據當前狀態決定要不要回、多久回：

- `sleeping` → 訊息存入待回佇列，起床後再處理
- `energy < 20` → 不回（太累）
- `mood=irritated` + 不熟 → 不回
- 其他 → 以 log-normal 分佈計算延遲秒數（模擬人類回訊習慣）

好感度越高 → 回得越快；關係越生疏 → 越容易「漏看」。

---

## 資料庫結構（SQLite）

位置：`data/kana.db`，用 [DB Browser for SQLite](https://sqlitebrowser.org/) 可以直接查看。

### `persona_state`
加奈目前的狀態，只有一筆（id=1）。

| 欄位 | 說明 |
|------|------|
| current_activity | 目前活動 |
| current_mood | 目前心情 |
| energy_level | 體力（0–100） |
| thesis_progress | 論文進度（0–100） |
| last_updated | 最後更新時間（台北時間） |

### `relationship`
與每位使用者的關係紀錄。

| 欄位 | 說明 |
|------|------|
| user_id | Discord User ID |
| display_name | 暱稱 |
| familiarity | 熟悉度（0–100） |
| affection | 好感度（-50–100） |
| relationship_stage | stranger / acquaintance / friend / close / special |
| known_facts | 加奈記得對方說過的事（JSON array） |
| inside_jokes | 你們之間的梗（JSON array） |

### `memory_conversation`
每次對話後的摘要紀錄。

| 欄位 | 說明 |
|------|------|
| summary | 這次對話的 50 字摘要 |
| emotional_moment | 特別情感事件（若有） |
| familiarity_delta | 這次對話熟悉度變化 |
| affection_delta | 這次對話好感度變化 |

### `memory_self`
加奈的自我記憶日誌，包含心跳記錄、日記、重要事件。

| tag | 說明 |
|-----|------|
| `heartbeat` | 每次心跳的狀態快照 |
| `wake_up` | 起床記錄 |
| `sleep` | 就寢記錄 |
| `diary` | 當日日記（23:59 寫） |
| `proactive` | 加奈主動傳訊的記錄 |
| `ignored_message` | 加奈主動忽略的訊息 |

### `memory_world`
加奈自主瀏覽到的外部資訊（新聞、論文、動漫）及她的反應。
> 目前 fetch 函式為 stub，尚未實作。

### `pending_messages`
睡眠期間收到的未讀訊息佇列，起床後處理。

### `media_library`
加奈的影視書籍清單（書、電影、動漫、漫畫、影集），Bot 啟動時自動填入。

---

## 專案結構

```
kana/
├── src/
│   ├── bot.py           # Discord Bot 主程式、排程
│   ├── database.py      # SQLite CRUD 操作
│   ├── memory.py        # 記憶注入、狀態更新、日記
│   ├── claude_client.py # Anthropic API 封裝
│   ├── delay.py         # 回覆延遲與主動推送判斷
│   └── browse.py        # 自主瀏覽（目前為 stub）
├── data/
│   └── kana.db          # SQLite 資料庫（不進版本控制）
├── status.py            # 診斷工具（查看 DB 狀態、即時 log）
├── start.bat            # Windows 啟動腳本
├── start.sh             # Git Bash 啟動腳本
├── requirements.txt
├── .env.example
└── persona.md           # 加奈的角色設定文件
```

---

## 診斷工具

```bash
python status.py          # 查看完整 DB 狀態快照
python status.py --log    # 即時追蹤 log（Ctrl+C 離開）
python status.py --reset  # 重建 DB（危險：清空所有資料）
```
