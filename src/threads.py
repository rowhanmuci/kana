"""
threads.py — 加奈的 Threads 自主發文

Threads API 發文兩步驟：
  1. 建立 media container（回傳 creation_id）
  2. 發布 container（回傳 thread_id）
"""

import asyncio
import logging
import os
import ssl
from datetime import date

import aiohttp
import certifi

from audio import analyze_youtube_audio

logger = logging.getLogger(__name__)

_THREADS_BASE = "https://graph.threads.net/v1.0"
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# ── ZUTOMAYO 演唱會倒數 ────────────────────────────────────────────────────────

# ZUTOMAYO INTENSE II 台北場：2026/5/16
_CONCERT_DATE = date(2026, 5, 16)

# 倒數 9 首歌（5/7–5/15），按倒數天數排列（index 0 = 距演唱會最遠）
# 資料來源：官網 zutomayo.net、Wikipedia、各動畫主題曲公告
ZUTOMAYO_COUNTDOWN = [
    {
        "title": "間人間",
        "romaji": "Ma Ningen",
        "context": "2026年3月25日發行的第四張全長專輯《形藻土》收錄曲，track 02，是最新專輯的作品。",
        "lyrics_url": "https://www.uta-net.com/song/390000/",
        "mv_id": "Pd2HGnmkJ20",
    },
    {
        "title": "お勉強しといてよ",
        "romaji": "Obenkyou Shite Oite yo",
        "context": "2018年第一張EP《正しい偽りからの起床》收錄曲，早期代表作。",
        "lyrics_url": "https://www.uta-net.com/song/285644/",
        "mv_id": "Atvsg_zogxo",
    },
    {
        "title": "ハゼ馳せる果てるまで",
        "romaji": "Haze Hashiru Hate Ru Made",
        "context": "2019年首張全長專輯《潜潜話》收錄曲，是現場常見曲目之一。",
        "lyrics_url": "https://www.uta-net.com/song/276699/",
        "mv_id": "ElnxZtiBDvs",
    },
    {
        "title": "メディアノーチェ",
        "romaji": "Medianoche",
        "context": "2026年3月25日發行的第四張全長專輯《形藻土》收錄曲，西班牙語「午夜」之意。",
        "lyrics_url": "https://www.uta-net.com/song/386977/",
        "mv_id": "sBpITQ7oXxM",
    },
    {
        "title": "残機",
        "romaji": "Zanki",
        "context": "2022年《チェンソーマン》（Chainsaw Man）片尾曲，讓大量海外聽眾認識ずとまよ。",
        "lyrics_url": "https://www.uta-net.com/song/326599/",
        "mv_id": "6OC92oxs4gA",
    },
    {
        "title": "綺羅キラー(feat. Mori Calliope)",
        "romaji": "Kira Killer",
        "context": "與虛擬主播(Mori Calliope) 合作的歌曲，展現了獨特的流行電音風格與快嘴饒舌。",
        "lyrics_url": "https://www.uta-net.com/song/329692/",
        "mv_id": "e5LaKxJVeVI",
    },

    {
        "title": "秒針を噛む",
        "romaji": "Byoushin wo Kamu",
        "context": "2018年出道曲，ACAね與ぬゆり共同作曲。上傳首週就衝到20萬次觀看，是很多人認識ずとまよ的起點。",
        "lyrics_url": "https://www.uta-net.com/song/268870/",
        "mv_id": "GJI4Gv7NbmE",
    },
    {
        "title": "TAIDADA",
        "romaji": "TAIDADA",
        "context": "2024年《ダンダダン》（Dandadan）片尾曲。",
        "lyrics_url": "https://www.uta-net.com/song/361303/",
        "mv_id": "IeyCdm9WwXM",
    },
    {
        "title": "勘ぐれい",
        "romaji": "Kangure i",
        "context": "MV視覺風格印象深刻，加奈的 Discord 頭貼就是選自這支 MV。",
        "lyrics_url": "https://www.uta-net.com/song/294388/",
        "mv_id": "ugpywe34_30",
    },
]

_ACANE_INFO = (
    "ACAね（本名推測為加藤茜）：ZUTOMAYO 的作詞、作曲、主唱，1998年生，新潟出身。"
    "高中時曾做過平面模特兒，後來透過路上演唱積累受眾。"
    "以沙啞兼高音並存的獨特嗓音著稱，公開演出時全程遮臉，身分保持低調。"
    "2018年6月4日以《秒針を噛む》出道，上傳首週就衝到20萬次觀看。"
)

ZUTOMAYO_POST_SYSTEM = f"""你是加奈，資工所碩士生，ZUTOMAYO 死忠粉絲，5/16 台北 INTENSE II 演唱會票已入手，非常興奮。
全程繁體中文，不使用簡體字。不用 emoji、顏文字、hashtag、條列格式。

關於 ACAね：{_ACANE_INFO}

你的任務：根據提供的歌詞，寫一則類似樂迷介紹文的 Threads 發文，約 350–800 字。

格式：
第一行固定：ZUTOMAYO 5/16台北 倒數第X天 ／ 歌名（X填實際天數）
空行後接正文。

寫法：
- 口吻熱情，你真的很喜歡這首歌，讓讀者感受到這種熱度
- 接著把想分享的歌詞集中放在同一個區塊，雙語對照格式（日文 中文），逐行列出，例如：
  灰に潜り　秒針を噛み 潛入灰燼，把秒針咬碎
  でも壊れない　止まってくれない 但它不碎，也不停
  中文翻譯口語自然，不逐字硬譯
- 歌詞區塊後寫這些句子打動你什麼，具體說，熱情說
- 若有提供 YouTube 留言，可以引用或呼應粉絲的感受，當作共鳴的切入點
- 絕對避免這些 AI 慣用句型：「不是...而是...」「不只是...更是...」「太狠」「更狠了」破折號「——」等
- 把留言得到的資訊內化成自己的想法，不要直接引用留言原文（除非是特別精彩的留言），也不要說「有粉絲留言說...」
- 嚴禁捏造 ACAね 的嗓音或演唱細節（如「聲音是裂的」「高音爆發」等），除非歌詞本身有明確描述；若有 YouTube 留言提到聲音感受，才可以呼應
- 若提供了音訊分析資料，使用方式如下：
  - 調性（如 C minor）：可以直接說，是樂迷實際會講的資訊
  - 音域（如 D3～A3）：可以直接說，是具體的音樂理論描述
  - BPM：引用大概數字即可(不要精準的數字)，轉換成節奏感受
  - 音量高峰時間點：不要引用秒數，轉換成段落或情緒描述
  - 能量分佈：轉換成整首歌的張力走勢，不要直接說「前段中後段中」
  - 器樂層次：轉換成對整首歌音色或氛圍的感受
  - 人聲動態：轉換成歌手情緒起伏的感受
- Whisper 轉錄輔助理解歌詞時間結構，若與正式歌詞有出入，以正式歌詞為準"""


MAX_DAILY_POSTS = 3

POST_SYSTEM = (
    "你是加奈，用她的口吻寫一則 Threads 貼文（繁體中文）。"
    "貼文要自然、口語，像真人在隨手打的一段話，不超過 100 字。"
    "絕對不能用 emoji、顏文字、hashtag、條列格式。"
    "句子要短，語氣平淡帶點個人觀點，不要過度熱情。"
)


_THREAD_CHAR_LIMIT = 490  # Threads 上限 500，留 10 字緩衝


def _split_post(text: str) -> list[str]:
    """將文字切成最多 3 段，每段不超過字元上限，優先在段落邊界切割。"""
    parts = []
    remaining = text.strip()
    while remaining and len(parts) < 3:
        if len(remaining) <= _THREAD_CHAR_LIMIT:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, _THREAD_CHAR_LIMIT)
        if cut == -1:
            cut = remaining.rfind("\n", 0, _THREAD_CHAR_LIMIT)
        if cut == -1:
            cut = _THREAD_CHAR_LIMIT
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return parts


def get_countdown_song() -> dict | None:
    """
    若今天在倒數窗口（5/7–5/15）內，回傳今天對應的歌；否則回傳 None。
    以距演唱會天數決定索引，確保每天固定對應同一首歌。
    """
    today = date.today()
    days_left = (_CONCERT_DATE - today).days
    # 倒數窗口：1 <= days_left <= 9（對應 5/15 到 5/7）
    if not (1 <= days_left <= len(ZUTOMAYO_COUNTDOWN)):
        return None
    # days_left=9 → index 0（最早），days_left=1 → index 8（最後）
    idx = len(ZUTOMAYO_COUNTDOWN) - days_left
    song = ZUTOMAYO_COUNTDOWN[idx].copy()
    song["days_left"] = days_left
    return song


async def _fetch_lyrics(url: str) -> str:
    """從 uta-net.com 抓取歌詞文字。"""
    import re
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                html = await resp.text(encoding="utf-8", errors="replace")

        # uta-net 歌詞區塊在 id="kashi_area" 的 div 內
        match = re.search(r'id="kashi_area"[^>]*>(.*?)</div>', html, re.DOTALL)
        if not match:
            return ""
        raw = match.group(1)
        # 移除 HTML 標籤，保留換行
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", "", raw)
        lyrics = raw.strip()
        logger.info("[threads] 歌詞抓取成功：%d 字", len(lyrics))
        return lyrics
    except Exception as e:
        logger.warning("[threads] 歌詞抓取失敗：%s", e)
        return ""


async def _fetch_youtube_comments(video_id: str) -> list[str]:
    """抓取 YouTube MV 留言前 15 則（按相關性排序）。無 API key 時回傳空清單。"""
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.info("[threads] YOUTUBE_API_KEY 未設定，跳過留言抓取")
        return []
    url = (
        "https://www.googleapis.com/youtube/v3/commentThreads"
        f"?part=snippet&videoId={video_id}&maxResults=15&order=relevance&key={api_key}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, ssl=_SSL_CTX, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
        items = data.get("items", [])
        comments = []
        for item in items:
            text = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {}).get("textDisplay", "")
            text = text.strip()
            if text:
                comments.append(text[:200])
        logger.info("[threads] YouTube 留言抓取：%d 則", len(comments))
        return comments
    except Exception as e:
        logger.warning("[threads] YouTube 留言抓取失敗：%s", e)
        return []


async def generate_zutomayo_post(song: dict) -> str:
    """並行抓取歌詞、YouTube 留言、音訊分析，生成加奈的倒數貼文。"""
    from claude_client import get_client

    days_left = song["days_left"]
    mv_id = song.get("mv_id")

    async def _empty_str(): return ""
    async def _empty_list(): return []
    async def _empty_dict(): return {}

    lyrics, comments, audio = await asyncio.gather(
        _fetch_lyrics(song["lyrics_url"]) if song.get("lyrics_url") else _empty_str(),
        _fetch_youtube_comments(mv_id) if mv_id else _empty_list(),
        analyze_youtube_audio(mv_id) if mv_id else _empty_dict(),
    )

    lyrics_section = f"\n\n【歌詞】\n{lyrics[:800]}" if lyrics else ""

    comments_section = ""
    if comments:
        comments_section = "\n\n【YouTube 粉絲留言（參考用）】\n" + "\n".join(f"- {c}" for c in comments[:8])

    audio_section = ""
    if audio:
        lines = ["\n\n【音訊分析】"]
        if audio.get("duration_str"):
            lines.append(f"曲長：{audio['duration_str']}")
        if audio.get("key"):
            lines.append(f"調性：{audio['key']}")
        if audio.get("tempo"):
            lines.append(f"節拍速度（僅供感受參考，勿直接引用數字）：{audio['tempo']} BPM")
        if audio.get("vocal_range"):
            vr = audio["vocal_range"]
            lines.append(f"旋律音域：{vr['low']} ～ {vr['high']}")
        if audio.get("energy_desc"):
            lines.append(f"能量分佈（轉換成走勢描述，勿直接引用）：{audio['energy_desc']}")
        if audio.get("peak_str"):
            lines.append(f"音量高峰時間點（勿直接引用秒數，用段落感受描述）：{audio['peak_str']}")
        if audio.get("drum_char"):
            lines.append(f"鼓組：{audio['drum_char']}")
        if audio.get("bass_root"):
            lines.append(f"貝斯主根音：{audio['bass_root']}")
        if audio.get("other_char"):
            lines.append(f"器樂層次（轉換成音色或氛圍描述，勿直接引用）：{audio['other_char']}")
        if audio.get("vocal_dynamic"):
            lines.append(f"人聲動態（轉換成情緒起伏描述，勿直接引用）：{audio['vocal_dynamic']}")
        if audio.get("transcript"):
            lines.append(f"\nWhisper 轉錄（自動辨識，可能有誤，以正式歌詞為準）：\n{audio['transcript'][:500]}")
        audio_section = "\n".join(lines)
    
    prompt = (
        f"距離演唱會還有 {days_left} 天。\n\n"
        f"今天的歌：{song['title']}（{song['romaji']}）\n"
        f"背景：{song['context']}"
        f"{lyrics_section}"
        f"{comments_section}"
        f"{audio_section}\n\n"
        "根據以上，寫加奈的倒數貼文。"
    )
    client = get_client()
    try:
        text = await client.call(
            call_type="social",
            messages=[{"role": "user", "content": prompt}],
            system_override=ZUTOMAYO_POST_SYSTEM,
        )
        return text.strip()
    except Exception as e:
        logger.error("[threads] 倒數貼文生成失敗：%s", e)
        return ""


async def zutomayo_daily_post() -> tuple[dict, str] | None:
    """
    每日倒數發文：若今天在倒數窗口內，生成草稿並回傳 (song, text)。
    不直接發文——由 bot.py 的排程包裝器送管理員審核。
    """
    song = get_countdown_song()
    if not song:
        logger.info("[threads] 今天不在 ZUTOMAYO 倒數窗口，跳過")
        return None

    if _get_today_zutomayo_count() > 0:
        logger.info("[threads] 今天已發過 ZUTOMAYO 倒數文，跳過")
        return None

    logger.info("[threads] 倒數第 %d 天，歌曲：%s", song["days_left"], song["title"])
    text = await generate_zutomayo_post(song)
    if not text:
        return None

    return song, text


def _get_today_zutomayo_count() -> int:
    from database import get_connection
    today = date.today().isoformat()
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM memory_self WHERE type = 'threads_zutomayo' AND created_at >= ?",
            (f"{today} 00:00:00",),
        ).fetchone()[0]


_MY_THREADS_USERNAME = "kana_ssisis"


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user_id = os.environ.get("THREADS_USER_ID")
    if not token or not user_id:
        raise ValueError("THREADS_ACCESS_TOKEN 或 THREADS_USER_ID 未設定")
    return token, user_id


async def get_my_recent_posts(limit: int = 10) -> list[dict]:
    """取得加奈最近的 Threads 貼文列表（不含回覆串）。"""
    try:
        token, user_id = _get_credentials()
    except ValueError as e:
        logger.error("[threads] %s", e)
        return []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{_THREADS_BASE}/{user_id}/threads",
                params={
                    "fields": "id,text,timestamp,permalink",
                    "limit": limit,
                    "access_token": token,
                },
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error("[threads] 取貼文列表失敗：%s", e)
            return []

    posts = data.get("data", [])
    logger.info("[threads] 取得 %d 則貼文", len(posts))
    return posts


async def _fetch_replies_raw(thread_id: str) -> list[dict]:
    """取得 thread 的直接回覆（不過濾）。"""
    try:
        token, _ = _get_credentials()
    except ValueError as e:
        logger.error("[threads] %s", e)
        return []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{_THREADS_BASE}/{thread_id}/replies",
                params={
                    "fields": "id,text,username,timestamp",
                    "access_token": token,
                },
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error("[threads] 取留言失敗 thread=%s：%s", thread_id, e)
            return []

    return data.get("data", [])


async def get_post_replies(post_id: str) -> list[dict]:
    """取得指定貼文下的留言，排除加奈自己的回覆。"""
    replies = await _fetch_replies_raw(post_id)
    others = [r for r in replies if r.get("username") != _MY_THREADS_USERNAME]
    logger.info("[threads] post=%s 共 %d 則非自己留言", post_id, len(others))
    return others


async def get_all_non_self_replies(post_id: str) -> list[dict]:
    """
    取得貼文下所有非自己的留言，包含：
    1. 直接回覆貼文的非自己留言
    2. 加奈自己的留言底下，別人再回覆的留言（附上加奈原本說了什麼）
    """
    all_direct = await _fetch_replies_raw(post_id)

    others = [r for r in all_direct if r.get("username") != _MY_THREADS_USERNAME]

    kana_comments = [r for r in all_direct if r.get("username") == _MY_THREADS_USERNAME]
    for kc in kana_comments:
        sub = await _fetch_replies_raw(kc["id"])
        for r in sub:
            if r.get("username") != _MY_THREADS_USERNAME:
                r["_kana_said"] = kc.get("text", "")[:60]
                others.append(r)

    logger.info("[threads] post=%s 共收集 %d 則非自己留言（含巢狀）", post_id, len(others))
    return others


async def post_thread(text: str, topic_tag: str | None = None) -> str | None:
    """發布一則文字貼文。回傳 thread_id，失敗回傳 None。"""
    try:
        token, user_id = _get_credentials()
    except ValueError as e:
        logger.error("[threads] %s", e)
        return None

    async with aiohttp.ClientSession() as session:
        # Step 1: 建立 container
        params = {"media_type": "TEXT", "text": text, "access_token": token}
        if topic_tag:
            params["topic_tag"] = topic_tag
        try:
            async with session.post(
                f"{_THREADS_BASE}/{user_id}/threads",
                params=params,
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
            creation_id = data.get("id")
            if not creation_id:
                logger.error("[threads] 建立 container 失敗：%s", data)
                return None
        except Exception as e:
            logger.error("[threads] container 建立異常：%s", e)
            return None

        # Step 2: 發布
        try:
            async with session.post(
                f"{_THREADS_BASE}/{user_id}/threads_publish",
                params={"creation_id": creation_id, "access_token": token},
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
            thread_id = data.get("id")
            if not thread_id:
                logger.error("[threads] 發布失敗：%s", data)
                return None
            logger.info("[threads] 發文成功：thread_id=%s", thread_id)
            return thread_id
        except Exception as e:
            logger.error("[threads] 發布異常：%s", e)
            return None


async def reply_to_thread(text: str, reply_to_id: str) -> str | None:
    """回覆指定貼文。回傳新 thread_id，失敗回傳 None。"""
    import asyncio
    try:
        token, user_id = _get_credentials()
    except ValueError as e:
        logger.error("[threads] %s", e)
        return None

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{_THREADS_BASE}/{user_id}/threads",
                params={
                    "media_type": "TEXT",
                    "text": text,
                    "reply_to_id": reply_to_id,
                    "access_token": token,
                },
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
            creation_id = data.get("id")
            if not creation_id:
                logger.error("[threads] 回覆 container 失敗：%s", data)
                return None
        except Exception as e:
            logger.error("[threads] 回覆 container 異常：%s", e)
            return None

        # Threads API 需要等 reply container 索引完成才能 publish
        await asyncio.sleep(10)

        try:
            async with session.post(
                f"{_THREADS_BASE}/{user_id}/threads_publish",
                params={"creation_id": creation_id, "access_token": token},
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
            thread_id = data.get("id")
            if not thread_id:
                logger.error("[threads] 回覆發布失敗：%s", data)
                return None
            logger.info("[threads] 回覆成功：%s → %s", reply_to_id, thread_id)
            return thread_id
        except Exception as e:
            logger.error("[threads] 回覆發布異常：%s", e)
            return None


async def generate_post(browse_items: list[dict], state: dict) -> str:
    """根據最近的記憶、瀏覽結果和當前狀態，生成加奈會發的 Threads 貼文。"""
    from claude_client import get_client
    from database import get_recent_self_memories, get_recent_world_memories

    activity = state.get("current_activity", "idle")
    mood = state.get("current_mood", "content")
    energy = state.get("energy_level", 70)

    # 收集最近的活動記憶（日記、日常事件）
    self_memories = get_recent_self_memories(limit=5)
    recent_events = [
        m["content"] for m in self_memories
        if m.get("type") in ("diary", "threads_post", "writing_thesis","threads_zutomayo") and m.get("content")
    ]

    # 收集最近的世界記憶（瀏覽結果）
    world_memories = get_recent_world_memories(limit=3)

    # 組裝素材區塊
    parts = [f"加奈現在：心情「{mood}」，體力 {energy}/100，活動「{activity}」"]

    if recent_events:
        events_text = "\n".join(f"- {e[:80]}" for e in recent_events[:3])
        parts.append(f"最近做的事：\n{events_text}")

    if browse_items:
        item = browse_items[0]
        source_label = {"arxiv": "論文", "anilist": "動漫"}.get(item.get("source", ""), "資訊")
        parts.append(
            f"剛看到的{source_label}：《{item.get('title', '')}》\n"
            f"她的感想：{item.get('her_reaction', item.get('summary', ''))[:100]}"
        )
    elif world_memories:
        wm = world_memories[0]
        source_label = {"arxiv": "論文", "anilist": "動漫"}.get(wm.get("source", ""), "資訊")
        parts.append(
            f"最近看到的{source_label}：《{wm.get('title', '')}》\n"
            f"她的感想：{wm.get('her_reaction', '')[:100]}"
        )

    context = "\n\n".join(parts)
    prompt = (
        f"{context}\n\n"
        "參考以上，寫一則加奈會發到 Threads 的貼文，不超過 100 字。"
        "參考最近的貼文，不要每篇都差不多，盡量多點變化、和古怪有趣的想法"
        "內容若是提及自己看的作品，要考究內容，勿捏造"
        "內容不一定要很具體，可以無意義，可以隨意發散，但要讓人感覺這是加奈的真實想法。"
    )

    client = get_client()
    try:
        text = await client.call(
            call_type="diary",
            messages=[{"role": "user", "content": prompt}],
            system_override=POST_SYSTEM,
        )
        return text.strip()
    except Exception as e:
        logger.error("[threads] 生成貼文失敗：%s", e)
        return ""


def _get_today_post_count() -> int:
    """查今天已發了幾篇貼文（從 memory_self 中計算）。"""
    from database import get_connection
    from datetime import date

    today = date.today().isoformat()
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_self WHERE type = 'threads_post' AND created_at >= ?",
            (f"{today} 00:00:00",),
        ).fetchone()[0]
    return count


async def autonomous_post(browse_results: list[dict], state: dict) -> str | None:
    """
    根據瀏覽結果和當前狀態，自主決定是否生成 Threads 草稿。
    回傳草稿文字供管理員審核，決定不發時回傳 None。
    不直接發文。
    """
    import random

    energy = state.get("energy_level", 70)
    activity = state.get("current_activity", "idle")

    if activity == "sleeping" or energy < 30:
        logger.info("[threads] 跳過發文（activity=%s, energy=%d）", activity, energy)
        return None

    today_count = _get_today_post_count()
    if today_count >= MAX_DAILY_POSTS:
        logger.info("[threads] 今天已發 %d 篇，跳過", today_count)
        return None

    threshold = 0.6 if browse_results else 0.2
    if random.random() > threshold:
        logger.info("[threads] 本次隨機決定不發文")
        return None

    text = await generate_post(browse_results, state)
    return text if text else None
