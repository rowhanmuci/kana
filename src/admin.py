"""
admin.py — 管理員頻道邏輯

審核流程：
  Kana 生成草稿 → 發到 ADMIN_CHANNEL → 管理員 ✅ 發出 / ❌ + 建議修改 → Kana 修改 → 再次送審
  管理員直接輸入指示 → Kana 生成 → 送審

.env 需設定：
  ADMIN_CHANNEL_ID   Discord 管理員頻道的 channel ID
  OWNER_USER_ID      管理員的 Discord user ID
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_THREADS_STYLE_PATH = Path(__file__).parent.parent / "data" / "threads_style.md"


def _load_threads_style() -> str:
    """讀取 Threads 風格指引，作為 dynamic_context 注入。"""
    try:
        content = _THREADS_STYLE_PATH.read_text(encoding="utf-8").strip()
        return f"[Threads 互動風格指引]\n{content}"
    except Exception:
        return ""


def _append_style_note(feedback: str) -> None:
    """把修改建議摘要 append 到 threads_style.md 的修正紀錄區。"""
    try:
        existing = _THREADS_STYLE_PATH.read_text(encoding="utf-8")
        if "## 修正紀錄" not in existing:
            existing += "\n\n## 修正紀錄\n"
        existing += f"- {feedback.strip()}\n"
        _THREADS_STYLE_PATH.write_text(existing, encoding="utf-8")
    except Exception as e:
        logger.warning("[admin] 寫入風格修正失敗：%s", e)

ADMIN_CHANNEL_ID = int(os.environ.get("ADMIN_CHANNEL_ID", 0))
OWNER_USER_ID = int(os.environ.get("OWNER_USER_ID", 0))

# ❌ 後 bot 回覆「給我修改建議」的訊息 ID → 原始草稿的 discord_message_id
_feedback_prompt_map: dict[str, str] = {}

# ✉️ 留言展示訊息 ID → {comment_id, text, author, post_snippet}
_comment_map: dict[str, dict] = {}

# 觸發「查留言」的關鍵字
_FETCH_REPLY_KEYWORDS = ["留言", "陌生人", "有人回", "看看回覆", "查回覆"]


_LABELS = {
    "threads_post":      "Threads 發文",
    "threads_zutomayo":  "Threads ZUTOMAYO 倒數",
    "threads_reply":     "Threads 回覆留言",
    "threads_custom":    "Threads 指示動作",
}


async def send_draft_for_review(
    bot,
    content: str,
    type_: str,
    context: str = None,
    target_id: str = None,
    parent_draft_id: int = None,
) -> int | None:
    """
    將草稿發到管理員頻道，加上 ✅ / ❌ 反應等待審核。

    Returns:
        draft DB id，或 None（設定缺失 / 頻道找不到時）
    """
    from database import save_draft

    if not ADMIN_CHANNEL_ID:
        logger.warning("[admin] ADMIN_CHANNEL_ID 未設定，跳過審核")
        return None

    channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if not channel:
        logger.error("[admin] 找不到管理員頻道 %d", ADMIN_CHANNEL_ID)
        return None

    label = _LABELS.get(type_, type_)
    lines = [f"**[草稿 · {label}]**"]
    if context:
        snippet = context[:120] + ("…" if len(context) > 120 else "")
        lines.append(f"> {snippet}")
    lines.append("")
    lines.append(content)

    msg = await channel.send("\n".join(lines))
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    draft_id = save_draft(
        discord_message_id=str(msg.id),
        type_=type_,
        content=content,
        context=context,
        target_id=target_id,
        parent_draft_id=parent_draft_id,
    )
    logger.info("[admin] 草稿已送審：id=%d type=%s", draft_id, type_)
    return draft_id


async def handle_raw_reaction(payload, bot) -> None:
    """處理管理員在管理員頻道訊息上按下的反應。"""
    emoji = str(payload.emoji)
    msg_id = str(payload.message_id)

    # ✉️：對留言生成回覆草稿
    if emoji == "✉️":
        comment_info = _comment_map.pop(msg_id, None)
        if comment_info:
            await _generate_comment_reply(comment_info, bot)
        return

    # ✅ / ❌：草稿審核
    from database import get_draft_by_message_id, update_draft_status

    draft = get_draft_by_message_id(msg_id)
    if not draft or draft["status"] != "pending":
        return

    channel = bot.get_channel(ADMIN_CHANNEL_ID)

    if emoji == "✅":
        update_draft_status(draft["id"], "posted")
        await _execute_draft(draft, bot)

    elif emoji == "❌":
        update_draft_status(draft["id"], "rejected")
        if channel:
            msg = await channel.fetch_message(payload.message_id)
            prompt_msg = await msg.reply("給我修改建議，直接回覆這則訊息 ↑")
            _feedback_prompt_map[str(prompt_msg.id)] = msg_id


async def handle_admin_message(message, bot) -> None:
    """
    處理管理員在頻道裡發的訊息：
    - 回覆草稿或回覆「給我修改建議」的提示訊息 → 作為修改建議
    - 含「留言」等關鍵字 → 查貼文留言並展示
    - 其他直接輸入 → 作為 Threads 指示，生成後送審
    """
    from database import get_draft_by_message_id

    if message.reference:
        ref_id = str(message.reference.message_id)

        draft = get_draft_by_message_id(ref_id)
        if draft:
            await _handle_revision(draft, message.content, bot)
            return

        original_draft_msg_id = _feedback_prompt_map.get(ref_id)
        if original_draft_msg_id:
            draft = get_draft_by_message_id(original_draft_msg_id)
            if draft:
                _feedback_prompt_map.pop(ref_id, None)
                await _handle_revision(draft, message.content, bot)
                return

    # 查留言關鍵字觸發
    if any(k in message.content for k in _FETCH_REPLY_KEYWORDS):
        await fetch_and_show_replies(bot)
        return

    await _handle_custom_instruction(message.content, bot)


async def fetch_and_show_replies(bot) -> None:
    """
    抓加奈最近貼文下的非自己留言，每則在管理員頻道顯示一則訊息加 ✉️ 反應。
    管理員點 ✉️ → 生成回覆草稿 → 標準審核流程。
    """
    from threads import get_my_recent_posts, get_all_non_self_replies

    channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if not channel:
        return

    await channel.send("🔍 正在抓取最近貼文的留言…")

    posts = await get_my_recent_posts(limit=10)
    if not posts:
        await channel.send("❌ 無法取得貼文列表")
        return

    found = 0
    for post in posts:
        post_id = post.get("id")
        post_text = (post.get("text") or "")[:60]
        replies = await get_all_non_self_replies(post_id)

        for reply in replies:
            comment_id = reply.get("id")
            author = reply.get("username", "?")
            text = reply.get("text", "")
            timestamp = (reply.get("timestamp") or "")[:10]
            kana_said = reply.get("_kana_said")

            lines = [f"**[留言]** @{author}　{timestamp}"]
            lines.append(f"> 貼文：「{post_text}{'…' if len(post.get('text','')) > 60 else ''}」")
            if kana_said:
                lines.append(f"> Kana 說：「{kana_said}{'…' if len(kana_said) == 60 else ''}」")
            lines.append("")
            lines.append(text)

            msg = await channel.send("\n".join(lines))
            await msg.add_reaction("✉️")

            _comment_map[str(msg.id)] = {
                "comment_id": comment_id,
                "text": text,
                "author": author,
                "post_snippet": post_text,
                "kana_said": kana_said,
            }
            found += 1

    if found == 0:
        await channel.send("目前沒有非自己的留言")
    else:
        await channel.send(f"共 {found} 則留言，點 ✉️ 讓 Kana 生成回覆草稿")


async def _generate_comment_reply(comment_info: dict, bot) -> None:
    """依留言資訊生成回覆草稿，送管理員審核。"""
    from claude_client import get_client

    author = comment_info["author"]
    text = comment_info["text"]
    post_snippet = comment_info.get("post_snippet", "")

    prompt = (
        f"你在 Threads 上發了一篇貼文：「{post_snippet}」\n"
        f"有個陌生人 @{author} 留言：「{text}」\n\n"
        "請用加奈的口吻寫一則自然的回覆，直接輸出，不要說明。"
    )

    client = get_client()
    try:
        reply_text = await client.call(
            call_type="social_short",
            messages=[{"role": "user", "content": prompt}],
            dynamic_context=_load_threads_style(),
        )
        await send_draft_for_review(
            bot,
            content=reply_text.strip(),
            type_="threads_reply",
            context=f"@{author}：「{text[:80]}」",
            target_id=comment_info["comment_id"],
        )
    except Exception as e:
        logger.error("[admin] 留言回覆生成失敗：%s", e)
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if channel:
            await channel.send(f"❌ 生成失敗：{e}")


async def _handle_revision(draft: dict, feedback: str, bot) -> None:
    """基於原稿 + 修改建議重新生成，送審。"""
    from claude_client import get_client

    prompt = (
        f"以下是你寫的 Threads 草稿：\n\n{draft['content']}\n\n"
        f"修改建議：{feedback}\n\n"
        "請保留原本的風格和語氣，依照建議調整，直接輸出修改後的文字，不要說明。"
    )

    _append_style_note(feedback)

    client = get_client()
    try:
        revised = await client.call(
            call_type="social_short",
            messages=[{"role": "user", "content": prompt}],
            dynamic_context=_load_threads_style(),
        )
        await send_draft_for_review(
            bot,
            content=revised.strip(),
            type_=draft["type"],
            context=draft.get("context"),
            target_id=draft.get("target_id"),
            parent_draft_id=draft["id"],
        )
    except Exception as e:
        logger.error("[admin] 修改生成失敗：%s", e)
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if channel:
            await channel.send(f"❌ 修改失敗：{e}")


async def _handle_custom_instruction(instruction: str, bot) -> None:
    """依管理員指示生成 Threads 內容，送審。"""
    from claude_client import get_client

    prompt = (
        f"管理員給你的指示：{instruction}\n\n"
        "請用加奈的口吻，針對這個指示生成 Threads 文字（發文或回覆），"
        "直接輸出，不要說明。"
    )

    client = get_client()
    try:
        content = await client.call(
            call_type="social_short",
            messages=[{"role": "user", "content": prompt}],
            dynamic_context=_load_threads_style(),
        )
        await send_draft_for_review(
            bot,
            content=content.strip(),
            type_="threads_custom",
            context=instruction[:120],
        )
    except Exception as e:
        logger.error("[admin] 指示生成失敗：%s", e)
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if channel:
            await channel.send(f"❌ 生成失敗：{e}")


async def _execute_draft(draft: dict, bot) -> None:
    """執行審核通過的草稿（實際發文到 Threads）。"""
    from threads import post_thread, reply_to_thread, _split_post
    from database import add_self_memory

    type_ = draft["type"]
    content = draft["content"]
    target_id = draft.get("target_id")
    channel = bot.get_channel(ADMIN_CHANNEL_ID)

    try:
        if type_ == "threads_reply" and target_id:
            result = await reply_to_thread(content, target_id)
            if result:
                add_self_memory(
                    type_="threads_reply",
                    content=content,
                    mood_after="content",
                    tags=["threads", "reply"],
                )
                if channel:
                    await channel.send("✅ 已回覆留言")
            else:
                if channel:
                    await channel.send("❌ 回覆失敗（API 錯誤）")
        else:
            parts = _split_post(content)
            topic = "zutomayo" if type_ == "threads_zutomayo" else None
            thread_id = await post_thread(parts[0], topic_tag=topic)
            if not thread_id:
                if channel:
                    await channel.send("❌ 發文失敗（API 錯誤）")
                return

            for part in parts[1:]:
                await reply_to_thread(part, thread_id)

            tags = ["threads", type_]
            if type_ == "threads_zutomayo":
                tags.append("countdown")
            add_self_memory(
                type_=type_,
                content=content,
                mood_after="content",
                tags=tags,
            )
            logger.info("[admin] 已發文 thread_id=%s", thread_id)
            if channel:
                await channel.send(f"✅ 已發出")

    except Exception as e:
        logger.error("[admin] 執行草稿失敗：%s", e)
        if channel:
            await channel.send(f"❌ 執行失敗：{e}")
