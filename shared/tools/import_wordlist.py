#!/usr/bin/env python3
"""shared/tools/import_wordlist.py — 把新词表追加到现有数据库

使用 INSERT OR IGNORE，不会破坏已有数据（听写历史、错题本）。
适合向已运行的 V1 / V2 数据库追加新学期内容。

用法：
  # 追加到 v1 的数据库（默认）
  python shared/tools/import_wordlist.py grade3s1_wordlist.csv

  # 指定数据库路径
  python shared/tools/import_wordlist.py grade3s1_wordlist.csv --db v2/dictation.db
"""
import csv
import json
import os
import sys
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")


def word_hash_unused(text):
    import hashlib
    return hashlib.md5(text.encode()).hexdigest()[:12]


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        clean = (line for line in f
                 if line.strip() and not line.lstrip().startswith("#"))
        reader = csv.DictReader(clean)
        for r in reader:
            rows.append({(k or "").strip(): (v or "").strip()
                         for k, v in r.items() if k})
    return rows


def build_options(row):
    opts = []
    for i in range(1, 4):
        w = row.get(f"word{i}", "").strip()
        p = row.get(f"pinyin{i}", "").strip()
        if w:
            opts.append({"text": w, "pinyin": p})
    return opts


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", help="词表 CSV 文件路径")
    parser.add_argument("--db", default=None, help="数据库路径（默认 v1/dictation.db）")
    args = parser.parse_args()

    csv_path = args.csv_file
    if not os.path.isabs(csv_path) and not os.path.exists(csv_path):
        csv_path = os.path.join(ROOT, csv_path)

    db_path = args.db or os.path.join(ROOT, "v1", "dictation.db")

    if not os.path.exists(csv_path):
        print(f"[X] 找不到 CSV: {csv_path}")
        sys.exit(1)
    if not os.path.exists(db_path):
        print(f"[X] 找不到数据库: {db_path}")
        print("   请先运行 init_db.py 初始化数据库")
        sys.exit(1)

    rows = load_csv(csv_path)
    print(f"读取 {len(rows)} 行，目标数据库: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── 插入课程（去重）──────────────────────────────────────────────
    lessons_seen = {}
    for r in rows:
        seq = int(r["lesson_seq"])
        if seq not in lessons_seen:
            lessons_seen[seq] = (seq, int(r["unit_id"]), r["unit_name"], r["lesson_name"])

    lessons_added = 0
    for vals in lessons_seen.values():
        cursor.execute(
            "INSERT OR IGNORE INTO lessons (lesson_seq,unit_id,unit_name,lesson_name) VALUES (?,?,?,?)",
            vals,
        )
        if cursor.rowcount:
            lessons_added += 1

    # ── 插入知识点 ─────────────────────────────────────────────────
    kps_added = 0
    for r in rows:
        opts = build_options(r)
        if not opts and r["category"] != "易混淆字":
            opts = [{"text": r["target"], "pinyin": ""}]
        opts_json = json.dumps(opts, ensure_ascii=False)
        cursor.execute(
            "INSERT OR IGNORE INTO knowledge_points (lesson_seq,target,category,options_json) "
            "VALUES (?,?,?,?)",
            (int(r["lesson_seq"]), r["target"], r["category"], opts_json),
        )
        if cursor.rowcount:
            kps_added += 1

    conn.commit()

    # ── 统计 ─────────────────────────────────────────────────────
    total_lessons = cursor.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    total_kps = cursor.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
    conn.close()

    print(f"[OK]  新增 {lessons_added} 课  （数据库共 {total_lessons} 课）")
    print(f"[OK]  新增 {kps_added} 知识点（数据库共 {total_kps} 条）")
    print(f"\n若需同步到 V2，重新运行指定 --db v2/dictation.db")
    print(f"若需更新 V3 D1，重新运行 python shared/tools/export_d1.py")


if __name__ == "__main__":
    main()
