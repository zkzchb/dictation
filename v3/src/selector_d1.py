"""v3/src/selector_d1.py —— 选词引擎的 D1 异步版

与 shared/selector.py 梯队逻辑完全一致（设计文档 §5），
差异仅在数据访问：D1 用 await prepare/bind/run 取行，见 _rows()。

Pyodide 环境下不能跨目录 import，故此文件由 stage.py 从 shared/ 同步维护，
或直接在此维护并与 shared/selector.py 保持逻辑对齐。
"""
import json
import random

CAT_CHAR = "生字"
CAT_WORD = "词语"
CAT_TYPO = "易错字"
CAT_POLY = "多音字"

TARGET_DAILY = 30
TARGET_REVIEW = 50
POLY_PER_LESSON = 2
COLD_START_LESSON = 3000
TYPO_MIN_SPACING = 3


def is_review_lesson(lesson_seq):
    return lesson_seq % 10 == 0 and lesson_seq != COLD_START_LESSON


async def _rows(db, sql, params=()):
    r = await db.prepare(sql).bind(*params).run()
    rows = r.results
    # 当前 workers-py 下 results 已是 Python list，调 .to_py() 会抛
    # AttributeError: 'list' object has no attribute 'to_py'。
    # 旧版 Pyodide 返回 JsProxy 才需要转换，故按能力判断而非写死。
    return rows.to_py() if hasattr(rows, "to_py") else list(rows)


async def regular_lessons(db):
    rows = await _rows(
        db, "SELECT lesson_seq FROM lessons WHERE lesson_seq > ? ORDER BY lesson_seq",
        (COLD_START_LESSON,))
    return [r["lesson_seq"] for r in rows if not is_review_lesson(r["lesson_seq"])]


async def _kps_of(db, lesson_seqs, categories):
    if not lesson_seqs or not categories:
        return []
    lp = ",".join("?" * len(lesson_seqs))
    cp = ",".join("?" * len(categories))
    sql = (f"SELECT id, lesson_seq, target, category, options_json "
           f"FROM knowledge_points "
           f"WHERE lesson_seq IN ({lp}) AND category IN ({cp}) "
           f"ORDER BY lesson_seq, id")
    return await _rows(db, sql, (*lesson_seqs, *categories))


async def _session_ids(db, user_id, limit=8):
    rows = await _rows(
        db, "SELECT id FROM dictation_history WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (user_id, limit))
    return [r["id"] for r in rows]


async def _wrong_kps_in_sessions(db, history_ids):
    if not history_ids:
        return []
    hp = ",".join("?" * len(history_ids))
    sql = (f"SELECT kp.id, kp.lesson_seq, kp.target, kp.category, kp.options_json, "
           f"       COUNT(*) AS wrong_times "
           f"FROM dictation_items di "
           f"JOIN knowledge_points kp ON kp.id = di.kp_id "
           f"WHERE di.history_id IN ({hp}) AND di.is_correct = 0 AND kp.category != ? "
           f"GROUP BY kp.id ORDER BY wrong_times DESC, kp.lesson_seq, kp.id")
    return await _rows(db, sql, (*history_ids, CAT_POLY))


async def _unit_wrong_kps(db, user_id, unit_lessons):
    if not unit_lessons:
        return []
    lp = ",".join("?" * len(unit_lessons))
    sql = (f"SELECT kp.id, kp.lesson_seq, kp.target, kp.category, kp.options_json, "
           f"       COUNT(*) AS wrong_times "
           f"FROM dictation_items di "
           f"JOIN dictation_history dh ON dh.id = di.history_id "
           f"JOIN knowledge_points kp ON kp.id = di.kp_id "
           f"WHERE dh.user_id = ? AND di.is_correct = 0 "
           f"  AND kp.lesson_seq IN ({lp}) AND kp.category != ? "
           f"GROUP BY kp.id ORDER BY wrong_times DESC, kp.lesson_seq, kp.id")
    return await _rows(db, sql, (user_id, *unit_lessons, CAT_POLY))


class Picker:
    def __init__(self, target, rng=None):
        self.target = target
        self.rng = rng or random
        self.items = []
        self.seen_text = set()
        self.seen_char = set()
        self.seen_kp = set()

    def full(self):
        return len(self.items) >= self.target

    def _opts(self, row):
        try:
            o = json.loads(row["options_json"])
            if isinstance(o, str):
                o = json.loads(o)
            return [x for x in o if isinstance(x, dict) and x.get("text")]
        except Exception:
            return []

    def add(self, row, word_type):
        if self.full():
            return False
        if row["id"] in self.seen_kp:
            return False
        cat = row["category"]
        if cat == CAT_CHAR and row["target"] in self.seen_char:
            return False

        opts = self._opts(row)
        if not opts:
            text, pinyin, extra = row["target"], "", {}
        else:
            pick = self.rng.choice(opts) if (cat == CAT_CHAR and len(opts) > 1) else opts[0]
            text = pick.get("text") or row["target"]
            pinyin = pick.get("pinyin", "")
            extra = {k: v for k, v in pick.items() if k not in ("text", "pinyin")}

        if text in self.seen_text:
            if cat != CAT_CHAR:
                return False
            alt = [o for o in opts if o["text"] not in self.seen_text]
            if not alt:
                return False
            text, pinyin = alt[0]["text"], alt[0].get("pinyin", "")
            extra = {k: v for k, v in alt[0].items() if k not in ("text", "pinyin")}

        item = {"id": row["id"], "target": text, "pinyin": pinyin,
                "word_type": word_type, "category": cat,
                "source_lesson": row["lesson_seq"]}
        if extra.get("pair_id"):
            item["pair_id"] = extra["pair_id"]
        self.items.append(item)
        self.seen_text.add(text)
        self.seen_kp.add(row["id"])
        if cat == CAT_CHAR:
            self.seen_char.add(row["target"])
        return True

    def extend(self, rows, word_type):
        for r in rows:
            if self.full():
                break
            self.add(r, word_type)


def _space_typo_pairs(items, min_gap=TYPO_MIN_SPACING):
    paired = [it for it in items if it.get("pair_id")]
    if len(paired) < 2:
        return items
    others = [it for it in items if not it.get("pair_id")]
    buckets = {}
    for it in paired:
        buckets.setdefault(it["pair_id"], []).append(it)
    rounds = []
    while any(buckets.values()):
        for pid in list(buckets.keys()):
            if buckets[pid]:
                rounds.append(buckets[pid].pop(0))
            else:
                del buckets[pid]
    n_pairs = len({it["pair_id"] for it in paired})
    result, oi = [], 0
    if n_pairs >= min_gap:
        step = max(1, len(rounds) // (len(others) + 1)) if others else 1
        for idx, it in enumerate(rounds):
            result.append(it)
            if oi < len(others) and (idx + 1) % step == 0:
                result.append(others[oi]); oi += 1
        result.extend(others[oi:])
        return result
    for it in rounds:
        result.append(it)
        for _ in range(min_gap - 1):
            if oi < len(others):
                result.append(others[oi]); oi += 1
    result.extend(others[oi:])
    return result


async def _recent_poly_ids(db, user_id, limit=8):
    """最近 limit 次听写各自播报过的多音字 kp_id。

    返回 [set, set, ...]，索引 0 是最近一次。
    多音字不入 dictation_items（不判分），所以单独记在
    dictation_history.poly_ids 里，逗号分隔。
    """
    try:
        rows = await _rows(
            db, "SELECT poly_ids FROM dictation_history "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    except Exception:
        # 旧库还没有 poly_ids 列时：视作无历史，不影响出题
        return []
    out = []
    for r in rows:
        raw = (r.get("poly_ids") or "").strip()
        ids = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        out.append(ids)
    return out


async def _polyphonic_section(db, lesson_seq, user_id=1, count=POLY_PER_LESSON):
    # 先把候选池收全 —— 原实现凑够 count 就停止回溯，池子永远只有 2 个，
    # 没有任何轮换空间，这是每次固定同两个多音字的直接原因。
    rows    = list(await _kps_of(db, [lesson_seq], [CAT_POLY]))
    own_ids = {r["id"] for r in rows}
    have    = set(own_ids)
    order   = await regular_lessons(db)
    prior   = [l for l in order if l < lesson_seq]
    prior.reverse()
    prior.append(COLD_START_LESSON)
    for lid in prior:
        for r in await _kps_of(db, [lid], [CAT_POLY]):
            if r["id"] not in have:
                rows.append(r); have.add(r["id"])

    # 休息规则：最近两次听写都出现过的字，这一轮不抽（休息一轮）。
    # 若排除后不足 count，放宽回全池，保证段落不为空。
    hist    = await _recent_poly_ids(db, user_id, limit=8)
    resting = (hist[0] & hist[1]) if len(hist) >= 2 else set()
    pool    = [r for r in rows if r["id"] not in resting]
    if len(pool) < count:
        pool = rows

    def _rank(r):
        kid   = r["id"]
        times = sum(1 for s in hist if kid in s)   # 近期出现次数，越少越优先
        # 上次出现在几轮前；从未出现视为最久（优先级最高）
        ago   = next((i for i, s in enumerate(hist) if kid in s), len(hist) + 99)
        # 本课自带的多音字优先于回溯补来的
        return (0 if kid in own_ids else 1, times, -ago, r["lesson_seq"], kid)

    pool.sort(key=_rank)

    out = []
    for r in pool[:count]:
        try:
            opts = json.loads(r["options_json"])
            if isinstance(opts, str):
                opts = json.loads(opts)
        except Exception:
            opts = []
        out.append({
            "id": r["id"], "character": r["target"],
            "readings": [{"pron": o.get("pron", ""),
                          "example_word": o.get("text", ""),
                          "example_pinyin": o.get("pinyin", "")}
                         for o in opts if isinstance(o, dict)],
        })
    return out


async def build_word_list(db, lesson_seq, user_id=1, rng=None):
    rng = rng or random
    review = is_review_lesson(lesson_seq)
    picker = Picker(TARGET_REVIEW if review else TARGET_DAILY, rng)

    if review:
        await _fill_review(db, picker, lesson_seq, user_id)
        poly = []
    else:
        await _fill_daily(db, picker, lesson_seq, user_id)
        poly = await _polyphonic_section(db, lesson_seq, user_id)

    return _space_typo_pairs(picker.items), poly


async def _fill_daily(db, picker, lesson_seq, user_id):
    sessions = await _session_ids(db, user_id, limit=8)

    if sessions:
        picker.extend(await _wrong_kps_in_sessions(db, sessions[:1]), "wrong_last")
    picker.extend(await _kps_of(db, [lesson_seq], [CAT_WORD, CAT_TYPO]), "new_word")
    picker.extend(await _kps_of(db, [lesson_seq], [CAT_CHAR]), "new_char")

    if not picker.full() and len(sessions) > 1:
        picker.extend(await _wrong_kps_in_sessions(db, sessions[1:4]), "wrong_recent")

    order = await regular_lessons(db)
    prior = [l for l in order if l < lesson_seq]
    if not picker.full() and not prior:
        picker.extend(await _kps_of(db, [COLD_START_LESSON], [CAT_TYPO, CAT_WORD]), "filler")

    recent3 = prior[-3:][::-1]
    if not picker.full() and recent3:
        picker.extend(await _kps_of(db, recent3, [CAT_CHAR]), "review")
    if not picker.full() and recent3:
        picker.extend(await _kps_of(db, recent3, [CAT_WORD, CAT_TYPO]), "review")

    if not picker.full():
        for lid in prior[:-3][::-1]:
            if picker.full():
                break
            picker.extend(await _kps_of(db, [lid], [CAT_CHAR, CAT_WORD, CAT_TYPO]), "review")

    if not picker.full():
        picker.extend(await _kps_of(db, [COLD_START_LESSON], [CAT_TYPO, CAT_WORD]), "review")


async def _fill_review(db, picker, lesson_seq, user_id):
    unit_prefix = lesson_seq // 10
    order = await regular_lessons(db)
    kids = [l for l in order if l // 10 == unit_prefix]

    picker.extend(await _unit_wrong_kps(db, user_id, kids), "wrong_last")
    if not picker.full() and kids:
        picker.extend(await _kps_of(db, kids, [CAT_WORD]), "review")
    if not picker.full() and kids:
        picker.extend(await _kps_of(db, kids, [CAT_CHAR]), "review")
    if not picker.full():
        # 复习课自带的易混字：不是孩子错过的词，归 filler（紫色）
        picker.extend(await _kps_of(db, [lesson_seq], [CAT_TYPO]), "filler")
    if not picker.full():
        picker.extend(await _kps_of(db, [lesson_seq], [CAT_WORD]), "review")
    if not picker.full():
        earlier = [l for l in order if l // 10 < unit_prefix][::-1]
        for lid in earlier:
            if picker.full():
                break
            picker.extend(await _kps_of(db, [lid], [CAT_CHAR, CAT_WORD, CAT_TYPO]), "review")
