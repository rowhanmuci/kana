"""
browse.py — 自主瀏覽邏輯

加奈的自主瀏覽行為：根據興趣和當前時間，抓取外部資訊並存入 memory_world。
目前為 stub 實作，保留完整架構，fetch 函數可逐步補齊。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── 瀏覽觸發條件（依時間） ─────────────────────────────────────────────────────
# schedule.md 定義：cron 0 9,11,14,16,19,21 * * *
BROWSE_SCHEDULE = {
    (9, 11):  "tech_news",
    (11, 13): "arxiv",
    (14, 16): "anime_community",
    (19, 21): "anime_updates",
}


async def autonomous_browse(state: dict) -> list[dict]:
    """
    根據加奈的興趣和當前時間，抓取外部資訊。
    最多處理 5 筆，讓 Claude 幫她「消化」成主觀反應，存入 memory_world。

    Args:
        state: 當前 persona_state dict

    Returns:
        原始資料列表（供 proactive pipeline 判斷觸發情境）
    """
    hour = datetime.now(timezone.utc).astimezone().hour
    results = []

    if 9 <= hour < 11:
        logger.info("[browse] 時段 09-11：抓取技術新聞")
        results += await _fetch_tech_news()

    if 11 <= hour < 13:
        logger.info("[browse] 時段 11-13：抓取 arXiv 論文")
        results += await _fetch_arxiv()

    if 14 <= hour < 16:
        logger.info("[browse] 時段 14-16：抓取 AniList 動態")
        results += await _fetch_anilist()

    if 19 <= hour < 21:
        logger.info("[browse] 時段 19-21：抓取追蹤動漫更新")
        results += await _fetch_anime_updates()

    if not results:
        logger.info("[browse] 本次無資料（fetch 函式尚為 stub 或本時段無排程）")
        return []

    # 每次最多消化 5 筆
    to_process = results[:5]
    logger.info("[browse] 開始消化 %d 筆資料", len(to_process))
    saved = 0
    for item in to_process:
        try:
            reaction = await _generate_her_reaction(item)
            _save_item_to_memory(item, reaction)
            saved += 1
        except Exception as e:
            logger.error("[browse] 項目處理失敗：%s", e)

    logger.info("[browse] 完成，成功存入 %d 筆 memory_world", saved)
    return to_process


# ── Fetch 函數（stub，可逐步實作） ────────────────────────────────────────────

async def _fetch_tech_news() -> list[dict]:
    """抓取技術新聞（RSS）。"""
    logger.info("[browse] fetch_tech_news（stub）")
    # TODO: 實作 RSS 抓取
    # import feedparser
    # feed = feedparser.parse("https://feeds.feedburner.com/TheHackersNews")
    # return [{"source": "hackernews", "url": e.link, "title": e.title, "summary": e.summary[:200]}
    #         for e in feed.entries[:3]]
    return []


async def _fetch_arxiv() -> list[dict]:
    """從 arXiv 抓取多模態 AI / 視覺語言模型相關論文。"""
    logger.info("[browse] fetch_arxiv（stub）")
    # TODO: 實作 arXiv API 查詢
    # import aiohttp
    # query = "multimodal+language+model+cross-modal+alignment"
    # url = f"https://export.arxiv.org/api/query?search_query={query}&max_results=3"
    # ...
    return []


async def _fetch_anilist() -> list[dict]:
    """抓取 AniList 近期動漫更新。"""
    logger.info("[browse] fetch_anilist（stub）")
    # TODO: 實作 AniList GraphQL API
    # QUERY = '''{ Page(page:1, perPage:5) { media(type:ANIME, sort:TRENDING) {
    #   title { romaji native } episodes meanScore siteUrl } } }'''
    # ...
    return []


async def _fetch_anime_updates() -> list[dict]:
    """抓取追蹤中動漫的最新集數。"""
    logger.info("[browse] fetch_anime_updates（stub）")
    # TODO: 整合 AniList 或 MyAnimeList API
    return []


# ── 反應生成 ──────────────────────────────────────────────────────────────────

REACTION_SYSTEM = "你是加奈，用加奈的口吻寫出她看到這個資訊後的想法（繁體中文，30字以內）。只輸出想法本身，不加引號。"


async def _generate_her_reaction(item: dict) -> str:
    """
    讓 Claude（Sonnet）幫加奈消化資訊，生成她的主觀反應。
    失敗時回傳預設字串。
    """
    from claude_client import get_client

    client = get_client()
    prompt = (
        f"標題：{item.get('title', '')}\n"
        f"摘要：{item.get('summary', '')[:300]}\n\n"
        "加奈看到這個的想法（30字以內）："
    )

    try:
        reaction = await client.call(
            call_type="browse",
            messages=[{"role": "user", "content": prompt}],
            system_override=REACTION_SYSTEM,
        )
        return reaction.strip()
    except Exception as e:
        logger.error("生成反應失敗：%s", e)
        return "（無反應）"


def _save_item_to_memory(item: dict, reaction: str) -> None:
    """將瀏覽結果和加奈的反應存入 memory_world。"""
    from database import save_world_memory
    import json

    tags = item.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [tags]

    save_world_memory(
        source=item.get("source", "unknown"),
        url=item.get("url", ""),
        title=item.get("title", ""),
        summary=item.get("summary", ""),
        her_reaction=reaction,
        tags=tags,
    )
    logger.info("瀏覽記憶已存：%s", item.get("title", ""))
