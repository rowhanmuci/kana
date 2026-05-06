"""
database.py — SQLite 操作封裝

提供加奈專案所有資料表的 CRUD 操作。
資料表：persona_state, memory_self, memory_world,
        relationship, memory_conversation, media_library
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
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
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
        """)

        # 初始化 persona_state（只有一筆）
        conn.execute("""
            INSERT OR IGNORE INTO persona_state (id, current_activity, current_mood,
                energy_level, last_updated)
            VALUES (1, 'idle', 'content', 70, CURRENT_TIMESTAMP)
        """)

    _seed_media_library()


def _seed_media_library():
    """將 media_taste.md 的作品資料批次寫入（若表格已有資料則跳過）。"""
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM media_library").fetchone()[0]
        if count > 0:
            return  # 已有資料，跳過

        media_data = [
            # ── 書 ──────────────────────────────────────────────────────────
            ("挪威的森林", "book", "村上春樹", 7, "done",
             "讀完之後靜了很久", '["村上春樹","孤獨","愛情"]'),
            ("生命中不能承受之輕", "book", "米蘭·昆德拉", 7, "done",
             "什麼是輕什麼是重，讀完更不確定了", '["昆德拉","哲學","愛情"]'),
            ("安娜·卡列尼娜", "book", "列夫·托爾斯泰", 7, "done",
             "讀很久但值得", '["托爾斯泰","俄國文學","悲劇"]'),
            ("異鄉人", "book", "卡繆", 7, "done",
             "有時候覺得自己也是", '["卡繆","荒謬","孤獨"]'),
            ("一九八四", "book", "喬治·歐威爾", 6, "done",
             None, '["反烏托邦","政治"]'),
            ("國境之南·太陽之西", "book", "村上春樹", 6, "done",
             None, '["村上春樹","愛情"]'),
            ("人造衛星情人", "book", "村上春樹", 6, "done",
             None, '["村上春樹","孤獨"]'),
            ("沒有女人的男人們", "book", "村上春樹", 6, "done",
             "每篇都在說同一件事但每次都有不同感覺", '["村上春樹","短篇","男女"]'),
            ("1Q84（全三冊）", "book", "村上春樹", 6, "done",
             None, '["村上春樹","奇幻"]'),
            ("世界末日與冷酷異境", "book", "村上春樹", 5, "done",
             None, '["村上春樹","奇幻"]'),
            ("舞·舞·舞", "book", "村上春樹", 6, "done",
             None, '["村上春樹"]'),
            ("聽風的歌", "book", "村上春樹", 5, "done",
             None, '["村上春樹"]'),
            ("接近無限透明的藍", "book", "村上龍", 6, "done",
             "有點不舒服但停不下來", '["村上龍","頹廢"]'),
            ("聊天紀錄", "book", "莎莉·魯尼", 6, "done",
             None, '["當代小說","愛情"]'),
            ("廚房", "book", "吉本芭娜娜", 5, "done",
             "很短但讀完後有點難過", '["日本文學","孤獨"]'),
            ("變形記", "book", "法蘭茲·卡夫卡", 4, "done",
             None, '["卡夫卡","荒謬"]'),
            ("人間失格", "book", "太宰治", 3, "done",
             "讀的時候很煩躁，但懂他在說什麼", '["太宰治","頹廢"]'),
            ("鎧甲的裂縫", "book", "安娜·戈華達", 6, "done",
             None, '["法國文學"]'),
            ("55歲開始的Hello Life", "book", "村上龍", 5, "done",
             None, '["村上龍"]'),
            ("晚安晚安", "book", "陸穎魚", 5, "done",
             None, '["詩","華語"]'),
            ("尋羊冒險記", "book", "村上春樹", 4, "done",
             None, '["村上春樹"]'),
            ("我沒死，只是變成了掃地機器人", "book", "添田信", 3, "done",
             "睡前讀的，還好", '["日本","輕鬆"]'),
            ("親愛的提奧", "book", "文森·梵谷", 6, "done",
             "補充，不在原始清單", '["書信","藝術"]'),
            ("局外人（重讀版）", "book", "卡繆", None, "in_progress",
             "補充，目前在讀", '["卡繆","荒謬"]'),

            # ── 動漫 ────────────────────────────────────────────────────────
            ("寄生獸", "anime", "岩明均", 6, "done",
             "共生還是佔領，說的其實是人", '["科幻","哲學"]'),
            ("葬送的芙莉蓮", "anime", "山田鐘人", 5, "done",
             "時間感很特別", '["奇幻","時間"]'),
            ("失憶投捕", "anime", "美川繪子", 6, "done",
             None, '["棒球","愛情"]'),
            ("敗北女角太多了！", "anime", "雨森たきび", 6, "done",
             "比想像中有深度", '["戀愛","校園"]'),
            ("孤獨搖滾", "anime", None, 3, "done",
             "畫面很好看但劇情普通", '["音樂","校園"]'),
            ("奧術 S2", "anime", "克里斯汀·林克", 5, "done",
             None, '["奇幻","遊戲改編"]'),
            ("無職轉生 S2", "anime", None, 6, "done",
             None, '["異世界"]'),
            ("火之鳥：伊甸17", "anime", "手塚治虫", 5, "done",
             None, '["手塚治虫","科幻"]'),
            ("地。-關於地球的運動-", "anime", "魚豐", 6, "done",
             "執著到底是什麼", '["歷史","哲學"]'),
            ("去唱卡拉OK吧！", "anime", None, 5, "done",
             None, '["日常","音樂"]'),
            ("夜晚的水母不會游泳", "anime", None, None, "in_progress",
             "在看", '["音樂"]'),
            ("光逝去的夏天", "anime", None, None, "in_progress",
             "在看", '["夏天"]'),
            ("迷宮飯", "anime", "九井諒子", None, "in_progress",
             "在看，食物畫得很好吃", '["奇幻","美食"]'),
            ("我推的孩子 S2", "anime", "赤坂アカ", None, "in_progress",
             "在看", '["娛樂圈"]'),
            ("擅長逃跑的殿下", "anime", None, None, "in_progress",
             "在看", '["政治","輕鬆"]'),
            ("不時輕聲地以俄語遮羞的鄰座艾莉同學", "anime", "燦燦SUN", None, "in_progress",
             "在看", '["校園","語言"]'),
            ("新世紀福音戰士", "anime", "庵野秀明", None, "done",
             "補充，TVA 版，比劇場版更混亂但更喜歡", '["機器人","心理"]'),
            ("電腦線圈", "anime", "磯光雄", 6, "done",
             "補充，和論文方向有點相關，意外好看", '["科幻","AI","成長"]'),
            ("少女終末旅行", "anime", "つくみず", 6, "done",
             "補充，看完很安靜", '["末日","日常","孤獨"]'),

            # ── 電影 ────────────────────────────────────────────────────────
            ("重慶森林", "film", "王家衛", 6, "done",
             "王家衛最好看的一部", '["王家衛","孤獨","城市"]'),
            ("墮落天使", "film", "王家衛", 4, "done",
             None, '["王家衛"]'),
            ("花樣年華", "film", "王家衛", 4, "done",
             None, '["王家衛","愛情"]'),
            ("春光乍洩", "film", "王家衛", 3, "done",
             None, '["王家衛","同志"]'),
            ("藍色恐懼", "film", "今敏", 6, "done",
             "真實和虛構的邊界是什麼", '["今敏","心理","現實"]'),
            ("東京教父", "film", "今敏", 5, "done",
             None, '["今敏","家庭"]'),
            ("王牌冤家", "film", "米歇·龔德里", 6, "done",
             "如果能刪除記憶你會選什麼", '["記憶","愛情","科幻"]'),
            ("海邊的曼徹斯特", "film", "肯尼斯·洛勒根", 6, "done",
             None, '["悲傷","家庭"]'),
            ("鬥陣俱樂部", "film", "大衛·芬奇", 6, "done",
             None, '["心理","反社會"]'),
            ("我的完美日常", "film", "文·溫德斯", 6, "done",
             "看完想去打掃廁所", '["文·溫德斯","日常","日本"]'),
            ("巴黎·德州", "film", "文·溫德斯", 4, "done",
             None, '["文·溫德斯","公路"]'),
            ("可憐的東西", "film", "尤格·藍西莫", 5, "done",
             None, '["女性主義","奇幻"]'),
            ("蝙蝠俠：開戰時刻", "film", "諾蘭", 5, "done",
             None, '["諾蘭","超英"]'),
            ("福音戰士新劇場版：破", "film", "庵野秀明", 5, "done",
             None, '["庵野秀明","機器人"]'),
            ("福音戰士新劇場版：終", "film", "庵野秀明", 5, "done",
             None, '["庵野秀明","機器人"]'),
            ("雲端情人", "film", "Spike Jonze", 5, "done",
             "看完覺得和 AI 對話這件事很複雜", '["AI","愛情","科技"]'),
            ("驀然回首", "film", "藤本樹", 5, "done",
             "短片但說了很多", '["藤本樹","創作"]'),
            ("魔法公主", "film", "宮崎駿", 4, "done",
             None, '["宮崎駿","自然"]'),
            ("心之谷", "film", "宮崎駿", 5, "done",
             None, '["宮崎駿","成長"]'),
            ("霸王別姬", "film", "陳凱歌", 4, "done",
             None, '["華語","歷史"]'),
            ("聽海湧（劇場版）", "film", None, None, "done",
             "補充", '["台灣","歷史"]'),
            ("抓馬戀人", "film", "克里斯多佛·博格利", 6, "done",
             None, '["愛情","喜劇"]'),
            ("納米比亞沙漠直播中", "film", None, 6, "done",
             None, '["日本","女性"]'),
            ("長椅小情歌", "film", "奧山由之", 6, "done",
             "日常的拍法很特別", '["日常","愛情"]'),
            ("BLUE GIANT 藍色巨星", "film", "石塚真一", 6, "done",
             None, '["音樂","熱血"]'),
            ("末日小隊請登入", "film", None, 7, "done",
             "補充，完全沒預期這麼好看", '["科幻","末日"]'),
            ("攻殼機動隊", "film", "押井守", 6, "done",
             "補充，和論文主題有點呼應", '["AI","賽博龐克","身份認同"]'),
            ("A.I. 人工智慧", "film", "史蒂芬·史匹柏", 5, "done",
             "補充，重看後感受不一樣了", '["AI","科幻"]'),

            # ── 漫畫 ────────────────────────────────────────────────────────
            ("驀然回首", "manga", "藤本樹", 5, "done",
             None, '["藤本樹","創作"]'),
            ("直到夜色溫柔", "manga", "簡莉穎 x 廢廢子", 5, "done",
             None, '["台灣","愛情"]'),
            ("Day Off", "manga", "每日青菜", 5, "done",
             None, '["日常"]'),
            ("我在意的對象並不是男人", "manga", "新井すみこ", None, "in_progress",
             "在看", '["百合","愛情"]'),
            ("躲在超市後門抽菸的兩人", "manga", "地主", None, "in_progress",
             "在看", '["日常","愛情"]'),
            ("九號天鵝", "manga", None, None, "in_progress",
             "在看", '[]'),
            ("膽大黨", "manga", None, None, "in_progress",
             "在看", '["青春","犯罪"]'),
            ("SAKAMOTO DAYS", "manga", None, None, "in_progress",
             "在看", '["動作"]'),
            ("浪人劍客", "manga", "井上雄彥", None, "in_progress",
             "在看", '["武士","歷史"]'),
            ("黃昏流星群", "manga", None, None, "not_started",
             None, '["愛情"]'),
            ("錢進球場", "manga", "森高夕次", None, "in_progress",
             "在看", '["棒球","商業"]'),

            # ── 影集 ────────────────────────────────────────────────────────
            ("馴鹿寶貝", "tv", "Richard Gadd", 6, "done",
             "看完好幾天沒辦法說話", '["英劇","心理","創傷"]'),
            ("聽海湧", "tv", "孫介珩", 6, "done",
             None, '["台灣","歷史"]'),
            ("極度不妥！", "tv", "宮藤官九郎", 5, "done",
             None, '["日劇","喜劇"]'),
            ("混沌少年時", "tv", None, 5, "done",
             None, '["台灣","青春"]'),
            ("THE HOT SPOT", "tv", "笨蛋節奏", 5, "done",
             None, '["音樂","台灣"]'),
            ("離線找真愛", "tv", None, 6, "done",
             None, '["韓劇","戀愛"]'),
            ("絕命毒師", "tv", None, None, "in_progress",
             "在看", '["美劇","犯罪"]'),
        ]

        conn.executemany("""
            INSERT INTO media_library
                (title, type, author, score, status, her_note, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, media_data)


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

        new_familiarity = max(0, min(100,
            rel["familiarity"] + data.get("familiarity_delta", 0)))
        new_affection = max(-50, min(100,
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
    if affection > 85 and familiarity > 75:
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

def log_ignored_message(user_id: str, content: str) -> None:
    """記錄加奈選擇忽略的訊息（不回覆但仍儲存）。"""
    add_self_memory(
        type_="daily_event",
        content=f"收到 {user_id} 的訊息但沒回：{content[:50]}",
        mood_after=None,
        tags=["ignored_message"]
    )


def log_proactive_message(user_id: str, trigger: str, response: str) -> None:
    """記錄加奈主動發出的訊息。"""
    add_self_memory(
        type_="daily_event",
        content=f"主動傳訊給 {user_id}，觸發：{trigger}",
        mood_after=None,
        tags=["proactive"]
    )


# ─── pending_messages（睡眠期間收到的訊息佇列） ──────────────────────────────

def save_pending_message(user_id: str, display_name: str, content: str) -> None:
    """將睡眠或低體力期間收到的訊息存入佇列，等醒來後處理。"""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO pending_messages (user_id, display_name, content, received_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, display_name, content, now_taipei()))


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
