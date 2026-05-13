"""
memory.py -- 記憶注入與更新邏輯
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from database import (
    add_commitment,
    add_self_memory,
    get_dynamic_persona,
    get_persona_state,
    get_relationship,
    get_recent_conversations,
    get_recent_self_memories,
    get_recent_self_memories_excluding,
    get_recent_world_memories,
    save_conversation_memory,
    update_dynamic_persona,
    update_relationship,
)
from claude_client import get_client

_THESIS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "thesis.md"
)

THESIS_KEYWORDS = [
    "論文", "進度", "指導教授", "研究方法", "實驗", "第幾章", "寫了多少",
    "paper", "thesis", "baseline", "實驗設計", "跨模態", "對齊", "文獻",
]

logger = logging.getLogger(__name__)


def _append_thesis_note(note: str, timestamp: str) -> None:
    """將 writing_thesis 活動產生的筆記 append 到 thesis.md。"""
    try:
        try:
            with open(_THESIS_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            content = ""

        entry = f"- [{timestamp[:16]}] {note}\n"
        if "近期工作記錄" not in content:
            with open(_THESIS_PATH, "a", encoding="utf-8") as f:
                f.write("\n\n---\n\n## 近期工作記錄\n\n" + entry)
        else:
            with open(_THESIS_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
        logger.info("[論文] 工作記錄已寫入 thesis.md：%s", note)
    except Exception as e:
        logger.warning("[論文] 寫入 thesis.md 失敗：%s", e)


def query_thesis_context(message: str) -> str:
    """若訊息含論文相關關鍵字，讀取 thesis.md 回傳；否則回傳空字串。"""
    if not any(k in message for k in THESIS_KEYWORDS):
        return ""
    try:
        with open(_THESIS_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return "[論文現況]\n" + content
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning("[thesis] 讀取 thesis.md 失敗：%s", e)
        return ""


def build_system_prompt(user_id: str) -> str:
    state = get_persona_state()
    rel = get_relationship(user_id)
    recent_self = get_recent_self_memories_excluding(["heartbeat"], limit=7)
    recent_world = get_recent_world_memories(limit=5)
    recent_convs = get_recent_conversations(user_id, limit=5)

    from database import TAIPEI_TZ
    now = datetime.now(TAIPEI_TZ)
    _weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    now_str = now.strftime("%Y-%m-%d ") + _weekdays[now.weekday()] + now.strftime(" %H:%M")

    state_section = (
        "[當前狀態]\n"
        + "現在時間：" + now_str + "\n"
        + "你正在做的事：" + str(state.get("current_activity", "idle")) + "\n"
        + "你的心情：" + str(state.get("current_mood", "content")) + "\n"
        + "體力：" + str(state.get("energy_level", 70)) + "/100"
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

    stage = rel.get("relationship_stage", "stranger") if rel else "stranger"
    familiarity = rel.get("familiarity", 0) if rel else 0

    if stage == "special":
        tone_hint = (
            "你們關係很特別，對方是你真的在乎的人。"
            "說話可以很自然，不用過濾，偶爾可以展現你少見的軟的那面。"
        )
    elif stage == "close":
        tone_hint = (
            "你們很熟。語氣輕鬆，可以俏皮、吐槽、開對方玩笑，"
            "說話不用克制，想說什麼就說。"
        )
    elif stage == "friend":
        tone_hint = (
            "你們已經是朋友了。語氣可以比平常放鬆，"
            "對有趣的事多說幾句，偶爾俏皮一下也沒問題。"
        )
    elif stage == "acquaintance":
        tone_hint = (
            "你們不是完全陌生，有一點熟悉度。"
            "可以稍微放鬆，對有興趣的話題自然地多回應一些。"
        )
    else:
        tone_hint = (
            "對方是不太熟的人，保持你平常的冷靜就好，"
            "但如果對方說了什麼讓你有想法的，你還是可以有反應。"
        )

    dp = get_dynamic_persona()
    dp_section = ""
    if dp and (dp.get("current_obsessions") or dp.get("dynamic_interests")):
        obsessions = "、".join(dp.get("current_obsessions") or []) or "（無）"
        interests_str = "、".join(dp.get("dynamic_interests") or []) or "（無）"
        tendencies = dp.get("tone_tendencies") or "（無特別傾向）"
        sensitivities = "、".join(dp.get("recent_sensitivities") or []) or "（無）"
        dp_section = (
            "[加奈近期的樣子（每日更新）]\n"
            f"最近著迷的事：{obsessions}\n"
            f"興趣方向：{interests_str}\n"
            f"說話傾向：{tendencies}\n"
            f"最近在意的事：{sensitivities}"
        )

    sections = [state_section]
    if dp_section:
        sections.append(dp_section)
    sections += [self_section, world_section, rel_section, conv_section]
    sections.append(
        f"根據以上狀態，用加奈的方式回應。{tone_hint}\n"
        "對話歷史裡每則訊息的 [日期 時間] 是傳送時間，是給你參考用的元資料，"
        "回覆中不要重複輸出這個格式。對方說「明天」「今天」「昨天」等相對時間時，"
        "請對照該訊息的時間戳推算正確日期，不要假設和現在同一天。"
    )
    return "\n\n".join(sections)


def build_system_prompt_with_media(user_id: str, user_message: str) -> str:
    base = build_system_prompt(user_id)
    parts = [base]

    media = query_relevant_media(user_message)
    if media:
        media_lines = "\n".join(
            "- 《" + str(m["title"]) + "》（" + str(m.get("type", "")) + "）"
            + (" 作者：" + str(m["author"]) if m.get("author") else "")
            + (" 評分：" + str(m["score"]) + "/7" if m.get("score") else "")
            + (" 你的感想：" + str(m["her_note"]) if m.get("her_note") else "")
            for m in media
        )
        parts.append("[你看過的相關作品（按需注入）]\n" + media_lines)

    thesis = query_thesis_context(user_message)
    if thesis:
        parts.append(thesis)

    if re.search(r'https?://', user_message):
        parts.append("[提醒] 訊息中有網址。請立刻用 fetch_url 工具打開讀取，不要說打不開或表示無法存取。")

    return "\n\n".join(parts)


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
        '  "mood_toward_user": "neutral",\n'
        '  "new_promises": []\n'
        "}"
    )
    return (
        "根據以下對話，輸出 JSON（familiarity_delta: -5到+10，affection_delta: -10到+15，"
        "mood_toward_user: neutral|warm|curious|flustered|annoyed|distant）：\n\n"
        "使用者說：" + user_message + "\n"
        "加奈回應：" + kana_response + "\n\n"
        "new_promises：加奈在對話中說她之後會去看/聽/查的事情。"
        '格式：[{"type": "music|anime|book|url|other", "title": "名稱", "url": "連結（沒有填空字串）", "detail": "她說了什麼"}]'
        "。沒有承諾填 []。目標是捕捉已發生的承諾，不是要她承諾更多事。\n\n"
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

        for p in result.get("new_promises", []):
            title = p.get("title", "")
            if not title:
                continue
            add_commitment(
                user_id=user_id,
                type_=p.get("type", "other"),
                title=title,
                url=p.get("url", ""),
                detail=p.get("detail", ""),
            )
            logger.info("[承諾] 寫入：%s (%s)", title, p.get("type", "other"))

        logger.info("記憶更新完成：user=%s summary=%s", user_id, result.get("summary", ""))
    except Exception as e:
        logger.error("記憶更新失敗：user=%s error=%s", user_id, e)


HEARTBEAT_SYSTEM = "你是一個角色狀態管理系統。只輸出 JSON，不要有其他文字。"


def _make_heartbeat_prompt(state: dict) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %A")
    hour = datetime.now(timezone.utc).astimezone().hour

    dp = get_dynamic_persona()
    interests = dp.get("dynamic_interests", []) if dp else []
    obsessions = dp.get("current_obsessions", []) if dp else []
    interest_hint = ""
    if interests or obsessions:
        parts = []
        if obsessions:
            parts.append("最近在鑽：" + "、".join(obsessions))
        if interests:
            parts.append("興趣方向：" + "、".join(interests))
        interest_hint = "加奈近期狀態（供 activity 選擇參考）：" + "；".join(parts) + "\n"

    return (
        "現在時間：" + now + "（當地時間 " + str(hour) + " 點）\n"
        + interest_hint
        + "加奈上一個狀態：活動=" + str(state.get("current_activity", "idle"))
        + "，心情=" + str(state.get("current_mood", "content"))
        + "，體力=" + str(state.get("energy_level", 70)) + "\n\n"
        "根據時間推測加奈現在的狀態，輸出 JSON，所有欄位都必須填：\n"
        "- current_activity：sleeping|commuting|lab|reading|watching_anime|idle|writing_thesis|listening_music 其中一個\n"
        "- current_mood：focused|lazy|anxious|content|irritated|distracted 其中一個\n"
        "- energy_level：整數 0-100。每次 heartbeat 的變化規則（嚴格遵守，不可例外）：\n"
        "    * idle / watching_anime / reading / listening_music：-3 到 +5（放鬆狀態可小幅恢復）\n"
        "    * commuting：只能減少，範圍 -5 到 -8\n"
        "    * lab：只能減少，範圍 -8 到 -12（實驗室高壓，中途不會突然恢復）\n"
        "    * writing_thesis：只能減少，範圍 -10 到 -15（論文最耗心力）\n"
        "    * 不可低於 5，不可高於 100\n"
        "    * 警告：lab / commuting / writing_thesis 狀態下體力只降不升，輸出比上一次高的數值是錯誤的\n"
        "- thesis_note（可選）：只在 current_activity=writing_thesis 時填，"
        "這次主要在做論文哪個部分（20字以內，例如「整理第三章，補充 baseline 說明」）；"
        "其他活動時不要輸出這個欄位\n\n"
        "時段參考：7-8 點 idle（剛起床），8-9 點 commuting，10-18 點 lab 或 writing_thesis，"
        "19-23 點 watching_anime 或 reading 或 idle 或 listening_music\n"
        "注意：0-7 點是強制睡眠期，heartbeat 不會在這段時間執行，不需要輸出 sleeping\n\n"
        "輸出範例：{\"current_activity\": \"writing_thesis\", \"current_mood\": \"focused\", "
        "\"energy_level\": 58, "
        "\"thesis_note\": \"修改第二章文獻探討，補了兩篇 cross-modal 的 related work\"}"
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

        # 過濾有效欄位，並強制 energy_level 為整數
        valid_keys = {"current_activity", "current_mood", "energy_level"}
        filtered = {}
        for k, v in result.items():
            if k not in valid_keys:
                continue
            if k == "energy_level":
                try:
                    filtered[k] = max(5, min(100, int(v)))
                except (TypeError, ValueError):
                    pass
            else:
                filtered[k] = v

        # 硬性保護：高消耗活動下體力不可上升
        ENERGY_ONLY_DOWN = {"lab", "commuting", "writing_thesis"}
        new_activity = filtered.get("current_activity", old_activity)
        if new_activity in ENERGY_ONLY_DOWN and "energy_level" in filtered:
            old_energy = state.get("energy_level", 100)
            if filtered["energy_level"] > old_energy:
                logger.warning(
                    "[體力保護] %s 狀態下體力不應上升（%d → %d），強制修正",
                    new_activity, old_energy, filtered["energy_level"]
                )
                # 給予最小消耗（-5），模擬「沒什麼在動」的低耗狀態
                filtered["energy_level"] = max(5, old_energy - 5)

        update_persona_state(**filtered)

        # 偵測 sleeping → 清醒的轉換（new_activity 已在上方體力保護區塊定義）
        woke_up = (old_activity == "sleeping" and new_activity != "sleeping")

        # 寫入 memory_self，讓 DB 留下心跳軌跡
        mood = filtered.get("current_mood", state.get("current_mood", ""))
        activity = new_activity
        energy = filtered.get("energy_level", state.get("energy_level", ""))
        tag = "wake_up" if woke_up else "heartbeat"
        content = (
            f"起床了 → 活動={activity}，心情={mood}，體力={energy}"
            if woke_up else
            f"狀態更新 → 活動={activity}，心情={mood}，體力={energy}"
        )
        add_self_memory(type_="heartbeat", content=content, mood_after=mood, tags=[tag])

        # 論文工作記錄：若 Claude 回傳了 thesis_note，append 到 thesis.md
        thesis_note = result.get("thesis_note")
        if thesis_note and new_activity == "writing_thesis":
            now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
            _append_thesis_note(thesis_note, now_str)

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

    # 收集今日的 memory_self（排除 heartbeat/daily_event，留下有意義的事件）
    all_self = get_recent_self_memories(limit=50)
    today_events = [
        m for m in all_self
        if str(m.get("created_at", ""))[:10] == today
        and m.get("type") not in ("heartbeat", "daily_event")
    ]

    # 收集今日的對話摘要
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

    events_text = "\n".join(
        f"- {m['content']}" for m in today_events
    ) or "（無特別事件）"

    convs_text = "\n".join(
        f"- 和 {c['user_id']} 聊天：{c['summary']}" for c in today_convs
    ) or "（今天沒有對話）"

    state = get_persona_state()
    prompt = (
        f"今天是 {today}，加奈的狀態：心情={state.get('current_mood')}，"
        f"體力={state.get('energy_level')}\n\n"
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


# ── GLA Reflect-Evolve ────────────────────────────────────────────────────────

REFLECT_SYSTEM = (
    "你是加奈，用加奈的第一人稱語氣輸出。只輸出觀察內容，不要標題、日期或任何格式。"
    "觀察要忠實反映你本來的品味與個性——若看到的東西和你原本不感興趣的領域有關（例如主流流行音樂），"
    "不需要強迫自己說「有點感興趣」，可以說無感或忽略不提。"
)
EVOLVE_SYSTEM = "只輸出合法 JSON，不要其他說明。"


async def run_daily_reflect_evolve() -> None:
    """每日 Reflect → Evolve：把近期記憶壓縮成動態人格狀態。"""
    client = get_client()

    # ── Reflect：第一人稱自我觀察 ────────────────────────────────────────────
    recent_self = get_recent_self_memories_excluding(["heartbeat", "daily_event"], limit=20)
    recent_world = get_recent_world_memories(limit=10)

    if not recent_self and not recent_world:
        logger.info("[Reflect] 沒有記憶資料，跳過")
        return

    self_lines = "\n".join(
        f"- [{str(m.get('created_at', ''))[:10]}] {m.get('type', '')}: {m['content']}"
        for m in recent_self
    ) or "（無）"

    world_lines = "\n".join(
        f"- {m['title']}（{m.get('source', '')}）：{m.get('her_reaction', '')}"
        for m in recent_world
    ) or "（無）"

    reflect_prompt = (
        "[加奈最近的記錄]\n\n"
        f"【生活事件】\n{self_lines}\n\n"
        f"【看到的東西】\n{world_lines}\n\n"
        "請用加奈的第一人稱視角，寫出她對自己最近這段時間的內心觀察（繁體中文，150字以內）。"
        "重點是她注意到自己最近有什麼傾向、在乎什麼、對什麼有感覺。"
        "是內心獨白，不是日記，不需要日期。"
    )

    try:
        reflection = await client.call(
            call_type="memory",
            messages=[{"role": "user", "content": reflect_prompt}],
            system_override=REFLECT_SYSTEM,
        )
        reflection = reflection.strip()
        add_self_memory(type_="reflection", content=reflection, tags=["reflection"])
        logger.info("[Reflect] 完成，%d 字", len(reflection))
    except Exception as e:
        logger.error("[Reflect] 失敗：%s", e)
        return

    # ── Evolve：結構化人格更新 ───────────────────────────────────────────────
    current_dp = get_dynamic_persona()
    current_dp_json = json.dumps(
        {k: v for k, v in current_dp.items() if k != "id" and k != "updated_at"},
        ensure_ascii=False,
    )

    evolve_prompt = (
        "根據加奈對自己的觀察，更新她的動態狀態。\n\n"
        f"【她的觀察】\n{reflection}\n\n"
        f"【上一次狀態（參考用）】\n{current_dp_json}\n\n"
        "【判斷興趣是否納入的標準】\n"
        "納入：她在觀察中表達了主動的感受、反覆琢磨、或因真實對話/體驗觸動的東西。\n"
        "排除：她只是「看到了」某個資訊但沒有具體感受，或只是被動接收的瀏覽結果。\n"
        "人格可以漂移，但漂移要由真實體驗驅動，不是由搜尋結果驅動。\n\n"
        "輸出 JSON（只輸出 JSON，不要其他說明）：\n"
        "{\n"
        '  "current_obsessions": ["最近在鑽研或著迷的事，最多3項，具體"],\n'
        '  "dynamic_interests": ["當前興趣方向，最多5項，須符合底色精神"],\n'
        '  "tone_tendencies": "最近說話傾向，20字以內",\n'
        '  "recent_sensitivities": ["最近比較在意或敏感的事，最多3項"]\n'
        "}"
    )

    try:
        result = await client.call_for_json(
            call_type="memory",
            messages=[{"role": "user", "content": evolve_prompt}],
            system_override=EVOLVE_SYSTEM,
        )
        update_dynamic_persona(
            obsessions=result.get("current_obsessions", []),
            interests=result.get("dynamic_interests", []),
            tendencies=result.get("tone_tendencies", ""),
            sensitivities=result.get("recent_sensitivities", []),
        )
        logger.info("[Evolve] 完成：興趣=%s", result.get("dynamic_interests", []))
    except Exception as e:
        logger.error("[Evolve] 失敗：%s", e)
