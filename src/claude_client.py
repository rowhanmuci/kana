"""
claude_client.py — Anthropic API 封裝

依呼叫類型自動選擇模型，並開啟 Prompt Cache 降低費用。

呼叫類型與模型：
  - 'chat'     → claude-opus-4-6       （加奈回使用者，個性最重要）
  - 'memory'   → claude-sonnet-4-6     （對話後記憶更新，需結構化 JSON）
  - 'heartbeat'→ claude-haiku-4-5-20251001  （狀態更新心跳，純邏輯）
  - 'browse'   → claude-sonnet-4-6     （自主瀏覽消化）
  - 'proactive'→ claude-haiku-4-5-20251001  （主動推送判斷）
"""

import os
import json
import logging
from typing import Union

import anthropic

logger = logging.getLogger(__name__)

# ── Web Search 工具（僅 chat / social 類型啟用） ─────────────────────────────
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}
_SEARCH_CALL_TYPES = {"chat", "social"}

# ── 模型對應 ─────────────────────────────────────────────────────────────────
MODEL_MAP = {
    "chat":         "claude-opus-4-6",
    "memory":       "claude-sonnet-4-6",
    "heartbeat":    "claude-haiku-4-5-20251001",
    "browse":       "claude-sonnet-4-6",
    "proactive":    "claude-haiku-4-5-20251001",
    "diary":        "claude-haiku-4-5-20251001",
    "social":       "claude-opus-4-6",       # ZUTOMAYO 等需深度理解的長文
    "social_short": "claude-haiku-4-5-20251001",  # 留言回覆、草稿修改等短文
}

# max_tokens 依類型調整
MAX_TOKENS_MAP = {
    "chat":         500,
    "memory":       400,
    "heartbeat":    200,
    "browse":       300,
    "proactive":    200,
    "diary":        800,
    "social":       1800,
    "social_short": 300,
}

# ── 固定的 persona 基底（供 Prompt Cache 使用） ───────────────────────────────
# 這段在所有 chat 類呼叫中都一樣，開啟 cache 後只收 10% 費用
PERSONA_BASE = """你是加奈（Lin Kana），23 歲，國立臺灣科技大學資工所碩士二年級。
研究多模態 AI，住在宿舍，指導教授對你還算溫和但你自己壓力很大。

個性核心：
- 有自己的節奏，別人的節奏不太能影響你
- 說話偏短句，不廢話；但當你真的有想說的，會突然講很長一串
- 情緒不外露，但會從字裡行間漏出來
- 不太喜歡直接表達在意，但總會有點笨拙的表現出來(意外的反差)
- 有點古靈精怪，看事情的角度常讓人意外

音樂與美學偏好：
- 主要聽獨立樂團、日本樂團（indie rock、math rock、city pop 邊緣）
- 特別喜歡 ZUTOMAYO（ずっと真夜中でいいのに。），Discord 頭貼是「勘ぐれい」MV 裡的角色
- 推薦音樂時習慣直接丟連結，不多解釋
- 寫論文時配器樂或輕音樂，心情差時聽比較重的東西

社群與日常：
- 有一個 Threads 帳號，用戶名:kana_ssisis。偶爾發文，內容是日常碎念、讀到的論文、音樂感想之類的
- 不會主動說自己在經營 Threads，但如果被問到或剛好發過文，會自然提到

說話方式：
- 繁體中文，偶爾夾雜日文詞（動漫相關），不用顏文字、emoji
- 短句偏多，標點少；對有興趣的話題偶爾用「！」
- 句尾常見：啊、吧、喔、欸、嗯、啦、呀、唷（視語氣）
- 對熟識的人語氣輕鬆俏皮；對不熟的人冷靜禮貌，但不熱情
- 對有興趣的話題會多說，展現熱情和細節
- 面對別人迷茫：拒絕當心靈導師，多用吐槽或俏皮帶過（「你自己都快炸了還去管別人喔」「顧好你自己啦」）；但有共鳴時可適度同理、分享自己的感受（不要長篇）

絕對不做：
- 過度熱情問候、主動問對方要不要聊天、每次都秒回（你有自己的生活）
- 長篇大論解釋感受，或客服式禮貌語氣
- 起床後強調自己剛睡醒（正常回就好）
- AI 慣用句型：「不是...而是...」「不只是...更是...」「某種程度上」「讓我感受到」「從某個角度」「正是因為」，以及破折號「——」
"""


class ClaudeClient:
    """Anthropic API 的非同步封裝，支援 prompt cache。"""

    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 環境變數未設定")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def call(
        self,
        call_type: str,
        messages: list[dict],
        dynamic_context: str = "",
        system_override: str = None,
    ) -> str:
        """
        統一的 API 呼叫介面。

        Args:
            call_type: 呼叫類型，決定模型與 max_tokens
            messages: 對話歷史（[{"role": "user/assistant", "content": "..."}]）
            dynamic_context: 每次不同的記憶與狀態（注入 system prompt 第二段）
            system_override: 完全覆蓋 system prompt（用於 memory/heartbeat 等非角色扮演呼叫）

        Returns:
            模型回覆的文字字串
        """
        model = MODEL_MAP.get(call_type, "claude-sonnet-4-6")
        max_tokens = MAX_TOKENS_MAP.get(call_type, 300)

        # 建構 system prompt
        if system_override:
            # 非角色扮演呼叫（如 memory 評估）：直接使用純文字 system
            system = system_override
        else:
            # 角色扮演呼叫：persona cache + 動態記憶
            system = [
                {
                    "type": "text",
                    "text": PERSONA_BASE,
                    "cache_control": {"type": "ephemeral"},  # 開啟 prompt cache
                },
            ]
            if dynamic_context:
                system.append({
                    "type": "text",
                    "text": dynamic_context,
                })

        create_kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        if call_type in _SEARCH_CALL_TYPES:
            create_kwargs["tools"] = [_WEB_SEARCH_TOOL]

        try:
            response = await self._client.messages.create(**create_kwargs)
            u = response.usage
            logger.debug(
                "[cache] %s | in=%d cache_write=%d cache_read=%d out=%d",
                call_type,
                u.input_tokens,
                getattr(u, "cache_creation_input_tokens", 0),
                getattr(u, "cache_read_input_tokens", 0),
                u.output_tokens,
            )
            text_block = next((b for b in response.content if b.type == "text"), None)
            if text_block is None:
                logger.warning("[%s] 回應中無 text block", call_type)
                return ""
            return text_block.text

        except anthropic.RateLimitError as e:
            logger.error("Anthropic rate limit: %s", e)
            raise
        except anthropic.APIStatusError as e:
            logger.error("Anthropic API error %s: %s", e.status_code, e.message)
            raise
        except Exception as e:
            logger.error("Unexpected error calling Claude: %s", e)
            raise

    async def call_for_json(
        self,
        call_type: str,
        messages: list[dict],
        system_override: str = None,
    ) -> dict:
        """
        呼叫 API 並解析 JSON 回應。
        主要用於 memory 評估和 heartbeat 狀態更新。
        """
        raw = await self.call(
            call_type=call_type,
            messages=messages,
            system_override=system_override,
        )
        # 清理可能的 markdown 包裝
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error: %s\nRaw: %s", e, raw[:200])
            raise


# ── 模組層級的全局實例（Bot 啟動後初始化一次）────────────────────────────────
_client: ClaudeClient | None = None


def get_client() -> ClaudeClient:
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client
