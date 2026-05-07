"""
browse.py — 自主瀏覽邏輯

加奈的自主瀏覽行為：根據興趣和當前時間，抓取外部資訊並存入 memory_world。
- 09–11 點：暫停（tech_news stub）
- 11–13 點：arXiv 多模態 AI 論文
- 14–16 點：AniList 趨勢動漫
- 19–21 點：AniList 更新中的動漫
"""

import logging
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import certifi

logger = logging.getLogger(__name__)

# Windows 上 Python 的系統憑證庫不完整，用 certifi 的 CA bundle 統一處理
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())


async def autonomous_browse(state: dict) -> list[dict]:
    """
    根據加奈的興趣和當前時間，抓取外部資訊。
    最多處理 5 筆，讓 Claude 幫她「消化」成主觀反應，存入 memory_world。

    Returns:
        原始資料列表（供 proactive pipeline 判斷觸發情境）
    """
    hour = datetime.now(timezone.utc).astimezone().hour
    results = []

    if 11 <= hour < 13:
        logger.info("[browse] 時段 11-13：抓取 arXiv 論文")
        results += await _fetch_arxiv()

    if 14 <= hour <= 16:
        logger.info("[browse] 時段 14-16：抓取 AniList 趨勢動漫")
        results += await _fetch_anilist_trending()

    if 19 <= hour < 21:
        logger.info("[browse] 時段 19-21：抓取 AniList 更新中動漫")
        results += await _fetch_anilist_airing()

    if not results:
        logger.info("[browse] 本次無資料（本時段無排程或 fetch 失敗）")
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


# ── arXiv ────────────────────────────────────────────────────────────────────

ARXIV_QUERIES = [
    "ti:multimodal AND ti:alignment",
    "ti:cross-modal AND ti:alignment",
    "ti:vision-language AND ti:model",
]


async def _fetch_arxiv() -> list[dict]:
    """從 arXiv 抓取多模態 AI / 跨模態對齊相關論文（最新 3 篇）。"""
    import aiohttp

    import random
    query = random.choice(ARXIV_QUERIES)
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": query,
        "max_results": 5,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params,
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                text = await resp.text()

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(text)
        results = []
        for entry in root.findall("atom:entry", ns)[:3]:
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)
            id_el = entry.find("atom:id", ns)
            if title_el is None or id_el is None:
                continue
            title = title_el.text.strip().replace("\n", " ")
            summary = (summary_el.text or "").strip()[:300]
            link = id_el.text.strip()
            results.append({
                "source": "arxiv",
                "url": link,
                "title": title,
                "summary": summary,
                "tags": ["arxiv", "research", "multimodal"],
            })

        logger.info("[browse] arXiv 抓取成功：%d 篇", len(results))
        return results

    except Exception as e:
        logger.warning("[browse] arXiv 抓取失敗：%s", e)
        return []


# ── AniList ──────────────────────────────────────────────────────────────────

_ANILIST_URL = "https://graphql.anilist.co"

_TRENDING_QUERY = """
{
  Page(page: 1, perPage: 5) {
    media(type: ANIME, sort: TRENDING, isAdult: false) {
      title { native romaji }
      description(asHtml: false)
      siteUrl
      episodes
      meanScore
    }
  }
}
"""

_AIRING_QUERY = """
{
  Page(page: 1, perPage: 5) {
    media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC, isAdult: false) {
      title { native romaji }
      description(asHtml: false)
      siteUrl
      episodes
      meanScore
    }
  }
}
"""


async def _query_anilist(gql: str) -> list[dict]:
    """通用 AniList GraphQL 查詢，回傳 media 列表。"""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _ANILIST_URL,
                json={"query": gql},
                ssl=_SSL_CTX,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()

        media_list = (
            data.get("data", {})
                .get("Page", {})
                .get("media", [])
        )
        results = []
        for m in media_list[:3]:
            title = m["title"]["native"] or m["title"]["romaji"] or "（未知）"
            raw_desc = m.get("description") or ""
            # 去掉 HTML 標籤
            import re
            desc = re.sub(r"<[^>]+>", "", raw_desc).strip()[:250]
            url = m.get("siteUrl", "")
            score = m.get("meanScore")
            results.append({
                "source": "anilist",
                "url": url,
                "title": title,
                "summary": f"平均分：{score}/100\n{desc}" if score else desc,
                "tags": ["anime", "anilist"],
            })
        return results

    except Exception as e:
        logger.warning("[browse] AniList 查詢失敗：%s", e)
        return []


async def _fetch_anilist_trending() -> list[dict]:
    """抓取 AniList 趨勢動漫。"""
    results = await _query_anilist(_TRENDING_QUERY)
    logger.info("[browse] AniList 趨勢動漫：%d 筆", len(results))
    return results


async def _fetch_anilist_airing() -> list[dict]:
    """抓取 AniList 目前播出中的動漫。"""
    results = await _query_anilist(_AIRING_QUERY)
    logger.info("[browse] AniList 播出中動漫：%d 筆", len(results))
    return results


# ── 反應生成 ──────────────────────────────────────────────────────────────────

REACTION_SYSTEM = (
    "你是加奈，用加奈的口吻寫出她看到這個資訊後的想法（繁體中文，30字以內）。"
    "只輸出想法本身，不加引號、不加標籤。"
)


async def _generate_her_reaction(item: dict) -> str:
    """讓 Claude 幫加奈消化資訊，生成她的主觀反應。"""
    from claude_client import get_client

    source_label = {"arxiv": "論文", "anilist": "動漫"}.get(item.get("source", ""), "資訊")
    prompt = (
        f"【{source_label}】{item.get('title', '')}\n"
        f"{item.get('summary', '')[:200]}\n\n"
        "加奈看到這個的想法（30字以內）："
    )

    client = get_client()
    try:
        reaction = await client.call(
            call_type="browse",
            messages=[{"role": "user", "content": prompt}],
            system_override=REACTION_SYSTEM,
        )
        return reaction.strip()
    except Exception as e:
        logger.error("[browse] 生成反應失敗：%s", e)
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
    logger.info("[browse] 已存：%s", item.get("title", ""))
