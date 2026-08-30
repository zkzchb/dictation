#!/usr/bin/env python3
"""给已部署的库加 dictation_history.poly_ids 列（幂等，不动任何现有数据）。

为什么需要这一列
----------------
多音字只播报、不判分，所以它们从不进 dictation_items —— 也就没有任何
「哪些多音字被播报过」的记录。结果 _polyphonic_section 每次都按
(lesson_seq, id) 取同样的前 2 个，出现「连续多天固定同两个字」的现象。

本列存逗号分隔的 kp_id，由前端提交时回传，供休息规则读取：
同一个多音字若在最近两轮听写中都出现，本轮不再抽中。

用法
----
  v2/venv/bin/python shared/tools/migrate_poly_ids.py
  v2/venv/bin/python shared/tools/migrate_poly_ids.py /var/lib/dictation/v2/dictation.db

安全性：只做 ALTER TABLE ADD COLUMN，已有行的该列为空串。
空串不影响休息规则 —— 规则只看最近两轮，历史空值等同于「没播报过」。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DBS = [
    os.path.join(ROOT, "v2", "dictation.db"),
]


def migrate(db_path: str) -> bool:
    if not os.path.exists(db_path):
        print(f"[skip] 不存在: {db_path}")
        return True

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(dictation_history)")]
        if not cols:
            print(f"[skip] {db_path} 没有 dictation_history 表")
            return True

        n_before = conn.execute("SELECT COUNT(*) FROM dictation_history").fetchone()[0]

        if "poly_ids" in cols:
            print(f"[skip] {db_path} 已有 poly_ids 列（{n_before} 行历史）")
            return True

        conn.execute(
            "ALTER TABLE dictation_history ADD COLUMN poly_ids TEXT DEFAULT ''")
        conn.commit()

        cols_after = [r[1] for r in conn.execute("PRAGMA table_info(dictation_history)")]
        n_after = conn.execute("SELECT COUNT(*) FROM dictation_history").fetchone()[0]

        ok = ("poly_ids" in cols_after) and (n_after == n_before)
        mark = "[OK]" if ok else "[FAIL]"
        print(f"{mark} {db_path} 加列完成 —— 历史 {n_before} 行 -> {n_after} 行")
        if not ok:
            print("      行数不符或列未生成，请检查！")
        return ok
    finally:
        conn.close()


def main():
    dbs = sys.argv[1:] or DEFAULT_DBS
    all_ok = True
    for db in dbs:
        if not migrate(db):
            all_ok = False
    if all_ok:
        print("\n全部完成。重启服务后休息规则生效：")
        print("  sudo systemctl restart dictation-v2")
    else:
        print("\n有失败项，请勿重启，先排查。")
        sys.exit(1)


if __name__ == "__main__":
    main()
