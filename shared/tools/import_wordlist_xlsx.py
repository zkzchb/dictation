#!/usr/bin/env python3
"""shared/tools/import_wordlist_xlsx.py — 直接从xlsx导入到数据库

不依赖中间JSON文件，读xlsx后直接 INSERT OR IGNORE 到现有DB。

用法：
  python shared/tools/import_wordlist_xlsx.py wordlist_grade3s1.xlsx
  python shared/tools/import_wordlist_xlsx.py wordlist_grade3s1.xlsx --db v2/dictation.db
"""
import json, os, sys, re, sqlite3, hashlib
import openpyxl
from pypinyin import lazy_pinyin, Style

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")


def auto_pinyin(text):
    if not text: return ""
    return " ".join(lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True))

def infer_pron(pw, word, word_py):
    if not word or not word_py:
        return auto_pinyin(pw).split()[0] if pw else ""
    try:
        idx = word.index(pw)
        syls = word_py.split()
        if idx < len(syls): return syls[idx]
    except (ValueError, IndexError): pass
    return auto_pinyin(pw).split()[0] if pw else ""

def read_sheet(wb, name):
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows: return []
    header = [str(c).strip() if c else "" for c in rows[0]]
    result = []
    for row in rows[1:]:
        if not row or row[0] is None: continue
        first = str(row[0]).strip()
        if not first or not re.match(r"^\d+$", first): continue
        d = {header[i]: (str(row[i]).strip() if i < len(row) and row[i] is not None else "")
             for i in range(len(header))}
        result.append(d)
    return result


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("xlsx_file")
    p.add_argument("--db", default=None)
    args = p.parse_args()

    xlsx_path = args.xlsx_file
    if not os.path.isabs(xlsx_path) and not os.path.exists(xlsx_path):
        xlsx_path = os.path.join(ROOT, xlsx_path)
    if not os.path.exists(xlsx_path):
        print(f"[X] 找不到文件: {xlsx_path}"); sys.exit(1)

    db_path = args.db or os.path.join(ROOT, "v1", "dictation.db")
    if not os.path.exists(db_path):
        print(f"[X] 找不到数据库: {db_path}"); sys.exit(1)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    units      = read_sheet(wb, "unit")
    lessons    = read_sheet(wb, "lesson")
    word2write = read_sheet(wb, "word2write")
    vocab_rows = read_sheet(wb, "vocab")
    typo_rows  = read_sheet(wb, "typo")
    poly_rows  = read_sheet(wb, "polyphonic")

    unit_map = {int(r["uid"]): r["utitle"] for r in units}

    conn = sqlite3.connect(db_path); cursor = conn.cursor()
    la, ka = 0, 0

    def ins_lesson(lid):
        uid = lid // 10
        uname = unit_map.get(uid, f"单元{uid}")
        row = next((r for r in lessons if int(r["lid"]) == lid), None)
        if not row: return
        cursor.execute("INSERT OR IGNORE INTO lessons VALUES (?,?,?,?)",
                       (lid, uid, uname, row.get("lname","")))
        nonlocal la
        if cursor.rowcount: la += 1

    def ins_kp(lesson_seq, target, category, opts):
        opts_json = json.dumps(opts, ensure_ascii=False)
        cursor.execute("INSERT OR IGNORE INTO knowledge_points "
                       "(lesson_seq,target,category,options_json) VALUES (?,?,?,?)",
                       (lesson_seq, target, category, opts_json))
        nonlocal ka
        if cursor.rowcount: ka += 1

    # lessons first
    for r in lessons:
        ins_lesson(int(r["lid"]))

    # 生字
    for r in word2write:
        ls = int(r["wid"]) // 100
        ins_lesson(ls)
        bw = r.get("basic_word","")
        if not bw: continue
        opts = []
        for i in (1,2):
            w = r.get(f"word{i}","").strip()
            py = r.get(f"word{i}py","").strip() or auto_pinyin(w)
            if w: opts.append({"text":w,"pinyin":py})
        if not opts: opts = [{"text":bw,"pinyin":auto_pinyin(bw)}]
        ins_kp(ls, bw, "生字", opts)

    # 词语
    for r in vocab_rows:
        ls = int(r["vid"]) // 100
        ins_lesson(ls)
        vw = r.get("vword","").strip()
        if not vw: continue
        py = r.get("vpy","").strip() or auto_pinyin(vw)
        ins_kp(ls, vw, r.get("vtype","词语") or "词语", [{"text":vw,"pinyin":py}])

    # 易错字
    for r in typo_rows:
        ls = int(r["tid"]) // 100
        ins_lesson(ls)
        for wk, pk in (("tw1","tw1py"),("tw2","tw2py")):
            w = r.get(wk,"").strip(); py = r.get(pk,"").strip() or auto_pinyin(w)
            if w: ins_kp(ls, w, "易错字", [{"text":w,"pinyin":py}])

    # 多音字
    groups: dict = {}
    for r in poly_rows:
        ppid = int(r["ppid"]); groups.setdefault(ppid,[]).append(r)
    for ppid, rows in groups.items():
        ls = ppid // 100
        ins_lesson(ls)
        pw = rows[0].get("pw","")
        if not pw: continue
        opts = []
        for r in rows:
            w = r.get("word","").strip()
            py = r.get("word_py","").strip() or auto_pinyin(w)
            pron = r.get("pron","").strip() or infer_pron(pw,w,py)
            if w: opts.append({"text":w,"pinyin":py,"pron":pron})
        if opts: ins_kp(ls, pw, "多音字", opts)

    conn.commit()
    tl = cursor.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    tk = cursor.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
    conn.close()
    print(f"[OK] 新增 {la} 课（共 {tl}），新增 {ka} 知识点（共 {tk}）")


if __name__ == "__main__":
    main()
