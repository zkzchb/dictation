#!/usr/bin/env python3
"""shared/tools/export_d1.py — 把题库 JSON 导出为 D1 种子 SQL

读取 shared/data/ 中的 JSON 文件，生成 v3/migrations/0002_seed.sql。
在 wrangler d1 migrations apply 时按文件名顺序自动执行（0002 在 0001 schema 之后）。

用法：
  python shared/tools/export_d1.py

每次题库 JSON 变更后需重新运行，再执行：
  npx wrangler d1 migrations apply dictation-v3
"""
import json
import os

HERE         = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(HERE, "..", "data")
MIGRATIONS   = os.path.join(HERE, "..", "..", "v3", "migrations")
OUT          = os.path.join(MIGRATIONS, "0002_seed.sql")


def q(s: str) -> str:
    """SQLite 字符串转义（单引号翻倍）。"""
    return "'" + str(s).replace("'", "''") + "'"


def main():
    os.makedirs(MIGRATIONS, exist_ok=True)
    lines = [
        "-- D1 种子数据 — 由 shared/tools/export_d1.py 自动生成，请勿手动编辑",
        "-- 重新生成：python shared/tools/export_d1.py",
        "",
    ]

    # ── lessons ──────────────────────────────────────────────────────────
    lessons_file = os.path.join(DATA_DIR, "lessons_grade3.json")
    if not os.path.exists(lessons_file):
        print(f"[X] 找不到课程文件: {lessons_file}")
        return
    with open(lessons_file, encoding="utf-8") as f:
        lessons = json.load(f)

    lines.append("-- lessons")
    for d in lessons:
        # 显式写列名，不用位置 VALUES —— schema 加列时位置会错位，
        # lesson_title 就是后补的列，当初的位置写法会把它和 lesson_name 搞反。
        lines.append(
            f"INSERT OR IGNORE INTO lessons "
            f"(lesson_seq, unit_id, unit_name, lesson_title, lesson_name) VALUES "
            f"({d['lesson_seq']}, {d['unit_id']}, "
            f"{q(d['unit_name'])}, {q(d.get('lesson_title', ''))}, "
            f"{q(d['lesson_name'])});"
        )
    lines.append(f"-- {len(lessons)} 条课程记录\n")

    # ── knowledge_points ─────────────────────────────────────────────────
    kp_files = [os.path.join(DATA_DIR, "kp_grade3.json")]
    total_kp = 0
    lines.append("-- knowledge_points")
    for kp_file in kp_files:
        if not os.path.exists(kp_file):
            print(f"[!]  找不到题库文件: {kp_file}")
            continue
        with open(kp_file, encoding="utf-8") as f:
            kps = json.load(f)
        for kp in kps:
            opts_str = json.dumps(kp["options_json"], ensure_ascii=False)
            lines.append(
                f"INSERT OR IGNORE INTO knowledge_points "
                f"(lesson_seq, target, category, options_json) VALUES "
                f"({kp['lesson_seq']}, {q(kp['target'])}, "
                f"{q(kp['category'])}, {q(opts_str)});"
            )
        total_kp += len(kps)
        print(f"  {os.path.basename(kp_file)}: {len(kps)} 条")

    lines.append(f"-- {total_kp} 条知识点记录\n")

    # ── 种子用户 + 冷启动错词 ──────────────────────────────────────────────
    lines.append("-- 种子用户")
    lines.append(
        "INSERT OR IGNORE INTO user_progress (user_id, current_lesson_seq) VALUES (1, 1);"
    )

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n[OK]  写入 {OUT}")
    print(f"   包含 {len(lessons)} 条课程 + {total_kp} 条知识点")
    print(f"\n下一步（D1 建库后执行）：")
    print(f"  npx wrangler d1 migrations apply dictation-v3")


if __name__ == "__main__":
    main()
