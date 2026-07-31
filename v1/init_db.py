import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "..", "shared", "data")
DB_NAME      = os.path.join(BASE_DIR, "dictation.db")
KP_FILES     = [os.path.join(DATA_DIR, f"kp_part{i}.json") for i in range(4)]
LESSONS_FILE = os.path.join(DATA_DIR, "lessons_2b.json")

def init_database():
    # ⚠️ 本脚本会 DROP 所有表并重灌题库，会清空用户的听写记录与错题本。
    # 为防止误运行毁掉真实数据，除非显式加 --force，否则拒绝覆盖已存在的库。
    if os.path.exists(DB_NAME) and "--force" not in sys.argv:
        print(f"❌ 检测到已存在的数据库 {DB_NAME}，其中可能含有真实学习记录。")
        print("   本脚本仅用于全新初始化，会清空所有用户数据。")
        print("   如确需重建，请先备份，再运行：  python init_db.py --force")
        sys.exit(1)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    print("🚀 开始初始化生产级数据库...")

    # 1. 炸毁旧表，确保每次运行都是全新的纯净状态
    tables = ['lessons', 'knowledge_points', 'user_progress', 'user_memory', 'dictation_history', 'dictation_items']
    for t in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {t};")

    # ================= 静态字典表 =================
    cursor.execute("""
    CREATE TABLE lessons (
        lesson_seq INTEGER PRIMARY KEY,
        unit_id INTEGER NOT NULL,
        unit_name TEXT NOT NULL,
        lesson_name TEXT NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE knowledge_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_seq INTEGER NOT NULL,
        target TEXT NOT NULL,
        category TEXT NOT NULL,
        options_json TEXT NOT NULL,
        FOREIGN KEY(lesson_seq) REFERENCES lessons(lesson_seq)
    )""")

    # ================= 动态记忆表 =================
    cursor.execute("""
    CREATE TABLE user_progress (
        user_id INTEGER PRIMARY KEY,
        current_lesson_seq INTEGER DEFAULT 1,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""
    CREATE TABLE user_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kp_id INTEGER NOT NULL,
        status INTEGER DEFAULT 0,  -- 0: 学习/复习中, 1: 已掌握
        error_count INTEGER DEFAULT 0,
        correct_streak INTEGER DEFAULT 0,
        last_tested_date DATE,
        next_review_date DATE,
        UNIQUE(user_id, kp_id)
    )""")

    cursor.execute("""
    CREATE TABLE dictation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        dictation_type TEXT NOT NULL, -- 'daily' 或 'review'
        scope_id INTEGER NOT NULL,    
        score INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""
    CREATE TABLE dictation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER NOT NULL,
        kp_id INTEGER NOT NULL,
        is_correct BOOLEAN NOT NULL,
        FOREIGN KEY(history_id) REFERENCES dictation_history(id)
    )""")

    # 2. 灌入静态数据
    if os.path.exists(LESSONS_FILE):
        with open(LESSONS_FILE, "r", encoding="utf-8") as f:
            lessons_data = json.load(f)
            cursor.executemany("INSERT INTO lessons VALUES (?, ?, ?, ?)", 
                               [(d["lesson_seq"], d["unit_id"], d["unit_name"], d["lesson_name"]) for d in lessons_data])
        print(f"✅ 成功灌入 {len(lessons_data)} 条课程目录数据！")

    total_kp = 0
    for file_name in KP_FILES:
        if os.path.exists(file_name):
            with open(file_name, "r", encoding="utf-8") as f:
                kp_data = json.load(f)
                cursor.executemany("INSERT INTO knowledge_points (lesson_seq, target, category, options_json) VALUES (?, ?, ?, ?)", 
                                   [(kp["lesson_seq"], kp["target"], kp["category"], json.dumps(kp["options_json"], ensure_ascii=False)) for kp in kp_data])
                total_kp += len(kp_data)
            print(f"✅ 成功从 {file_name} 灌入 {len(kp_data)} 条知识点数据！")

    # 3. 初始化 MVP 种子用户
    cursor.execute("INSERT INTO user_progress (user_id, current_lesson_seq) VALUES (1, 1)")

    # 4. 冷启动注入：将 lesson 3000 (高频易错字) 注入用户记忆库
    #    next_review_date = today+3，避免第一天就与真实错词竞争
    cursor.execute("""
        SELECT id FROM knowledge_points
        WHERE lesson_seq = 3000 AND category NOT IN ('易混淆字', '多音字')
    """)
    cold_start_kps = cursor.fetchall()

    cold_start_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    for kp in cold_start_kps:
        cursor.execute("""
        INSERT OR IGNORE INTO user_memory (user_id, kp_id, status, error_count, next_review_date)
        VALUES (1, ?, 0, 1, ?)
        """, (kp[0], cold_start_date))

    print(f"成功注入 {len(cold_start_kps)} 个冷启动易错词（next_review={cold_start_date}）")

    conn.commit()
    conn.close()
    print("🎉 生产级数据库完整初始化完毕！")

if __name__ == "__main__":
    init_database()