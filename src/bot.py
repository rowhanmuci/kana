"""
bot.py — 加奈 Discord Bot 主程式

功能：
  - 監聽 DM 訊息，執行完整 reply pipeline
  - APScheduler 心跳排程（每30分鐘更新狀態、每2小時自主瀏覽/主動推送）
  - 每日熟悉度衰減
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()  # 自動載入專案根目錄的 .env

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 確保可以 import 同目錄的模組
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_db,
    ensure_user_exists,
    log_ignored_message,
    log_proactive_message,
    decay_familiarity_all,
    get_users_by_min_stage,
    save_pending_message,
    get_pending_messages,
    get_pending_count,
    mark_pending_processed,
    get_persona_state,
)
from memory import (
    build_system_prompt_with_media,
    update_memory_after_conversation,
    update_persona_state_via_llm,
)
from delay import (
    calculate_reply_delay,
    should_proactively_message,
    determine_trigger_context,
)
from browse import autonomous_browse
from claude_client import get_client

# ── 日誌設定 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("kana.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("kana.bot")

# ── Discord Bot 設定 ──────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()

# ── 訊息緩衝區（分段訊息合併用）────────────────────────────────────────────────
# 同一個 user 在延遲期間傳來的訊息全部累積，時間到了才一起回
_message_buffer: dict[str, list[str]] = {}   # user_id → 待回覆的訊息列表
_reply_tasks:   dict[str, asyncio.Task] = {}  # user_id → 當前待執行的回覆 task


# ═══════════════════════════════════════════════════════════════════════════════
# REPLY PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_message(message: discord.Message):
    # 忽略 bot 自己的訊息
    if message.author == bot.user:
        return

    # 只處理 DM
    if not isinstance(message.channel, discord.DMChannel):
        return

    user_id = str(message.author.id)
    display_name = message.author.display_name or message.author.name

    logger.info("收到 DM：user=%s content=%s", user_id, message.content[:50])

    # 1. 初始化新使用者
    ensure_user_exists(user_id, display_name)

    # 2. 取消舊的待回覆 task（如果有的話），準備合併訊息
    existing_task = _reply_tasks.get(user_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
        logger.info("[訊息] 取消舊排程，合併訊息：user=%s（已累積 %d 則）",
                    user_id, len(_message_buffer.get(user_id, [])))

    # 3. 累積訊息
    if user_id not in _message_buffer:
        _message_buffer[user_id] = []
    _message_buffer[user_id].append(message.content)

    # 4. 判斷要不要回（以最新訊息為準）
    delay = calculate_reply_delay(user_id, message.content)
    if delay is None:
        state = get_persona_state()
        activity = state.get("current_activity", "idle")
        energy = state.get("energy_level", 70)
        if activity == "sleeping" or energy < 20:
            # 整批存入待回佇列，等醒來後處理
            for msg in _message_buffer.pop(user_id, []):
                save_pending_message(user_id, display_name, msg)
            logger.info("訊息存入待回佇列（%s）：user=%s", activity, user_id)
        else:
            # 主動忽略（心情差、漏看等）
            log_ignored_message(user_id, message.content)
            _message_buffer.pop(user_id, None)
        return

    logger.info("計畫在 %d 秒後回覆 user=%s（緩衝 %d 則）",
                delay, user_id, len(_message_buffer[user_id]))

    # 5. 建立新的回覆 task
    task = asyncio.create_task(
        _delayed_reply(user_id, message.channel, delay)
    )
    _reply_tasks[user_id] = task


async def _delayed_reply(user_id: str, channel: discord.DMChannel, delay: int):
    """等待延遲後，取出緩衝區的所有訊息，合併成一個對話輪次再回覆。"""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        # 被新訊息取消，正常退出（緩衝區的訊息會在新 task 裡一起處理）
        return

    # 取出並清空緩衝
    messages = _message_buffer.pop(user_id, [])
    _reply_tasks.pop(user_id, None)

    if not messages:
        return

    # 合併多則訊息
    combined = "\n".join(messages)
    if len(messages) > 1:
        logger.info("[回覆] 合併 %d 則訊息一起回：user=%s", len(messages), user_id)

    # 呼叫 Claude
    async with channel.typing():
        try:
            dynamic_context = build_system_prompt_with_media(user_id, combined)
            conversation_history = _build_conversation_history(user_id, limit=10)
            client = get_client()
            response = await client.call(
                call_type="chat",
                messages=conversation_history + [
                    {"role": "user", "content": combined}
                ],
                dynamic_context=dynamic_context,
            )
        except Exception as e:
            logger.error("Claude 呼叫失敗：%s", e)
            return

    await channel.send(response)
    logger.info("回覆已送出：user=%s response=%s", user_id, response[:50])

    asyncio.create_task(
        update_memory_after_conversation(user_id, combined, response)
    )


def _build_conversation_history(user_id: str, limit: int = 5) -> list[dict]:
    """
    從 memory_conversation 建構最近對話的 messages 格式。
    以合法的 user/assistant 輪流配對呈現摘要，避免 API 格式錯誤。
    實際訊息內容已由 dynamic_context 的 conv_section 提供，這裡補充配對結構。
    """
    from database import get_recent_conversations

    convs = get_recent_conversations(user_id, limit=limit)
    history = []
    for conv in reversed(convs):  # 時間序
        if conv.get("summary"):
            history.append({"role": "user", "content": "[之前的對話]"})
            history.append({"role": "assistant", "content": f"[摘要：{conv['summary']}]"})
    return history


# ═══════════════════════════════════════════════════════════════════════════════
# PROACTIVE PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

async def proactive_check():
    """
    每 2 小時執行（與 heartbeat_state_only 同分觸發時，狀態更新由 heartbeat 負責）：
    1. 自主瀏覽
    2. 對有資格的使用者判斷是否主動傳訊
    """
    logger.info("[心跳] proactive_check 觸發")

    try:
        from database import get_persona_state
        state = get_persona_state()

        # 自主瀏覽
        world_memory = await autonomous_browse(state)
        logger.info("[心跳] 瀏覽完成，共 %d 筆", len(world_memory))

        # 主動推送
        eligible_users = get_users_by_min_stage("friend")
        for user_id in eligible_users:
            if should_proactively_message(user_id):
                await _send_proactive_message(user_id, world_memory)

    except Exception as e:
        logger.error("[心跳] proactive_check 失敗：%s", e)


async def _send_proactive_message(user_id: str, world_memory: list):
    """組裝 prompt 並主動傳訊給指定使用者。"""
    trigger_context = determine_trigger_context(user_id, world_memory)
    logger.info("[主動推送] user=%s trigger=%s", user_id, trigger_context)

    try:
        dynamic_context = build_system_prompt_with_media(user_id, "")
        client = get_client()
        response = await client.call(
            call_type="chat",
            messages=[{
                "role": "user",
                "content": f"[系統提示：{trigger_context}，請以加奈的身份主動傳訊息給對方]"
            }],
            dynamic_context=dynamic_context,
        )

        discord_user = await bot.fetch_user(int(user_id))
        await discord_user.send(response)
        log_proactive_message(user_id, trigger_context, response)
        logger.info("[主動推送] 已傳送：user=%s", user_id)

    except Exception as e:
        logger.error("[主動推送] 失敗：user=%s error=%s", user_id, e)


async def wake_up_check():
    """
    加奈起床後，檢查睡眠期間積累的未讀訊息，
    依照當前狀態（機率）決定要不要回，回覆邏輯與一般 DM 相同。
    """
    pending = get_pending_messages()
    if not pending:
        return

    logger.info("[起床] 發現 %d 位使用者有未讀訊息，開始處理", len(pending))

    for item in pending:
        user_id = item["user_id"]
        content = item["content"]
        count = get_pending_count(user_id)  # 積了幾則
        received_at = item["received_at"]

        logger.info("[起床] 處理 user=%s 的訊息（共 %d 則積累，最新：%s）",
                    user_id, count, content[:30])

        # 用當前清醒狀態重新計算要不要回
        delay = calculate_reply_delay(user_id, content)
        mark_pending_processed(user_id)  # 不管回不回，先標為已處理

        if delay is None:
            logger.info("[起床] 決定不回覆 user=%s 的積累訊息", user_id)
            log_ignored_message(user_id, content)
            continue

        # 延遲較短（起床後通常比較快回）
        actual_delay = min(delay, 300)
        logger.info("[起床] 將在 %d 秒後回覆 user=%s", actual_delay, user_id)
        await asyncio.sleep(actual_delay)

        try:
            discord_user = await bot.fetch_user(int(user_id))
            # 組裝 prompt，讓加奈知道這是睡前積累的訊息
            context_note = (
                f"[你剛起床，發現對方在 {received_at} 傳了訊息"
                + (f"（加上之前還有 {count-1} 則沒看到的）" if count > 1 else "")
                + "，請以加奈的方式自然地回應，可以提到剛睡醒這件事]"
            )
            dynamic_context = build_system_prompt_with_media(user_id, content)
            client = get_client()
            response = await client.call(
                call_type="chat",
                messages=_build_conversation_history(user_id) + [
                    {"role": "user", "content": f"{context_note}\n\n使用者說：{content}"}
                ],
                dynamic_context=dynamic_context,
            )
            await discord_user.send(response)
            logger.info("[起床] 已回覆 user=%s：%s", user_id, response[:50])
            asyncio.create_task(
                update_memory_after_conversation(user_id, content, response)
            )
        except Exception as e:
            logger.error("[起床] 回覆失敗 user=%s：%s", user_id, e)


async def heartbeat_state_only():
    """每 30 分鐘輕量狀態更新（不含瀏覽和推送），僅在 07:00–23:30 執行。"""
    logger.info("[心跳] heartbeat_state_only 觸發")
    try:
        result = await update_persona_state_via_llm()
        if result.get("woke_up"):
            logger.info("[心跳] 偵測到起床，執行未讀訊息檢查")
            await wake_up_check()
    except Exception as e:
        logger.error("[心跳] 狀態更新失敗：%s", e)


async def morning_wake_up():
    """每日 07:00 強制起床：體力回滿，處理睡眠期間積累的未讀訊息。"""
    from database import update_persona_state, add_self_memory
    logger.info("[起床] 早安！強制起床，體力回滿")
    update_persona_state(
        current_activity="idle",
        current_mood="content",
        energy_level=100,
    )
    add_self_memory(
        type_="daily_event",
        content="早上起床了，體力回滿",
        mood_after="content",
        tags=["wake_up", "morning"],
    )
    await wake_up_check()


async def night_sleep():
    """每日 02:00 強制進入睡眠，2:00–7:00 期間不再有心跳。"""
    from database import update_persona_state, add_self_memory
    logger.info("[就寢] 強制進入睡眠狀態")
    update_persona_state(
        current_activity="sleeping",
        current_mood="content",
    )
    add_self_memory(
        type_="daily_event",
        content="睡覺了，晚安",
        mood_after="content",
        tags=["sleep", "night"],
    )


async def daily_diary():
    """每日 23:59 整理今天的日記。"""
    from memory import write_daily_diary
    logger.info("[日記] 開始整理今日日記")
    try:
        await write_daily_diary()
        logger.info("[日記] 日記寫完了")
    except Exception as e:
        logger.error("[日記] 日記寫失敗：%s", e)


async def daily_decay():
    """每日凌晨執行熟悉度自然衰減。"""
    logger.info("[每日] 熟悉度衰減執行")
    try:
        decay_familiarity_all()
    except Exception as e:
        logger.error("[每日] 熟悉度衰減失敗：%s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# BOT 事件
# ═══════════════════════════════════════════════════════════════════════════════

async def startup_reconcile():
    """
    Bot 啟動時根據當前台北時間自動修正加奈的狀態。

    解決問題：電腦關機導致排程 job 沒有執行時
    （例如 1:00 AM 關機 → 錯過 2:00 night_sleep，
       9:00 AM 開機 → 錯過 7:00 morning_wake_up）
    """
    from database import update_persona_state, add_self_memory, get_persona_state
    from datetime import datetime, timezone, timedelta

    TAIPEI_TZ = timezone(timedelta(hours=8))
    now = datetime.now(TAIPEI_TZ)
    hour = now.hour
    state = get_persona_state()

    # 計算距上次心跳多久
    last_updated_str = state.get("last_updated", "")
    hours_offline = 999.0
    try:
        last_dt = datetime.fromisoformat(last_updated_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=TAIPEI_TZ)
        hours_offline = (now - last_dt).total_seconds() / 3600
    except Exception:
        pass

    logger.info("[啟動] 台北時間 %02d:%02d，上次狀態更新 %.1f 小時前（activity=%s, energy=%s）",
                hour, now.minute, hours_offline,
                state.get("current_activity"), state.get("energy_level"))

    if 2 <= hour < 7:
        # ── 睡眠時段（02:00–07:00）：強制設為 sleeping ──────────────────────
        if state.get("current_activity") != "sleeping":
            logger.info("[啟動] 睡眠時段，強制設為 sleeping")
            update_persona_state(current_activity="sleeping", current_mood="content")
            add_self_memory(
                type_="daily_event",
                content="（補記）Bot 重啟於睡眠時段，強制設為睡覺中",
                mood_after="content",
                tags=["sleep", "startup"],
            )
        else:
            logger.info("[啟動] 睡眠時段，狀態已是 sleeping，無需修正")

    elif hour >= 7:
        # ── 清醒時段（07:00–23:59）─────────────────────────────────────────
        needs_wake = (
            state.get("current_activity") == "sleeping"   # 還在睡眠狀態
            or hours_offline > 5                          # 離線超過 5 小時
        )
        if needs_wake:
            logger.info("[啟動] 需要起床（activity=%s, offline=%.1fh），強制起床 + 體力回滿",
                        state.get("current_activity"), hours_offline)
            update_persona_state(
                current_activity="idle",
                current_mood="content",
                energy_level=100,
            )
            add_self_memory(
                type_="daily_event",
                content=f"（補記）Bot 於 {now.strftime('%H:%M')} 重啟，起床了，體力回滿",
                mood_after="content",
                tags=["wake_up", "startup"],
            )
            await wake_up_check()
        else:
            logger.info("[啟動] 狀態正常（offline=%.1fh），無需修正", hours_offline)


@bot.event
async def on_ready():
    logger.info("加奈上線：%s (ID: %s)", bot.user, bot.user.id)

    # ── 啟動時狀態修正 ─────────────────────────────────────────────────────────
    await startup_reconcile()

    # ── 排程設定（台北時間）────────────────────────────────────────────────────

    # 每 30 分鐘（07:00–23:30）：輕量狀態更新
    # 02:00–07:00 為絕對睡眠期，不執行心跳
    scheduler.add_job(
        heartbeat_state_only,
        "cron",
        hour="7-23",
        minute="*/30",
        id="heartbeat_state",
        timezone="Asia/Taipei",
    )
    # 每 2 小時（活躍時段）：完整心跳（瀏覽 + 推送）
    scheduler.add_job(
        proactive_check,
        "cron",
        hour="9,11,14,16,19,21",
        minute=0,
        id="proactive_check",
        timezone="Asia/Taipei",
    )
    # 每日 07:00：強制起床，體力回滿，處理未讀訊息
    scheduler.add_job(
        morning_wake_up,
        "cron",
        hour=7,
        minute=0,
        id="morning_wake_up",
        timezone="Asia/Taipei",
    )
    # 每日 02:00：強制睡覺
    scheduler.add_job(
        night_sleep,
        "cron",
        hour=2,
        minute=0,
        id="night_sleep",
        timezone="Asia/Taipei",
    )
    # 每日 23:59：寫日記
    scheduler.add_job(
        daily_diary,
        "cron",
        hour=23,
        minute=59,
        id="daily_diary",
        timezone="Asia/Taipei",
    )
    # 每日 04:00：熟悉度衰減
    scheduler.add_job(
        daily_decay,
        "cron",
        hour=4,
        minute=0,
        id="daily_decay",
        timezone="Asia/Taipei",
    )
    scheduler.start()
    logger.info("排程已啟動（台北時間）")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.exception("Discord 事件錯誤：event=%s", event)


# ═══════════════════════════════════════════════════════════════════════════════
# 入口點
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN 未設定，Bot 無法啟動")
        sys.exit(1)

    # 初始化資料庫
    logger.info("初始化資料庫...")
    init_db()
    logger.info("資料庫初始化完成")

    # 啟動 Bot
    logger.info("啟動加奈 Bot...")
    bot.run(token, log_handler=None)  # log_handler=None 避免重複設定


if __name__ == "__main__":
    main()
