"""
database.py — SQLite 操作封裝

提供加奈專案所有資料表的 CRUD 操作。
資料表：persona_state, memory_self, memory_world, media_library,
        relationship, memory_conversation,
        pending_messages, message_log, pending_drafts
"""

import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

TAIPEI_TZ = timezone(timedelta(hours=8))


def now_taipei() -> str:
    """回傳台北時間的 ISO 字串，用於所有 DB 寫入。"""
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

# 預設用腳本所在目錄的上層（專案根目錄）下的 data/kana.db
# 這樣不管從哪個目錄執行 bot.py，路徑都固定
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "kana.db"
)
DATABASE_PATH = os.environ.get("DATABASE_PATH", _DEFAULT_DB_PATH)


def get_connection() -> sqlite3.Connection:
    """取得資料庫連線，啟用 WAL 模式和 row_factory。"""
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建立所有資料表（若不存在）並填入初始資料。"""
    os.makedirs(os.path.dirname(DATABASE_PATH) if os.path.dirname(DATABASE_PATH) else ".", exist_ok=True)

    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS persona_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            current_activity TEXT DEFAULT 'idle',
            current_mood TEXT DEFAULT 'content',
            energy_level INTEGER DEFAULT 70,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS memory_self (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            type TEXT,
            content TEXT,
            mood_after TEXT,
            tags TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS memory_world (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            url TEXT,
            title TEXT,
            summary TEXT,
            her_reaction TEXT,
            tags TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS relationship (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            first_met TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            familiarity INTEGER DEFAULT 0,
            affection INTEGER DEFAULT 0,
            relationship_stage TEXT DEFAULT 'stranger',
            inside_jokes TEXT DEFAULT '[]',
            known_facts TEXT DEFAULT '[]',
            last_mood_toward TEXT DEFAULT 'neutral'
        );

        CREATE TABLE IF NOT EXISTS memory_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary TEXT,
            emotional_moment TEXT,
            familiarity_delta INTEGER DEFAULT 0,
            affection_delta INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES relationship(user_id)
        );

        CREATE TABLE IF NOT EXISTS media_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT,
            author TEXT,
            score INTEGER,
            status TEXT DEFAULT 'done',
            completed_date TEXT,
            her_note TEXT,
            tags TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS pending_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            received_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours')),
            processed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', '+8 hours'))
        );

        CREATE TABLE IF NOT EXISTS pending_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_message_id TEXT UNIQUE,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            context TEXT,
            target_id TEXT,
            parent_draft_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        );
        """)

        # 初始化 persona_state（只有一筆）
        conn.execute("""
            INSERT OR IGNORE INTO persona_state (id, current_activity, current_mood,
                energy_level, last_updated)
            VALUES (1, 'idle', 'content', 70, CURRENT_TIMESTAMP)
        """)


# ─── persona_state ──────────────────────────────────────────────────────────

def get_persona_state() -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM persona_state WHERE id = 1").fetchone()
        return dict(row) if row else {}


def update_persona_state(**kwargs) -> None:
    """用關鍵字參數更新 persona_state。"""
    if not kwargs:
        return
    kwargs["last_updated"] = now_taipei()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values())
    with get_connection() as conn:
        conn.execute(
            f"UPDATE persona_state SET {set_clause} WHERE id = 1",
            values
        )


# ─── relationship ────────────────────────────────────────────────────────────

def get_relationship(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM relationship WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def ensure_user_exists(user_id: str, display_name: str) -> None:
    """新使用者自動初始化為 stranger。"""
    taipei_now = now_taipei()
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO relationship
                (user_id, display_name, first_met, last_interaction)
            VALUES (?, ?, ?, ?)
        """, (user_id, display_name, taipei_now, taipei_now))


def update_relationship(user_id: str, data: dict) -> None:
    """
    根據對話後評估結果更新 relationship 表。
    data 包含：familiarity_delta, affection_delta, new_known_facts,
               new_inside_jokes, mood_toward_user
    """
    with get_connection() as conn:
        rel = dict(conn.execute(
            "SELECT * FROM relationship WHERE user_id = ?", (user_id,)
        ).fetchone())

        new_familiarity = max(0, min(1000,
            rel["familiarity"] + data.get("familiarity_delta", 0)))
        new_affection = max(-500, min(1000,
            rel["affection"] + data.get("affection_delta", 0)))

        # 更新關係階段
        stage = _calculate_relationship_stage(new_familiarity, new_affection)

        # 更新 known_facts
        known_facts = json.loads(rel["known_facts"] or "[]")
        known_facts.extend(data.get("new_known_facts", []))
        known_facts = list(dict.fromkeys(known_facts))  # 去重

        # 更新 inside_jokes
        inside_jokes = json.loads(rel["inside_jokes"] or "[]")
        inside_jokes.extend(data.get("new_inside_jokes", []))

        conn.execute("""
            UPDATE relationship SET
                familiarity = ?,
                affection = ?,
                relationship_stage = ?,
                known_facts = ?,
                inside_jokes = ?,
                last_mood_toward = ?,
                last_interaction = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (
            new_familiarity,
            new_affection,
            stage,
            json.dumps(known_facts, ensure_ascii=False),
            json.dumps(inside_jokes, ensure_ascii=False),
            data.get("mood_toward_user", rel["last_mood_toward"]),
            user_id
        ))


def _calculate_relationship_stage(familiarity: int, affection: int) -> str:
    if affection > 100 and familiarity > 100:
        return "special"
    if affection > 70 and familiarity > 60:
        return "close"
    if affection > 30 and familiarity > 30:
        return "friend"
    if affection > 0 or familiarity > 10:
        return "acquaintance"
    if affection < -20:
        return "stranger"
    return "stranger"


def get_users_by_min_stage(min_stage: str) -> list[str]:
    """取得達到最低關係階段的使用者 ID 列表。"""
    stage_order = ["stranger", "acquaintance", "friend", "close", "special"]
    min_idx = stage_order.index(min_stage)
    valid_stages = stage_order[min_idx:]
    placeholders = ",".join("?" * len(valid_stages))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT user_id FROM relationship WHERE relationship_stage IN ({placeholders})",
            valid_stages
        ).fetchall()
    return [r["user_id"] for r in rows]


# ─── memory_conversation ────────────────────────────────────────────────────

def save_conversation_memory(user_id: str, data: dict) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO memory_conversation
                (created_at, user_id, summary, emotional_moment, familiarity_delta, affection_delta)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            now_taipei(),
            user_id,
            data.get("summary"),
            data.get("emotional_moment"),
            data.get("familiarity_delta", 0),
            data.get("affection_delta", 0),
        ))


def get_recent_conversations(user_id: str, limit: int = 5) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM memory_conversation
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ─── memory_self ────────────────────────────────────────────────────────────

def add_self_memory(type_: str, content: str,
                    mood_after: str = None, tags: list = None) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO memory_self (created_at, type, content, mood_after, tags)
            VALUES (?, ?, ?, ?, ?)
        """, (now_taipei(), type_, content, mood_after, json.dumps(tags or [], ensure_ascii=False)))


def get_recent_self_memories(limit: int = 7) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM memory_self
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_recent_self_memories_by_type(include_types: list[str], limit: int = 5) -> list[dict]:
    placeholders = ",".join("?" * len(include_types))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM memory_self WHERE type IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (*include_types, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_self_memories_excluding(exclude_types: list[str], limit: int = 7) -> list[dict]:
    placeholders = ",".join("?" * len(exclude_types))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM memory_self WHERE type NOT IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (*exclude_types, limit),
        ).fetchall()
    return [dict(r) for r in rows]



# ─── memory_world ────────────────────────────────────────────────────────────

def save_world_memory(source: str, url: str, title: str,
                      summary: str, her_reaction: str, tags: list = None) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO memory_world
                (created_at, source, url, title, summary, her_reaction, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now_taipei(), source, url, title, summary, her_reaction,
              json.dumps(tags or [], ensure_ascii=False)))


def world_memory_url_exists(url: str) -> bool:
    if not url:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM memory_world WHERE url = ? LIMIT 1", (url,)
        ).fetchone()
    return row is not None


def world_memory_urls_existing(urls: list[str]) -> set[str]:
    """一次查詢回傳已存在的 URL 集合。"""
    non_empty = [u for u in urls if u]
    if not non_empty:
        return set()
    placeholders = ",".join("?" * len(non_empty))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT url FROM memory_world WHERE url IN ({placeholders})",
            non_empty,
        ).fetchall()
    return {r["url"] for r in rows}


def get_recent_world_memories(limit: int = 5) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM memory_world
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ─── media_library ──────────────────────────────────────────────────────────

def query_media_by_types(types: list[str], min_score: int = 5,
                         limit: int = 5) -> list[dict]:
    placeholders = ",".join("?" * len(types))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT title, type, author, score, her_note FROM media_library "
            f"WHERE type IN ({placeholders}) AND score >= ? "
            f"ORDER BY score DESC LIMIT ?",
            types + [min_score, limit]
        ).fetchall()
    return [dict(r) for r in rows]


def query_high_score_media(min_score: int = 6, limit: int = 5) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title, type, author, score, her_note FROM media_library "
            "WHERE score >= ? ORDER BY RANDOM() LIMIT ?",
            (min_score, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── 熟悉度衰減（每日 cron 呼叫） ──────────────────────────────────────────

def decay_familiarity_all() -> None:
    """每天執行一次，讓長時間未互動的使用者熟悉度自然衰減。"""
    with get_connection() as conn:
        # 超過 30 天未互動 → -5
        conn.execute("""
            UPDATE relationship
            SET familiarity = MAX(0, familiarity - 5)
            WHERE julianday('now') - julianday(last_interaction) > 30
        """)
        # 超過 7 天未互動 → -2
        conn.execute("""
            UPDATE relationship
            SET familiarity = MAX(0, familiarity - 2)
            WHERE julianday('now') - julianday(last_interaction) > 7
              AND julianday('now') - julianday(last_interaction) <= 30
        """)


# ─── 工具函數 ────────────────────────────────────────────────────────────────

def log_proactive_message(user_id: str, trigger: str, response: str) -> None:
    """記錄加奈主動發出的訊息。"""
    add_self_memory(
        type_="daily_event",
        content=f"主動傳訊給 {user_id}，觸發：{trigger}",
        mood_after=None,
        tags=["proactive"]
    )


# ─── pending_messages（睡眠期間收到的訊息佇列） ──────────────────────────────

def save_pending_message(user_id: str, display_name: str, content: str) -> int:
    """將訊息存入佇列，回傳新記錄的 id。"""
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO pending_messages (user_id, display_name, content, received_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, display_name, content, now_taipei()))
        return cursor.lastrowid


def mark_single_pending_processed(message_id: int) -> None:
    """將單一 pending 訊息標記為已處理（用於忽略特定一則）。"""
    with get_connection() as conn:
        conn.execute("UPDATE pending_messages SET processed = 1 WHERE id = ?", (message_id,))


def get_pending_messages() -> list[dict]:
    """取得所有未處理的待回訊息，每個 user 只取最新一筆（避免重複回覆）。"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, user_id, display_name, content, received_at
            FROM pending_messages
            WHERE processed = 0
            GROUP BY user_id
            HAVING id = MAX(id)
            ORDER BY received_at ASC
        """).fetchall()
    return [dict(r) for r in rows]


def get_all_pending_for_user(user_id: str) -> list[dict]:
    """取得某使用者所有未處理的待回訊息，按時間排序。"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT content, received_at FROM pending_messages
            WHERE user_id = ? AND processed = 0
            ORDER BY received_at ASC
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_pending_count(user_id: str) -> int:
    """取得某使用者未讀訊息的總數量（用於組 prompt 告訴加奈）。"""
    with get_connection() as conn:
        return conn.execute("""
            SELECT COUNT(*) FROM pending_messages
            WHERE user_id = ? AND processed = 0
        """, (user_id,)).fetchone()[0]


def mark_pending_processed(user_id: str) -> None:
    """將某使用者的所有未處理訊息標為已處理。"""
    with get_connection() as conn:
        conn.execute("""
            UPDATE pending_messages SET processed = 1
            WHERE user_id = ? AND processed = 0
        """, (user_id,))


# ─── pending_drafts（Threads 草稿審核佇列） ───────────────────────────────────

def save_draft(
    discord_message_id: str, type_: str, content: str,
    context: str = None, target_id: str = None,
    parent_draft_id: int = None,
) -> int:
    """儲存草稿，回傳新記錄的 id。"""
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO pending_drafts
            (discord_message_id, type, content, context, target_id, parent_draft_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (discord_message_id, type_, content, context, target_id, parent_draft_id, now_taipei()))
        return cursor.lastrowid


def get_draft_by_message_id(discord_message_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pending_drafts WHERE discord_message_id = ?",
            (discord_message_id,),
        ).fetchone()
    return dict(row) if row else None


def update_draft_status(draft_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pending_drafts SET status = ? WHERE id = ?",
            (status, draft_id),
        )


# ─── message_log（原始對話紀錄，供對話歷史使用） ──────────────────────────────

def save_message_log(user_id: str, role: str, content: str) -> None:
    """儲存一則原始訊息（role: 'user' 或 'assistant'）。"""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO message_log (user_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, role, content, now_taipei()))


def get_message_log(user_id: str, limit: int = 20) -> list[dict]:
    """取得最近 N 則原始訊息（時間正序），用於重建對話歷史。"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT role, content FROM message_log
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]
