# discord_skill.md — Discord Bot 串接工程

## 架構概覽

```
[Discord Bot]
    ├── 接收使用者私訊  →  reply pipeline
    └── 定時排程        →  proactive pipeline

[reply pipeline]
    1. 判斷要不要回（回應意願邏輯）
    2. 計算延遲時間
    3. 注入記憶，呼叫 Claude API
    4. 傳送回覆
    5. 更新記憶資料庫

[proactive pipeline]
    1. 心跳觸發（每 2 小時 or 狀態更新後）
    2. 判斷要主動傳給誰
    3. 注入記憶 + 觸發情境，呼叫 Claude API
    4. 傳送訊息
    5. 更新記憶資料庫
```

---

## Discord Bot 設定

### 必要 Intents
```python
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
```

### 監聽範圍
- **只監聽私訊（DM）**
- 不加入任何伺服器頻道
- 每個使用者獨立的對話空間

---

## Reply Pipeline 實作

```python
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return

    user_id = str(message.author.id)

    # 1. 初始化新使用者
    ensure_user_exists(user_id, message.author.display_name)

    # 2. 判斷要不要回
    delay = calculate_reply_delay(user_id, message.content)
    if delay is None:
        # 不回，但記錄這則訊息
        log_ignored_message(user_id, message.content)
        return

    # 3. 等待延遲
    await asyncio.sleep(delay)

    # 4. 顯示「正在輸入」
    async with message.channel.typing():
        # 5. 組裝記憶，呼叫 Claude API
        system_prompt = build_system_prompt(user_id)
        conversation_history = get_recent_history(user_id, limit=10)
        
        response = await call_claude(
            system=system_prompt,
            messages=conversation_history + [
                {"role": "user", "content": message.content}
            ]
        )

    # 6. 傳送回覆
    await message.channel.send(response)

    # 7. 更新記憶（非同步，不阻塞）
    asyncio.create_task(
        update_memory_after_conversation(user_id, message.content, response)
    )
```

---

## Proactive Pipeline 實作

```python
async def proactive_check():
    """每 2 小時執行，判斷要不要主動傳訊息"""
    
    state = get_persona_state()
    
    # 更新加奈的狀態
    new_state = await update_persona_state()
    
    # 觸發自主瀏覽
    world_memory = await autonomous_browse(new_state)
    
    # 對每個關係足夠的使用者判斷
    eligible_users = get_users_by_min_stage('friend')
    
    for user_id in eligible_users:
        if should_proactively_message(user_id):
            trigger_context = determine_trigger_context(user_id, world_memory)
            
            system_prompt = build_system_prompt(user_id)
            
            response = await call_claude(
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": f"[系統提示：{trigger_context}，請以加奈的身份主動傳訊息]"
                }]
            )
            
            user = await bot.fetch_user(int(user_id))
            await user.send(response)
            
            log_proactive_message(user_id, trigger_context, response)
```

---

## 自主瀏覽實作

```python
async def autonomous_browse(state):
    """根據加奈的興趣和當前時間，抓取外部資訊"""
    
    hour = datetime.now().hour
    results = []

    if 9 <= hour < 11:
        # 資安 / 技術新聞
        results += await fetch_rss('https://feeds.feedburner.com/TheHackersNews')

    if 11 <= hour < 13:
        # arXiv（聯邦學習 / 隱私保護）
        results += await fetch_arxiv(
            query='federated learning privacy',
            max_results=3
        )

    if 14 <= hour < 16:
        # 動漫社群（AniList 近期更新）
        results += await fetch_anilist_recent()

    if 19 <= hour < 21:
        # 動漫更新
        results += await fetch_anime_updates()

    # 讓 Claude 幫加奈「消化」這些資訊，寫成她的反應
    for item in results[:5]:  # 每次最多處理5筆
        reaction = await generate_her_reaction(item)
        save_to_memory_world(item, reaction)

    return results
```

---

## Claude API 呼叫

```python
async def call_claude(system: str, messages: list) -> str:
    import anthropic
    
    client = anthropic.AsyncAnthropic()
    
    response = await client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,  # 加奈回覆不長
        system=system,
        messages=messages
    )
    
    return response.content[0].text
```

---

## 對話後記憶更新

```python
async def update_memory_after_conversation(user_id, user_message, kana_response):
    """對話結束後，評估這次互動並更新記憶"""
    
    evaluation_prompt = f"""
根據以下對話，輸出 JSON（只輸出 JSON，不要有其他文字）：

使用者說：{user_message}
加奈回應：{kana_response}

{{
  "summary": "這次對話摘要（50字以內）",
  "emotional_moment": "有無特別情感事件（沒有填 null）",
  "familiarity_delta": 整數（-5 到 +10）,
  "affection_delta": 整數（-10 到 +15）,
  "new_known_facts": ["這次得知的新事實"],
  "new_inside_jokes": ["新產生的梗（沒有就空陣列）"],
  "mood_toward_user": "neutral | warm | curious | flustered | annoyed | distant"
}}
"""
    
    result_json = await call_claude(
        system="你是一個評估對話品質的系統。只輸出 JSON。",
        messages=[{"role": "user", "content": evaluation_prompt}]
    )
    
    result = json.loads(result_json)
    update_relationship(user_id, result)
    save_conversation_memory(user_id, result)
```

---

## 部署注意事項

### 環境變數
```
DISCORD_BOT_TOKEN=
ANTHROPIC_API_KEY=
DATABASE_PATH=./kana.db
```

### 推薦執行環境
- Python 3.11+
- `discord.py` 2.x
- `anthropic` SDK
- `APScheduler` 做排程
- SQLite（本地），或未來升級 PostgreSQL

### Rate Limit 注意
- Discord DM 傳送：每頻道 5 msg/s，日常使用遠低於上限
- Anthropic API：注意 per-minute token 限制，proactive pipeline 加入佇列機制避免同時打爆
