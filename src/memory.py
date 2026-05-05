"""
memory.py -- 記憶注入與更新邏輯
"""

import json
import logging
from datetime import datetime, timezone

from database import (
    get_persona_state,
    get_relationship,
    get_recent_conversations,
    get_recent_self_memories,
    get_recent_world_memories,
    save_conversation_memory,
    update_relationship,
)
from claude_client import get_client

logger = logging.getLogger(__name__)


def build_system_prompt(user_id: str) -> str:
    state = get_persona_state()
    rel = get_relationship(user_id)
    recent_self = get_recent_self_memories(limit=7)
    recent_world = get_recent_world_memories(limit=5)
    recent_convs = get_recent_conversations(user_id, limit=5)

    now = datetime.now(timezone.utc).astimezone()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    state_section = (
        "[當前狀態]\n"
        + "現在時間：" + now_str + "\n"
        + "你正在做的事：" + str(state.get("current_activity", "idle")) + "\n"
        + "你的心情：" + str(state.get("current_mood", "content")) + "\n"
        + "體力：" + str(state.get("energy_level", 70)) + "/100\n"
        + "論文進度：" + str(state.get("thesis_progress", 0)) + "/100"
    )

    if recent_self:
        self_lines = "\n".join(
            "- [" + str(m.get("created_at", ""))[:10] + "] " + str(m["content"])
            for m in recent_self
        )
    else:
        self_lines = "（暫無記錄）"
    self_section = "[最近的生活（最近" + str(len(recent_self)) + "筆）]\n" + self_lines

    if recent_world:
        world_lines = "\n".join(
            "- " + str(m["title"]) + "（" + str(m.get("source", "")) + "）：" + str(m.get("her_reaction", ""))
            for m in recent_world
        )
    else:
        world_lines = "（暫無）"
    world_section = "[最近看到的東西（最近" + str(len(recent_world)) + "筆）]\n" + world_lines

    if rel:
        first_met_raw = rel.get("first_met", "")
        if isinstance(first_met_raw, datetime):
            first_met_date = first_met_raw.date()
        elif first_met_raw:
            try:
                first_met_date = datetime.fromisoformat(str(first_met_raw)[:19]).date()
            except Exception:
                first_met_date = None
        else:
            first_met_date = None

        if first_met_date:
            try:
                days = (now.date() - first_met_date).days
            except Exception:
                days = "?"
        else:
            days = "?"

        known_facts = json.loads(rel.get("known_facts") or "[]")
        inside_jokes = json.loads(rel.get("inside_jokes") or "[]")

        rel_section = (
            "[和這個使用者的關係]\n"
            + "名字：" + str(rel.get("display_name", user_id)) + "\n"
            + "認識多久：" + str(days) + " 天\n"
            + "熟悉度：" + str(rel.get("familiarity", 0)) + "/100\n"
            + "好感度：" + str(rel.get("affection", 0)) + "/100\n"
            + "關係階段：" + str(rel.get("relationship_stage", "stranger")) + "\n"
            + "你記得他說過的事：" + (", ".join(known_facts) if known_facts else "（還不太了解）") + "\n"
            + "你們之間的梗：" + (", ".join(inside_jokes) if inside_jokes else "（目前沒有）") + "\n"
            + "你現在對他的感覺：" + str(rel.get("last_mood_toward", "neutral"))
        )
    else:
        rel_section = "[和這個使用者的關係]\n第一次見面的陌生人（user_id: " + user_id + "）"

    if recent_convs:
        conv_lines = "\n".join(
            "- [" + str(c.get("created_at", ""))[:10] + "] " + str(c["summary"] or "")
            + ("（" + str(c["emotional_moment"]) + "）" if c.get("emotional_moment") else "")
            for c in recent_convs
        )
    else:
        conv_lines = "（這是你們的第一次對話）"
    conv_section = "[最近的對話記錄（最近" + str(len(recent_convs)) + "筆摘要）]\n" + conv_lines

    return "\n\n".join([
        state_section, self_section, world_section,
        rel_section, conv_section,
        "根據以上狀態，用加奈的方式回應。",
    ])


def build_system_prompt_with_media(user_id: str, user_message: str) -> str:
    base = build_system_prompt(user_id)
    media = query_relevant_media(user_message)
    if not media:
        return base
    media_lines = "\n".join(
        "- 《" + str(m["title"]) + "》（" + str(m.get("type", "")) + "）"
        + (" 作者：" + str(m["author"]) if m.get("author") else "")
        + (" 評分：" + str(m["score"]) + "/7" if m.get("score") else "")
        + (" 你的感想：" + str(m["her_note"]) if m.get("her_note") else "")
        for m in media
    )
    return base + "\n\n[你看過的相關作品（按需注入）]\n" + media_lines


def query_relevant_media(message: str) -> list:
    from database import query_media_by_types, query_high_score_media
    trigger_keywords = [
        "動漫", "電影", "書", "漫畫", "看了", "看過", "推薦",
        "最近在", "讀", "追", "好看", "有沒有", "番",
        "小說", "影集", "劇", "作品"
    ]
    if not any(k in message for k in trigger_keywords):
        return []
    type_map = {
        "anime": ["動漫", "番", "追"],
        "film":  ["電影", "看了", "看過"],
        "book":  ["書", "讀", "小說"],
        "manga": ["漫畫"],
        "tv":    ["影集", "劇"],
    }
    matched_types = [t for t, kws in type_map.items() if any(k in message for k in kws)]
    if not matched_types:
        return query_high_score_media(min_score=6, limit=5)
    return query_media_by_types(matched_types, min_score=5, limit=5)


MEMORY_EVAL_SYSTEM = "你是一個分析對話品質的系統。只輸出 JSON，不要有任何其他文字。"


def _make_eval_prompt(user_message: str, kana_response: str) -> str:
    schema = (
        "{\n"
        '  "summary": "這次對話摘要（50字以內）",\n'
        '  "emotional_moment": "有無特別情感事件（沒有填 null）",\n'
        '  "familiarity_delta": 0,\n'
        '  "affection_delta": 0,\n'
        '  "new_known_facts": [],\n'
        '  "new_inside_jokes": [],\n'
        '  "mood_toward_user": "neutral"\n'
        "}"
    )
    return (
        "根據以下對話，輸出 JSON（familiarity_delta: -5到+10，affection_delta: -10到+15，"
        "mood_toward_user: neutral|warm|curious|flustered|annoyed|distant）：\n\n"
        "使用者說：" + user_message + "\n"
        "加奈回應：" + kana_response + "\n\n"
        + schema
    )


async def update_memory_after_conversation(user_id, user_message, kana_response):
    client = get_client()
    try:
        prompt = _make_eval_prompt(user_message, kana_response)
        result = await client.call_for_json(
            call_type="memory",
            messages=[{"role": "user", "content": prompt}],
            system_override=MEMORY_EVAL_SYSTEM,
        )
        update_relationship(user_id, result)
        save_conversation_memory(user_id, result)
        logger.info("記憶更新完成：user=%s summary=%s", user_id, result.get("summary", ""))
    except Exception as e:
        logger.error("記憶更新失敗：user=%s error=%s", user_id, e)


HEARTBEAT_SYSTEM = "你是一個角色狀態管理系統。只輸出 JSON，不要有其他文字。"


def _make_heartbeat_prompt(state: dict) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %A")
    hour = datetime.now(timezone.utc).astimezone().hour
    return (
        "現在時間：" + now + "（當地時間 " + str(hour) + " 點）\n"
        "加奈上一個狀態：活動=" + str(state.get("current_activity", "idle"))
        + "，心情=" + str(state.get("current_mood", "content"))
        + "，體力=" + str(state.get("energy_level", 70))
        + "，論文進度=" + str(state.get("thesis_progress", 0)) + "\n\n"
        "根據時間推測加奈現在的狀態，輸出 JSON，所有欄位都必須填：\n"
        "- current_activity：sleeping|commuting|lab|reading|watching_anime|idle|writing_thesis 其中一個\n"
        "- current_mood：focused|lazy|anxious|content|irritated|distracted 其中一個\n"
        "- energy_level：整數 0-100。每次 heartbeat 的變化規則：\n"
        "    * idle / watching_anime / reading：-3 到 +5（放鬆，自然波動）\n"
        "    * commuting：-5 到 -8（通勤消耗）\n"
        "    * lab：-8 到 -12（實驗室高壓）\n"
        "    * writing_thesis：-10 到 -15（論文最耗心力）\n"
        "    * 不可低於 5，不可高於 100\n"
        "- thesis_progress：整數 0-100，代表論文完成百分比，只能增加或不變，每次最多 +2\n\n"
        "時段參考：7-8 點 idle（剛起床），8-9 點 commuting，10-18 點 lab 或 writing_thesis，"
        "19-23 點 watching_anime 或 reading 或 idle\n"
        "注意：0-7 點是強制睡眠期，heartbeat 不會在這段時間執行，不需要輸出 sleeping\n\n"
        "輸出範例：{\"current_activity\": \"reading\", \"current_mood\": \"content\", "
        "\"energy_level\": 65, \"thesis_progress\": 12}"
    )


async def update_persona_state_via_llm() -> dict:
    from database import get_persona_state, update_persona_state, add_self_memory
    client = get_client()
    state = get_persona_state()
    old_activity = state.get("current_activity", "idle")

    try:
        prompt = _make_heartbeat_prompt(state)
        result = await client.call_for_json(
            call_type="heartbeat",
            messages=[{"role": "user", "content": prompt}],
            system_override=HEARTBEAT_SYSTEM,
        )

        # 過濾有效欄位，並強制 energy_level / thesis_progress 為整數
        valid_keys = {"current_activity", "current_mood", "energy_level", "thesis_progress"}
        filtered = {}
        for k, v in result.items():
            if k not in valid_keys:
                continue
            if k in ("energy_level", "thesis_progress"):
                try:
                    filtered[k] = max(0, min(100, int(v)))
                except (TypeError, ValueError):
                    pass  # Claude 回傳非數字時跳過，保留舊值
            else:
                filtered[k] = v

        update_persona_state(**filtered)

        # 偵測 sleeping → 清醒的轉換
        new_activity = filtered.get("current_activity", old_activity)
        woke_up = (old_activity == "sleeping" and new_activity != "sleeping")

        # 寫入 memory_self，讓 DB 留下心跳軌跡
        mood = filtered.get("current_mood", state.get("current_mood", ""))
        activity = new_activity
        energy = filtered.get("energy_level", state.get("energy_level", ""))
        thesis = filtered.get("thesis_progress", state.get("thesis_progress", ""))
        tag = "wake_up" if woke_up else "heartbeat"
        content = (
            f"起床了 → 活動={activity}，心情={mood}，體力={energy}，論文={thesis}%"
            if woke_up else
            f"狀態更新 → 活動={activity}，心情={mood}，體力={energy}，論文={thesis}%"
        )
        add_self_memory(type_="heartbeat", content=content, mood_after=mood, tags=[tag])

        if woke_up:
            logger.info("偵測到起床：%s → %s", old_activity, new_activity)

        logger.info("狀態更新：%s", filtered)
        filtered["woke_up"] = woke_up
        return filtered
    except Exception as e:
        logger.error("狀態更新失敗：%s", e)
        return {**state, "woke_up": False}


# ── 日記 ──────────────────────────────────────────────────────────────────────

DIARY_SYSTEM = "你是加奈，用第一人稱繁體中文寫今天的日記。語氣自然、真實，像真正的私人日記，不需要標題。200字以內。"


async def write_daily_diary() -> None:
    """每日 23:59 呼叫，整理今天的事件寫成日記存入 memory_self。"""
    from database import get_recent_self_memories, get_recent_conversations, add_self_memory
    from datetime import date

    today = date.today().isoformat()

    # 收集今日的 memory_self（排除 heartbeat，留下有意義的事件）
    all_self = get_recent_self_memories(limit=50)
    today_events = [
        m for m in all_self
        if str(m.get("created_at", ""))[:10] == today
        and "heartbeat" not in json.loads(m.get("tags") or "[]")
    ]

    # 收集今日的對話摘要
    # 直接查 DB 取今天的對話
    from database import get_connection
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT summary, user_id FROM memory_conversation
            WHERE date(created_at) = ?
            ORDER BY created_at ASC
        """, (today,)).fetchall()
    today_convs = [dict(r) for r in rows]

    if not today_events and not today_convs:
        logger.info("[日記] 今天沒有記錄，跳過")
        return

    # 組裝給 Claude 的素材
    events_text = "\n".join(
        f"- {m['content']}" for m in today_events
    ) or "（無特別事件）"

    convs_text = "\n".join(
        f"- 和 {c['user_id']} 聊天：{c['summary']}" for c in today_convs
    ) or "（今天沒有對話）"

    state = get_persona_state()
    prompt = (
        f"今天是 {today}，加奈的狀態：心情={state.get('current_mood')}，"
        f"體力={state.get('energy_level')}，論文進度={state.get('thesis_progress')}%\n\n"
        f"今天發生的事：\n{events_text}\n\n"
        f"今天的對話：\n{convs_text}\n\n"
        "請用加奈的口吻寫今天的日記（繁體中文，200字以內，第一人稱，自然真實）："
    )

    client = get_client()
    try:
        diary = await client.call(
            call_type="diary",
            messages=[{"role": "user", "content": prompt}],
            system_override=DIARY_SYSTEM,
        )
        add_self_memory(
            type_="diary",
            content=diary.strip(),
            mood_after=state.get("current_mood"),
            tags=["diary", today],
        )
        logger.info("[日記] 今日日記寫完，%d 字", len(diary))
    except Exception as e:
        logger.error("[日記] 寫日記失敗：%s", e)
