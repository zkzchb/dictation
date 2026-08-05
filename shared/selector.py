"""shared/selector.py —— 听写选词引擎（V1/V2 共用）

实现设计文档 §5 的梯队算法。核心差异（见 §6）：
复习由「会话序 + 课程序」驱动，错词取自 dictation_history + dictation_items，
不再依赖 user_memory.next_review_date。

对外只有一个入口：
    build_word_list(conn, lesson_seq, user_id=1) -> (words, polyphonic_section)

conn 为 sqlite3 连接，需设 row_factory = sqlite3.Row。
"""
import json
import random

CAT_CHAR = "生字"
CAT_WORD = "词语"
CAT_TYPO = "易错字"
CAT_POLY = "多音字"

TARGET_DAILY = 30      # 生字听写词数
TARGET_REVIEW = 50     # 单元复习词数
POLY_PER_LESSON = 2    # 每次附带的多音字个数
COLD_START_LESSON = 3000
TYPO_MIN_SPACING = 3   # 易混字对之间至少间隔几个词


# ────────────────────────────────────────────────────────────────────────
# 基础查询
# ────────────────────────────────────────────────────────────────────────

def is_review_lesson(lesson_seq):
    """lid 末位为 0 即单元复习课（3000 冷启动除外）。"""
    return lesson_seq % 10 == 0 and lesson_seq != COLD_START_LESSON


def regular_lessons(conn):
    """全部正式课 lid 升序（排除复习课与冷启动）。"""
    rows = conn.execute(
        "SELECT lesson_seq FROM lessons WHERE lesson_seq > ? ORDER BY lesson_seq",
        (COLD_START_LESSON,)
    ).fetchall()
    return [r["lesson_seq"] for r in rows if not is_review_lesson(r["lesson_seq"])]


def _kps_of(conn, lesson_seqs, categories):
    """取若干课、若干类别的知识点，按 (课序, id) 稳定排序。"""
    if not lesson_seqs or not categories:
        return []
    lp = ",".join("?" * len(lesson_seqs))
    cp = ",".join("?" * len(categories))
    sql = (f"SELECT id, lesson_seq, target, category, options_json "
           f"FROM knowledge_points "
           f"WHERE lesson_seq IN ({lp}) AND category IN ({cp}) "
           f"ORDER BY lesson_seq, id")
    return conn.execute(sql, (*lesson_seqs, *categories)).fetchall()


def _session_ids(conn, user_id, limit=8):
    """最近若干次听写会话 id，最新在前。"""
    rows = conn.execute(
        "SELECT id FROM dictation_history WHERE user_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    return [r["id"] for r in rows]


def _wrong_kps_in_sessions(conn, history_ids):
    """给定会话中的错词 kp（去重，保持"错得多的靠前"）。"""
    if not history_ids:
        return []
    hp = ",".join("?" * len(history_ids))
    sql = (f"SELECT kp.id, kp.lesson_seq, kp.target, kp.category, kp.options_json, "
           f"       COUNT(*) AS wrong_times "
           f"FROM dictation_items di "
           f"JOIN knowledge_points kp ON kp.id = di.kp_id "
           f"WHERE di.history_id IN ({hp}) AND di.is_correct = 0 "
           f"  AND kp.category != ? "
           f"GROUP BY kp.id "
           f"ORDER BY wrong_times DESC, kp.lesson_seq, kp.id")
    return conn.execute(sql, (*history_ids, CAT_POLY)).fetchall()


def _unit_wrong_kps(conn, user_id, unit_lessons):
    """本单元范围内产生过的全部错词。"""
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
           f"GROUP BY kp.id "
           f"ORDER BY wrong_times DESC, kp.lesson_seq, kp.id")
    return conn.execute(sql, (user_id, *unit_lessons, CAT_POLY)).fetchall()


# ────────────────────────────────────────────────────────────────────────
# 取词器：负责去重与约束
# ────────────────────────────────────────────────────────────────────────

class Picker:
    """累积词表，处理去重（同词、同字）与来源标记。"""

    def __init__(self, target, rng=None):
        self.target = target
        self.rng = rng or random
        self.items = []
        self.seen_text = set()
        self.seen_char = set()   # 已出现的生字，避免同字换个组词又出一次
        self.seen_kp = set()

    def full(self):
        return len(self.items) >= self.target

    def _choose(self, row):
        """生字随机取一个组词；其余取第一个。返回 (text, pinyin, extra)。"""
        try:
            opts = json.loads(row["options_json"])
            if isinstance(opts, str):
                opts = json.loads(opts)
        except Exception:
            opts = []
        valid = [o for o in opts if isinstance(o, dict) and o.get("text")]
        if not valid:
            return row["target"], "", {}
        if row["category"] == CAT_CHAR and len(valid) > 1:
            chosen = self.rng.choice(valid)
        else:
            chosen = valid[0]
        extra = {k: v for k, v in chosen.items() if k not in ("text", "pinyin")}
        return chosen.get("text") or row["target"], chosen.get("pinyin", ""), extra

    def add(self, row, word_type):
        """尝试加入一个 kp。返回是否成功。"""
        if self.full():
            return False
        kp_id = row["id"]
        if kp_id in self.seen_kp:
            return False
        cat = row["category"]

        # 生字：同一个字不重复出题（无论换哪个组词）
        if cat == CAT_CHAR and row["target"] in self.seen_char:
            return False

        text, pinyin, extra = self._choose(row)
        if not text or text in self.seen_text:
            # 生字的首选组词撞了，试另一个候选
            if cat == CAT_CHAR:
                try:
                    opts = json.loads(row["options_json"])
                    if isinstance(opts, str):
                        opts = json.loads(opts)
                except Exception:
                    opts = []
                alt = [o for o in opts if isinstance(o, dict)
                       and o.get("text") and o["text"] not in self.seen_text]
                if not alt:
                    return False
                pick = alt[0]
                text, pinyin = pick["text"], pick.get("pinyin", "")
                extra = {k: v for k, v in pick.items() if k not in ("text", "pinyin")}
            else:
                return False

        item = {
            "id": kp_id,
            "target": text,
            "pinyin": pinyin,
            "word_type": word_type,
            "category": cat,
            "source_lesson": row["lesson_seq"],
        }
        if extra.get("pair_id"):
            item["pair_id"] = extra["pair_id"]
        self.items.append(item)
        self.seen_text.add(text)
        self.seen_kp.add(kp_id)
        if cat == CAT_CHAR:
            self.seen_char.add(row["target"])
        return True

    def extend(self, rows, word_type):
        for r in rows:
            if self.full():
                break
            self.add(r, word_type)


# ────────────────────────────────────────────────────────────────────────
# 易混字对排序：成对出现且间隔 >= TYPO_MIN_SPACING
# ────────────────────────────────────────────────────────────────────────

def _space_typo_pairs(items, min_gap=TYPO_MIN_SPACING):
    """把同 pair_id 的易混字拉开到至少 min_gap 个位置。

    做法：先把成对的词整体抽出，再按"轮转发牌"的方式重新插回，
    使同一对的两个词天然被其它词隔开。比就地交换稳健，不会因
    冲突项靠近末尾而失败。
    """
    paired = [it for it in items if it.get("pair_id")]
    if len(paired) < 2:
        return items

    others = [it for it in items if not it.get("pair_id")]

    # 按 pair_id 归组
    buckets = {}
    for it in paired:
        buckets.setdefault(it["pair_id"], []).append(it)
    # 轮转取词：每轮从每个对里取一个，保证同对的两词相隔 >= 组数
    rounds = []
    while any(buckets.values()):
        for pid in list(buckets.keys()):
            if buckets[pid]:
                rounds.append(buckets[pid].pop(0))
            else:
                del buckets[pid]

    # 若对数不足以自然隔开，则用 others 强制填充间隔
    n_pairs = len({it["pair_id"] for it in paired})
    result = []
    oi = 0
    if n_pairs >= min_gap:
        # 轮转本身已保证间隔，直接把 others 均匀混入
        result = rounds
        step = max(1, len(result) // (len(others) + 1)) if others else 1
        merged = []
        for idx, it in enumerate(result):
            merged.append(it)
            if oi < len(others) and (idx + 1) % step == 0:
                merged.append(others[oi]); oi += 1
        merged.extend(others[oi:])
        return merged

    # 对数少：每放一个成对词，就插入 min_gap-1 个非成对词
    for it in rounds:
        result.append(it)
        for _ in range(min_gap - 1):
            if oi < len(others):
                result.append(others[oi]); oi += 1
    result.extend(others[oi:])
    return result


# ────────────────────────────────────────────────────────────────────────
# 多音字：每次 2 个，本课优先，不足向前回溯
# ────────────────────────────────────────────────────────────────────────

def _polyphonic_section(conn, lesson_seq, count=POLY_PER_LESSON):
    rows = list(_kps_of(conn, [lesson_seq], [CAT_POLY]))
    if len(rows) < count:
        # 从已学课程（含冷启动）向前回溯补足
        order = regular_lessons(conn)
        prior = [l for l in order if l < lesson_seq]
        prior.reverse()
        prior.append(COLD_START_LESSON)
        have = {r["id"] for r in rows}
        for lid in prior:
            if len(rows) >= count:
                break
            for r in _kps_of(conn, [lid], [CAT_POLY]):
                if len(rows) >= count:
                    break
                if r["id"] not in have:
                    rows.append(r)
                    have.add(r["id"])
    out = []
    for r in rows[:count]:
        try:
            opts = json.loads(r["options_json"])
            if isinstance(opts, str):
                opts = json.loads(opts)
        except Exception:
            opts = []
        out.append({
            "id": r["id"],
            "character": r["target"],
            "readings": [{
                "pron": o.get("pron", ""),
                "example_word": o.get("text", ""),
                "example_pinyin": o.get("pinyin", ""),
            } for o in opts if isinstance(o, dict)],
        })
    return out


# ────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────

def build_word_list(conn, lesson_seq, user_id=1, rng=None):
    """按梯队生成词表。

    返回 (words, polyphonic_section)。
    words 每项含 id/target/pinyin/word_type/category/source_lesson[/pair_id]。
    单元复习课不返回多音字段落。
    """
    rng = rng or random
    review = is_review_lesson(lesson_seq)
    picker = Picker(TARGET_REVIEW if review else TARGET_DAILY, rng)

    if review:
        _fill_review(conn, picker, lesson_seq, user_id)
        poly = []
    else:
        _fill_daily(conn, picker, lesson_seq, user_id)
        poly = _polyphonic_section(conn, lesson_seq)

    words = _space_typo_pairs(picker.items)
    return words, poly


def _fill_daily(conn, picker, lesson_seq, user_id):
    """生字听写 7 级梯队。"""
    sessions = _session_ids(conn, user_id, limit=8)

    # 梯队1：上一次听写的错词（全部）—— 最高优先，前端用红色突出
    if sessions:
        picker.extend(_wrong_kps_in_sessions(conn, sessions[:1]), "wrong_last")

    # 梯队2：本课词语表
    picker.extend(_kps_of(conn, [lesson_seq], [CAT_WORD, CAT_TYPO]), "new_word")

    # 梯队3：本课生字表
    picker.extend(_kps_of(conn, [lesson_seq], [CAT_CHAR]), "new_char")

    # 梯队4：前 2–4 次听写的错词 —— 次级警示，前端用橙色
    if not picker.full() and len(sessions) > 1:
        picker.extend(_wrong_kps_in_sessions(conn, sessions[1:4]), "wrong_recent")

    # 冷启动：开学第一课，用 lesson3000 的易混字补
    order = regular_lessons(conn)
    prior = [l for l in order if l < lesson_seq]
    if not picker.full() and not prior:
        picker.extend(_kps_of(conn, [COLD_START_LESSON], [CAT_TYPO, CAT_WORD]), "filler")

    # 梯队5/6：前 1–3 课的生字表，然后词语表
    recent3 = prior[-3:][::-1]
    if not picker.full() and recent3:
        picker.extend(_kps_of(conn, recent3, [CAT_CHAR]), "review")
    if not picker.full() and recent3:
        picker.extend(_kps_of(conn, recent3, [CAT_WORD, CAT_TYPO]), "review")

    # 梯队7：继续向前回溯（可跨单元），一课一课往前取
    if not picker.full():
        for lid in prior[:-3][::-1]:
            if picker.full():
                break
            picker.extend(_kps_of(conn, [lid], [CAT_CHAR, CAT_WORD, CAT_TYPO]), "review")

    # 兜底：仍不足则用冷启动库
    if not picker.full():
        picker.extend(_kps_of(conn, [COLD_START_LESSON], [CAT_TYPO, CAT_WORD]), "review")


def _fill_review(conn, picker, lesson_seq, user_id):
    """单元复习 3 级梯队。"""
    unit_prefix = lesson_seq // 10           # 3110 -> 311
    order = regular_lessons(conn)
    kids = [l for l in order if l // 10 == unit_prefix]

    # 梯队1：本单元错词（全部）
    # 前端标红：本单元真实错词，复习课的重点
    picker.extend(_unit_wrong_kps(conn, user_id, kids), "wrong_last")

    # 梯队2：本单元各课的词语表 + 生字表
    if not picker.full() and kids:
        picker.extend(_kps_of(conn, kids, [CAT_WORD]), "review")
    if not picker.full() and kids:
        picker.extend(_kps_of(conn, kids, [CAT_CHAR]), "review")

    # 梯队3：本复习课自身的易混词 + 高阶词
    if not picker.full():
        # 复习课自带的易混字：不是孩子错过的词，归 filler（紫色）
        picker.extend(_kps_of(conn, [lesson_seq], [CAT_TYPO]), "filler")
    if not picker.full():
        picker.extend(_kps_of(conn, [lesson_seq], [CAT_WORD]), "review")

    # 兜底：本单元之前的课
    if not picker.full():
        earlier = [l for l in order if l // 10 < unit_prefix][::-1]
        for lid in earlier:
            if picker.full():
                break
            picker.extend(_kps_of(conn, [lid], [CAT_CHAR, CAT_WORD, CAT_TYPO]), "review")
