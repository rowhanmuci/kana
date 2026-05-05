# HANDOFF.md — 加奈專案交接文件

這份文件是 Cowork 的入口。請按照以下順序理解專案，然後執行「當前任務」。

---

## 專案目標

建立一個名叫「加奈」的 AI 數位人物，運行在 Discord Bot 上。
她有自己的生活節奏、記憶系統，以及對每個使用者獨立的關係狀態。
目標是讓她的行為越像真實人類越好。

---

## 目錄結構

```
kana-project/
├── HANDOFF.md              ← 你現在在這裡
├── docs/
│   ├── persona.md          ← 加奈是誰、個性、說話風格
│   ├── media_taste.md      ← 加奈看過的作品清單與品味特徵（含 SQL schema）
│   ├── memory_system.md    ← SQLite 資料庫 schema + 記憶注入格式
│   ├── schedule.md         ← 日常節奏、心跳邏輯、延遲計算（log-normal）
│   └── discord_skill.md    ← Discord Bot 串接架構與程式碼骨架
├── src/
│   ├── bot.py              ← Discord Bot 主程式（待建立）
│   ├── database.py         ← SQLite 操作封裝（待建立）
│   ├── memory.py           ← 記憶注入與更新邏輯（待建立）
│   ├── delay.py            ← 回應延遲計算（待建立）
│   ├── browse.py           ← 自主瀏覽邏輯（待建立）
│   └── claude_client.py    ← Anthropic API 封裝（待建立）
├── data/
│   └── kana.db             ← SQLite 資料庫（執行時自動建立）
└── requirements.txt        ← 待建立
```

---

## 閱讀順序

1. `docs/persona.md` — 先理解加奈是誰
2. `docs/media_taste.md` — 理解她的品味，以及 media_library 資料表
3. `docs/memory_system.md` — 理解記憶架構和資料庫結構
4. `docs/schedule.md` — 理解她的日常節奏和延遲邏輯
5. `docs/discord_skill.md` — 理解 Discord 串接的程式碼架構

---

## 當前任務

**目標：建立可以跑起來的最小可行版本（MVP）**

### Phase 1：基礎建設
1. 建立 `requirements.txt`
2. 建立 `src/database.py`：根據 `memory_system.md` 的 schema 建立 SQLite 資料表，提供 CRUD 函數；同時建立 `media_library` 表（schema 見 `media_taste.md`），並將 `media_taste.md` 的作品資料批次寫入
3. 建立 `src/claude_client.py`：封裝 Anthropic API 呼叫，接受 system prompt + messages，依呼叫類型自動選擇模型（見模型使用策略）

### Phase 2：核心邏輯
4. 建立 `src/memory.py`：
   - `build_system_prompt(user_id)` — 從資料庫撈資料，組裝注入格式（參考 memory_system.md 的模板）
   - `update_memory_after_conversation(user_id, user_msg, kana_response)` — 對話後評估並更新資料庫
5. 建立 `src/delay.py`：
   - `calculate_reply_delay(user_id, message)` — log-normal 延遲計算（完整邏輯在 schedule.md）

### Phase 3：Bot 主程式
6. 建立 `src/bot.py`：
   - 監聽 DM 訊息
   - 呼叫 delay.py 決定延遲
   - 呼叫 memory.py 組裝 prompt
   - 呼叫 claude_client.py 取得回覆
   - 傳送訊息
   - 非同步更新記憶

### Phase 4：心跳排程
7. 在 `bot.py` 中加入 APScheduler：
   - 每30分鐘：更新 persona_state
   - 每2小時（活躍時段）：觸發自主瀏覽（browse.py，可先用 stub）
   - 每2小時：檢查是否主動傳訊息

---

## 環境變數

執行前請確認以下環境變數已設定：

```
DISCORD_BOT_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
DATABASE_PATH=./data/kana.db
```

---

## 模型使用策略

不同呼叫用不同模型，避免全部用 Opus 造成不必要的費用。

| 呼叫類型 | 模型 | 理由 |
|----------|------|------|
| 對話回覆（加奈回使用者） | `claude-opus-4-6` | 個性細膩度最重要，不能省 |
| 對話後評估（記憶更新） | `claude-sonnet-4-6` | 結構化 JSON 輸出，Sonnet 夠用 |
| 狀態更新（心跳） | `claude-haiku-4-5-20251001` | 純邏輯判斷，速度優先 |
| 自主瀏覽消化 | `claude-sonnet-4-6` | 摘要與反應生成，Sonnet 夠用 |
| 主動推送判斷 | `claude-haiku-4-5-20251001` | 條件判斷，最輕量 |

### Prompt Cache 設定（必開）

persona.md 的內容每次呼叫都一樣，開啟 cache 後重複部分只收 10% 費用，是最大省錢來源。

在所有 API 呼叫的 system prompt 開頭加上 cache breakpoint：

```python
system = [
    {
        "type": "text",
        "text": PERSONA_CONTENT,  # 固定不變的部分
        "cache_control": {"type": "ephemeral"}
    },
    {
        "type": "text",
        "text": dynamic_context   # 每次不同的狀態與記憶
    }
]
```

### 費用估算（每月）

以 3 個使用者、每天共 20 次對話為基準，開啟 cache：

- 預估約 **$10–15 USD/月**
- 對話量是最大變數，心跳和自主瀏覽是固定成本
- 每次對話的全包成本（含所有背景呼叫）約 $0.01–0.02

---

## 技術規格

- Python 3.11+
- `discord.py >= 2.3`
- `anthropic >= 0.25`
- `numpy` （log-normal 計算）
- `apscheduler >= 3.10`
- SQLite（內建，不需額外安裝）

---

## 注意事項

- 加奈只回應 DM，不加入頻道
- `sleeping` 狀態下收到的訊息不回覆，但仍記錄
- 記憶更新為非同步操作，不應阻塞 Bot 回應
- 所有對 Claude API 的呼叫都要有 try/except，API 失敗時 Bot 不應崩潰
- `persona_state` 表只有一筆資料（id=1），用 UPDATE 而非 INSERT

---

## 完成標準

MVP 完成的判斷標準：
- [ ] Bot 可以接收 DM 並回覆（有延遲）
- [ ] 回覆內容符合 persona.md 的個性設定
- [ ] 每次對話後記憶正確更新到資料庫
- [ ] 新使用者自動初始化為 stranger 關係
- [ ] persona_state 每30分鐘自動更新
