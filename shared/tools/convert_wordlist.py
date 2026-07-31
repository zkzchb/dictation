#!/usr/bin/env python3
"""shared/tools/convert_wordlist.py — 读取6表xlsx，生成JSON并补全拼音

依赖：pip install openpyxl pypinyin

用法：
  python shared/tools/convert_wordlist.py wordlist_grade3s1.xlsx

输出：
  shared/data/lessons_<base>.json   课程目录
  shared/data/kp_<base>.json        知识点列表
  shared/web/studio_manifest.json   录音工作台清单（待录音词条）
"""
import json, os, sys, hashlib, re
import openpyxl
from pypinyin import lazy_pinyin, Style

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
DATA_DIR = os.path.join(HERE, "..", "data")
WEB_DIR  = os.path.join(HERE, "..", "web")


# ── 拼音工具 ──────────────────────────────────────────────────────────────

def auto_pinyin(text: str) -> str:
    """用 pypinyin 生成带声调的拼音，空格分隔。"""
    if not text:
        return ""
    return " ".join(lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True))


def infer_pron(pw: str, word: str, word_py: str) -> str:
    """从组词和其拼音反推多音字的当前读音。
    例：pw=好, word=爱好, word_py='ài hào' -> 返回 'hào'
    """
    if not word or not word_py:
        return auto_pinyin(pw).split()[0] if pw else ""
    try:
        idx = word.index(pw)
        syllables = word_py.split()
        if idx < len(syllables):
            return syllables[idx]
    except (ValueError, IndexError):
        pass
    return auto_pinyin(pw).split()[0] if pw else ""


def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


# ── xlsx 读取 ──────────────────────────────────────────────────────────────

def read_sheet(wb, sheet_name: str) -> list[dict]:
    """读取一个工作表，过滤非数据行，返回行字典列表。"""
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    result = []
    for row in rows[1:]:
        # 过滤空行和注释行（第一列不是数字）
        if not row or row[0] is None:
            continue
        first = str(row[0]).strip()
        if not first or not re.match(r"^\d+$", first):
            continue
        d = {}
        for i, col in enumerate(header):
            val = row[i] if i < len(row) else None
            d[col] = str(val).strip() if val is not None else ""
        result.append(d)
    return result


# ── 主转换逻辑 ────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python convert_wordlist.py <词表.xlsx>")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    if not os.path.isabs(xlsx_path) and not os.path.exists(xlsx_path):
        xlsx_path = os.path.join(ROOT, xlsx_path)
    if not os.path.exists(xlsx_path):
        print(f"[X] 找不到文件: {xlsx_path}")
        sys.exit(1)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    units      = read_sheet(wb, "unit")
    lessons    = read_sheet(wb, "lesson")
    word2write = read_sheet(wb, "word2write")
    vocab      = read_sheet(wb, "vocab")
    typo       = read_sheet(wb, "typo")
    polyphonic = read_sheet(wb, "polyphonic")

    print(f"读取完毕: {len(units)} 单元, {len(lessons)} 课, "
          f"{len(word2write)} 生字, {len(vocab)} 词语, "
          f"{len(typo)} 易错字, {len(polyphonic)} 多音字行")

    # ── 建索引 ───────────────────────────────────────────────────────────
    unit_map = {int(r["uid"]): r["utitle"] for r in units}  # 311 -> "第一单元"
    lesson_map = {int(r["lid"]): r for r in lessons}

    # lesson_seq -> unit_id: lid // 10
    # unit_id -> unit_name: unit_map[uid]
    def get_unit_info(lid: int):
        uid = lid // 10
        return uid, unit_map.get(uid, f"单元{uid}")

    # ── 构建 lessons JSON ─────────────────────────────────────────────────
    lessons_out = []
    for r in lessons:
        lid = int(r["lid"])
        uid, uname = get_unit_info(lid)
        lessons_out.append({
            "lesson_seq":  lid,
            "unit_id":     uid,
            "unit_name":   uname,
            "lesson_name": r.get("lname", ""),
        })

    # ── 构建 knowledge_points ─────────────────────────────────────────────
    kps = []
    seen_for_manifest = {}   # text -> pinyin (for studio manifest)

    # 1. 生字 (word2write)
    for r in word2write:
        wid = int(r["wid"])
        lesson_seq = wid // 100
        bw = r.get("basic_word", "")
        if not bw:
            continue
        opts = []
        for i in (1, 2):
            w   = r.get(f"word{i}", "").strip()
            py  = r.get(f"word{i}py", "").strip() or auto_pinyin(w)
            if w:
                opts.append({"text": w, "pinyin": py})
                seen_for_manifest[w] = py
        if not opts:
            # 没有组词时用字本身
            py = auto_pinyin(bw)
            opts = [{"text": bw, "pinyin": py}]
            seen_for_manifest[bw] = py
        kps.append({"lesson_seq": lesson_seq, "target": bw,
                    "category": "生字", "options_json": opts})

    # 2. 词语/成语/四字词语 (vocab)
    for r in vocab:
        vid = int(r["vid"])
        lesson_seq = vid // 100
        vw = r.get("vword", "").strip()
        if not vw:
            continue
        py = r.get("vpy", "").strip() or auto_pinyin(vw)
        opts = [{"text": vw, "pinyin": py}]
        seen_for_manifest[vw] = py
        cat = r.get("vtype", "词语") or "词语"
        kps.append({"lesson_seq": lesson_seq, "target": vw,
                    "category": cat, "options_json": opts})

    # 3. 易错字 (typo) — tw1 和 tw2 各建一条
    for r in typo:
        tid = int(r["tid"])
        lesson_seq = tid // 100
        for slot in (("tw1", "tw1py"), ("tw2", "tw2py")):
            w  = r.get(slot[0], "").strip()
            py = r.get(slot[1], "").strip() or auto_pinyin(w)
            if w:
                opts = [{"text": w, "pinyin": py}]
                seen_for_manifest[w] = py
                kps.append({"lesson_seq": lesson_seq, "target": w,
                             "category": "易错字", "options_json": opts})

    # 4. 多音字 (polyphonic) — 按 ppid 分组，每组一条
    poly_groups: dict[int, list] = {}
    for r in polyphonic:
        ppid = int(r["ppid"])
        poly_groups.setdefault(ppid, []).append(r)

    for ppid, rows in poly_groups.items():
        lesson_seq = ppid // 100
        pw = rows[0].get("pw", "")
        if not pw:
            continue
        opts = []
        for r in rows:
            w  = r.get("word", "").strip()
            py = r.get("word_py", "").strip() or auto_pinyin(w)
            pron = r.get("pron", "").strip() or infer_pron(pw, w, py)
            if w:
                opts.append({"text": w, "pinyin": py, "pron": pron})
                seen_for_manifest[w] = py
        if opts:
            kps.append({"lesson_seq": lesson_seq, "target": pw,
                         "category": "多音字", "options_json": opts})

    # ── Studio manifest ───────────────────────────────────────────────────
    audio_dir = os.path.join(WEB_DIR, "audio", "w")
    recorded = set()
    if os.path.isdir(audio_dir):
        for f in os.listdir(audio_dir):
            if f.endswith(".mp3"):
                recorded.add(f[:-4])

    manifest = [{"text": t, "pinyin": p, "hash": word_hash(t)}
                for t, p in seen_for_manifest.items()]
    pending  = [w for w in manifest if w["hash"] not in recorded]

    # ── 写出文件 ──────────────────────────────────────────────────────────
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(WEB_DIR,  exist_ok=True)

    base = os.path.splitext(os.path.basename(xlsx_path))[0]
    lessons_path  = os.path.join(DATA_DIR, f"lessons_{base}.json")
    kp_path       = os.path.join(DATA_DIR, f"kp_{base}.json")
    manifest_path = os.path.join(WEB_DIR,  "studio_manifest.json")

    with open(lessons_path,  "w", encoding="utf-8") as f:
        json.dump(lessons_out, f, ensure_ascii=False, indent=2)
    print(f"[OK] {lessons_path}  ({len(lessons_out)} 课)")

    with open(kp_path, "w", encoding="utf-8") as f:
        json.dump(kps, f, ensure_ascii=False, indent=2)
    print(f"[OK] {kp_path}  ({len(kps)} 知识点)")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[OK] {manifest_path}  ({len(manifest)} 词条, {len(pending)} 待录音)")

    print("\n下一步：")
    print(f"  python shared/tools/import_wordlist_xlsx.py {xlsx_path}")
    print(f"  python shared/tools/export_d1.py")
    if pending:
        print(f"  访问 http://localhost:8888/studio  录制 {len(pending)} 个切片")


if __name__ == "__main__":
    main()
