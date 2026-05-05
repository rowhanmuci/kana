"""
status.py — 加奈 Bot 狀態診斷工具

使用方式（從 D:\\kana\\ 執行）：
    python status.py          # 顯示完整狀態快照
    python status.py --log    # 即時追蹤 log（Ctrl+C 離開）
    python status.py --reset  # 重新初始化 DB（危險！會清空所有資料）
"""

import sys
import os
import time
import sqlite3
import json

# ─── 路徑設定 ─────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(ROOT, "data", "kana.db")
)
LOG_PATH = os.path.join(ROOT, "kana.log")


# ─── 輔助函式 ─────────────────────────────────────────────────────────────────
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return "❌ (表格不存在)"


# ─── 主要功能 ─────────────────────────────────────────────────────────────────
def show_status():
    print("=" * 62)
    print("  加奈 Bot 狀態診斷")
    print("=" * 62)

    # ── 1. DB 檔案 ──────────────────────────────────────────────────
    print(f"\n📁 DB 路徑：{DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("  ❌ 檔案不存在 — 請先啟動 Bot 讓它初始化 DB")
        print("     或執行：python status.py --reset")
    else:
        size = os.path.getsize(DB_PATH)
        if size == 0:
            print("  ⚠️  0 bytes — DB 尚未初始化（請執行 python status.py --reset）")
        else:
            print(f"  ✅ 正常（{size:,} bytes）")

    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        print("\n  無法繼續，請先初始化 DB")
        return

    conn = _conn()

    # ── 2. 資料表筆數 ───────────────────────────────────────────────
    print("\n📊 資料庫內容：")
    tables = ["persona_state", "relationship", "memory_conversation",
              "memory_self", "memory_world", "media_library"]
    for t in tables:
        n = _table_count(conn, t)
        print(f"  {t:<28} {n} 筆")

    # ── 3. 加奈目前狀態 ─────────────────────────────────────────────
    print("\n🧠 加奈目前狀態（persona_state）：")
    row = conn.execute("SELECT * FROM persona_state WHERE id=1").fetchone()
    if row:
        r = dict(row)
        print(f"  心情：   {r.get('current_mood', '?')}")
        print(f"  活動：   {r.get('current_activity', '?')}")
        print(f"  能量：   {r.get('energy_level', '?')} / 100")
        print(f"  論文：   {r.get('thesis_progress', '?')} %")
        print(f"  更新時間：{r.get('last_updated', '?')}")
    else:
        print("  （尚無資料）")

    # ── 4. 最近心跳記錄 ─────────────────────────────────────────────
    print("\n💓 最近 5 筆心跳記錄（memory_self）：")
    rows = conn.execute("""
        SELECT created_at, type, content, tags
        FROM memory_self ORDER BY created_at DESC LIMIT 5
    """).fetchall()
    if rows:
        for r in rows:
            tags = json.loads(r["tags"] or "[]")
            tag_str = ", ".join(tags) if tags else "—"
            print(f"  [{r['created_at']}]  type={r['type']}  tags={tag_str}")
            print(f"    {(r['content'] or '')[:70]}")
    else:
        print("  （尚無記錄 — 心跳還沒觸發過，或 Bot 尚未運行）")

    # ── 5. 最近世界記憶 ─────────────────────────────────────────────
    print("\n🌐 最近 3 筆瀏覽記憶（memory_world）：")
    rows = conn.execute("""
        SELECT created_at, source, title, her_reaction
        FROM memory_world ORDER BY created_at DESC LIMIT 3
    """).fetchall()
    if rows:
        for r in rows:
            print(f"  [{r['created_at']}]  [{r['source']}] {r['title']}")
            print(f"    加奈反應：{(r['her_reaction'] or '（無）')[:60]}")
    else:
        print("  （尚無記錄 — browse.py stubs 都回空 list，目前是預期行為）")

    # ── 6. 最近對話記憶 ─────────────────────────────────────────────
    print("\n💬 最近 5 筆對話記憶（memory_conversation）：")
    rows = conn.execute("""
        SELECT mc.created_at, mc.user_id, r.display_name,
               mc.summary, mc.familiarity_delta, mc.affection_delta
        FROM memory_conversation mc
        LEFT JOIN relationship r ON mc.user_id = r.user_id
        ORDER BY mc.created_at DESC LIMIT 5
    """).fetchall()
    if rows:
        for r in rows:
            name = r["display_name"] or r["user_id"]
            print(f"  [{r['created_at']}]  {name}")
            print(f"    摘要：{(r['summary'] or '（無）')[:65]}")
            print(f"    熟悉度Δ={r['familiarity_delta']}  好感Δ={r['affection_delta']}")
    else:
        print("  （尚無對話 — 還沒有成功完成過對話）")

    # ── 7. 使用者關係 ───────────────────────────────────────────────
    print("\n👥 使用者關係表（relationship）：")
    rows = conn.execute("""
        SELECT display_name, user_id, familiarity, affection,
               relationship_stage, last_interaction
        FROM relationship ORDER BY last_interaction DESC
    """).fetchall()
    if rows:
        for r in rows:
            print(f"  {r['display_name']} (id={r['user_id']})")
            print(f"    stage={r['relationship_stage']}  "
                  f"熟悉={r['familiarity']}  好感={r['affection']}")
            print(f"    最後互動：{r['last_interaction']}")
    else:
        print("  （還沒有任何使用者傳過 DM）")

    conn.close()

    # ── 8. Log 最後 15 行 ───────────────────────────────────────────
    print(f"\n📄 Log 最後 15 行（{LOG_PATH}）：")
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-15:]:
            print("  " + line.rstrip())
    else:
        print("  （kana.log 不存在）")

    print("\n" + "=" * 62)
    print("  讀懂這份報告的重點：")
    print("  - memory_self 有 [heartbeat] tag → 心跳有在跑")
    print("  - persona_state.last_updated 每 30 分鐘應更新一次")
    print("  - memory_conversation 有資料 → 對話有成功完成")
    print("  - relationship 有資料 → 有人傳過 DM 給加奈")
    print("=" * 62)


def tail_log():
    """即時追蹤 kana.log（類似 tail -f）"""
    if not os.path.exists(LOG_PATH):
        print(f"❌ 找不到 {LOG_PATH}，Bot 是否有在執行中？")
        return
    print(f"追蹤 {LOG_PATH} — Ctrl+C 離開\n{'─'*50}")
    with open(LOG_PATH, encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines[-25:]:
            print(line.rstrip())
        print("─" * 50 + "  （以下為即時新增）\n")
        while True:
            line = f.readline()
            if line:
                print(line.rstrip())
            else:
                time.sleep(0.5)


def init_db_standalone():
    """獨立初始化 DB（不 import src/database.py）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS persona_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        current_activity TEXT DEFAULT 'idle',
        current_mood TEXT DEFAULT 'content',
        thesis_progress INTEGER DEFAULT 0,
        energy_level INTEGER DEFAULT 70,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS memory_self (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        type TEXT, content TEXT, mood_after TEXT, tags TEXT DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS memory_world (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source TEXT, url TEXT, title TEXT, summary TEXT,
        her_reaction TEXT, tags TEXT DEFAULT '[]'
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
        summary TEXT, emotional_moment TEXT,
        familiarity_delta INTEGER DEFAULT 0,
        affection_delta INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES relationship(user_id)
    );
    CREATE TABLE IF NOT EXISTS media_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, type TEXT, author TEXT,
        score INTEGER, status TEXT DEFAULT 'done',
        completed_date TEXT, her_note TEXT, tags TEXT DEFAULT '[]'
    );
    """)
    conn.execute("""
        INSERT OR IGNORE INTO persona_state
            (id, current_activity, current_mood, thesis_progress, energy_level)
        VALUES (1, 'idle', 'content', 5, 70)
    """)
    conn.commit()
    conn.close()
    print(f"✅ DB 初始化完成：{DB_PATH}")


def reset_db():
    confirm = input(f"⚠️  確定要清空 {DB_PATH}？輸入 YES 確認：")
    if confirm.strip() == "YES":
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        init_db_standalone()
        print("✅ DB 已重建（media_library 的資料需重啟 Bot 才會回來）")
    else:
        print("已取消")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--log" in sys.argv:
        tail_log()
    elif "--reset" in sys.argv:
        reset_db()
    else:
        show_status()
