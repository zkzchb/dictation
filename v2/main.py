"""听写小助手 V2 API —— FastAPI、SQLite 与预录音频版本。

运行时不调用外部 TTS。课程由外部 content pack 提供，词条和系统提示音以
静态文件服务；前端通过播放列表直接播放每个词的 ``audio_url``。
"""
import os, re, sys, json, hashlib, sqlite3, random, shutil, tempfile, threading
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# 引入共用选词引擎（shared/selector.py）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import selector  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DB_PATH          = os.getenv("DICTATION_DB", os.path.join(BASE_DIR, "dictation.db"))
USER_ID          = 1
EXCLUDE_CATEGORY = "易混淆字"
APP_TIMEZONE      = os.getenv("APP_TIMEZONE", "Asia/Shanghai")

try:
    _TZ = ZoneInfo(APP_TIMEZONE)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f"无效的 APP_TIMEZONE: {APP_TIMEZONE}") from exc

app = FastAPI(title="听写小助手 V2 API")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# ── 数据模型 ─────────────────────────────────────────────────────────────
class WordResult(BaseModel):
    kp_id: int
    is_correct: bool

class SubmitPayload(BaseModel):
    dictation_type: str
    scope_id: int
    results: List[WordResult] = Field(min_length=1, max_length=50)
    user_id: int = USER_ID
    submission_id: str = Field(default="", max_length=64)
    # 本次实际播报的多音字 kp_id。多音字不判分、不入 dictation_items，
    # 所以只有前端知道播了哪些 —— 记下来才能实现「连续两次出现则休息一轮」。
    poly_ids: List[int] = Field(default_factory=list, max_length=10)

# ── 辅助函数 ─────────────────────────────────────────────────────────────
def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

def audio_url_for(text: str) -> str:
    return f"/audio/w/{word_hash(text)}.mp3"

def _now() -> datetime:
    """应用业务时间；与打卡日期和录音台账保持同一时区。"""
    return datetime.now(_TZ)

def extract_word_info(target, options_json):
    text, pinyin = target, ""
    try:
        opts = json.loads(options_json)
        if isinstance(opts, str):
            opts = json.loads(opts)
        if isinstance(opts, list) and opts and isinstance(opts[0], dict):
            text    = opts[0].get("text") or target
            pinyin  = opts[0].get("pinyin", "")
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return text, pinyin

def _next_review(days: int) -> str:
    return (_now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _ensure_v2_schema(conn) -> None:
    """V2 自有的小型迁移；旧数据库可原地升级。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS submission_receipts ("
        "submission_id TEXT PRIMARY KEY, response_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.commit()

# ── 接口 ─────────────────────────────────────────────────────────────────

def _lesson_row(r):
    """把 lessons 行转成前端直接可用的结构（与 V3 保持一致）。

    补两个字段，避免前端自己拼字符串、也避免它暴露内部编号：
      is_review  由内容包声明是否为复习课，前端据此把两个下拉菜单分开
      label      给人看的名字，三种情形：
                   复习课            第一单元 单元复习
                   title 已含在 name 语文园地一
                   其余              第1课 - 大青树下的小学
    """
    d = dict(r)
    seq = d["lesson_seq"]
    title = (d.get("lesson_title") or "").strip()
    name = (d.get("lesson_name") or "").strip()
    unit = (d.get("unit_name") or "").strip()
    d["is_review"] = selector.is_review_lesson(seq)
    if d["is_review"]:
        d["label"] = f"{unit} {name}".strip()
    elif title and title not in name:
        d["label"] = f"{title} - {name}"
    else:
        d["label"] = name or title
    return d


@app.get("/api/lessons")
def get_lessons():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT lesson_seq, unit_id AS unit_seq, unit_name, lesson_name, "
            "       COALESCE(lesson_title, '') AS lesson_title "
            "FROM lessons ORDER BY lesson_seq"
        ).fetchall()
        return [
            _lesson_row(r) for r in rows
            if r["lesson_seq"] != selector.COLD_START_LESSON
        ]
    finally:
        conn.close()


def _select_opt(row):
    """生字随机取组词，其他取第一个。"""
    if row["category"] == "生字":
        try:
            opts = json.loads(row["options_json"])
            if isinstance(opts, str):
                opts = json.loads(opts)
            valid = [o for o in opts if isinstance(o, dict) and o.get("text")]
            if valid:
                return random.choice(valid)
        except Exception:
            pass
    text, pinyin = extract_word_info(row["target"], row["options_json"])
    return {"text": text, "pinyin": pinyin}


@app.get("/api/generate_daily/{lesson_seq}")
def generate_daily(lesson_seq: int, mode: str = "daily"):
    """生成词表。

    梯队算法在 shared/selector.py 中实现（见设计文档 §5）：
      * 正式课：使用内容包的 daily_target，7 级梯队，可附多音字段落
      * 复习课：使用内容包的 review_target，3 级梯队，无多音字
    模式由 lesson_seq 自动判定；mode 仅用于拒绝前端课程/模式错配。
    """
    if mode not in ("daily", "unit"):
        raise HTTPException(status_code=400, detail="mode 必须是 daily 或 unit")
    if (mode == "unit") != selector.is_review_lesson(lesson_seq):
        raise HTTPException(status_code=400, detail="mode 与课程类型不匹配")

    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT 1 FROM lessons WHERE lesson_seq=?", (lesson_seq,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="课程不存在")
        words, poly = selector.build_word_list(conn, lesson_seq, user_id=USER_ID)
    finally:
        conn.close()

    data = [{
        "id": w["id"],
        "target": w["target"],
        "pinyin": w["pinyin"],
        "word_type": w["word_type"],
        "category": w.get("category", ""),
        "audio_url": audio_url_for(w["target"]),
    } for w in words]

    # 多音字只需播报"字"本身，前端把它接到播放列表末尾
    for p in poly:
        p["audio_url"] = audio_url_for(p["character"])

    return {"data": data, "polyphonic_section": poly}


@app.post("/api/submit_dictation")
def submit_dictation(payload: SubmitPayload):
    if payload.user_id != USER_ID:
        raise HTTPException(status_code=400, detail="user_id 不受客户端控制")
    if payload.dictation_type not in ("daily", "unit"):
        raise HTTPException(status_code=400, detail="dictation_type 必须是 daily 或 unit")
    if payload.submission_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", payload.submission_id):
        raise HTTPException(status_code=400, detail="submission_id 格式不合法")

    result_ids = [r.kp_id for r in payload.results]
    if len(result_ids) != len(set(result_ids)):
        raise HTTPException(status_code=400, detail="results 含重复 kp_id")
    if len(payload.poly_ids) != len(set(payload.poly_ids)):
        raise HTTPException(status_code=400, detail="poly_ids 含重复 kp_id")

    conn = get_db(); cursor = conn.cursor()
    today = _now().strftime("%Y-%m-%d")
    created_at = _now().strftime("%Y-%m-%d %H:%M:%S")
    correct = sum(1 for r in payload.results if r.is_correct)
    score   = round(correct / len(payload.results) * 100, 2)
    try:
        _ensure_v2_schema(conn)
        lesson = cursor.execute(
            "SELECT lesson_seq FROM lessons WHERE lesson_seq=?", (payload.scope_id,)
        ).fetchone()
        if not lesson:
            raise HTTPException(status_code=400, detail="scope_id 对应课程不存在")

        placeholders = ",".join("?" for _ in result_ids)
        known = cursor.execute(
            f"SELECT id FROM knowledge_points WHERE id IN ({placeholders})", result_ids
        ).fetchall()
        if len(known) != len(result_ids):
            raise HTTPException(status_code=400, detail="results 含未知 kp_id")
        if payload.poly_ids:
            poly_placeholders = ",".join("?" for _ in payload.poly_ids)
            known_poly = cursor.execute(
                f"SELECT id FROM knowledge_points WHERE category=? "
                f"AND id IN ({poly_placeholders})",
                (selector.CAT_POLY, *payload.poly_ids),
            ).fetchall()
            if len(known_poly) != len(payload.poly_ids):
                raise HTTPException(status_code=400, detail="poly_ids 含非多音字或未知 kp_id")

        cursor.execute("BEGIN IMMEDIATE")
        if payload.submission_id:
            prior = cursor.execute(
                "SELECT response_json FROM submission_receipts WHERE submission_id=?",
                (payload.submission_id,),
            ).fetchone()
            if prior:
                conn.rollback()
                return json.loads(prior["response_json"])

        cursor.execute(
            "INSERT INTO dictation_history "
            "(user_id, dictation_type, scope_id, score, poly_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (USER_ID, payload.dictation_type, payload.scope_id, score,
             ",".join(str(i) for i in payload.poly_ids), created_at),
        )
        hid = cursor.lastrowid
        for r in payload.results:
            cursor.execute(
                "INSERT INTO dictation_items (history_id, kp_id, is_correct) VALUES (?,?,?)",
                (hid, r.kp_id, 1 if r.is_correct else 0),
            )
            mem = cursor.execute(
                "SELECT id, error_count, correct_streak FROM user_memory "
                "WHERE user_id=? AND kp_id=?", (USER_ID, r.kp_id)
            ).fetchone()
            if r.is_correct:
                streak = (mem["correct_streak"] if mem else 0) + 1
                vals   = (1 if streak >= 3 else 0, streak, today,
                          _next_review(min(2**streak, 30)))
                if mem:
                    cursor.execute(
                        "UPDATE user_memory SET status=?,correct_streak=?,"
                        "last_tested_date=?,next_review_date=? WHERE id=?",
                        (*vals, mem["id"]))
                else:
                    cursor.execute(
                        "INSERT INTO user_memory (user_id,kp_id,status,error_count,"
                        "correct_streak,last_tested_date,next_review_date) VALUES (?,?,?,0,?,?,?)",
                        (USER_ID, r.kp_id, *vals))
            else:
                err = (mem["error_count"] if mem else 0) + 1
                if mem:
                    cursor.execute(
                        "UPDATE user_memory SET status=0,error_count=?,correct_streak=0,"
                        "last_tested_date=?,next_review_date=? WHERE id=?",
                        (err, today, _next_review(1), mem["id"]))
                else:
                    cursor.execute(
                        "INSERT INTO user_memory (user_id,kp_id,status,error_count,"
                        "correct_streak,last_tested_date,next_review_date) VALUES (?,?,0,?,0,?,?)",
                        (USER_ID, r.kp_id, err, today, _next_review(1)))
        response = {
            "status": "success", "score": score, "correct": correct,
            "total": len(payload.results),
        }
        if payload.submission_id:
            cursor.execute(
                "INSERT INTO submission_receipts (submission_id,response_json,created_at) "
                "VALUES (?,?,?)",
                (payload.submission_id, json.dumps(response, ensure_ascii=False), created_at),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except sqlite3.OperationalError as e:
        conn.rollback()
        raise HTTPException(status_code=503, detail=f"数据库暂时繁忙，请重试：{e}")
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
    return response


@app.get("/api/dictation_history")
def get_dictation_history(start_date: str, end_date: str):
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期必须是 YYYY-MM-DD")
    if end < start or (end - start).days > 62:
        raise HTTPException(status_code=400, detail="日期范围必须为 0-62 天")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date(created_at) AS d, MAX(score) AS s "
            "FROM dictation_history "
            "WHERE user_id=? AND date(created_at) BETWEEN ? AND ? "
            "GROUP BY date(created_at)",
            (USER_ID, start_date, end_date),
        ).fetchall()
        return {r["d"]: r["s"] for r in rows}
    finally:
        conn.close()


# ================= 🎬 录音工作台 =================
# 录音保存到所选运行数据目录的 audio/w/。
STUDIO_AUDIO_DIR = os.getenv(
    "STUDIO_AUDIO_DIR",
    os.path.join(BASE_DIR, "..", "shared", "web", "audio", "w"),
)
STUDIO_HTML = os.path.join(BASE_DIR, "..", "shared", "web", "studio.html")

# 录音工作台可整体关闭。绑定到 0.0.0.0（局域网可访问）且无鉴权时，
# 建议设 STUDIO_ENABLED=0 —— /studio/* 会写文件到磁盘，不该对整个网段开放。
STUDIO_ENABLED = os.getenv("STUDIO_ENABLED", "1").lower() not in ("0", "false", "no")

# 切片文件名固定是 md5(文本)[:12]，即 12 位小写十六进制。
# 必须校验：hash 来自请求体，直接拼进路径会造成目录穿越（任意文件写入）。
_HASH_RE = re.compile(r"^[0-9a-f]{12}$")


def _safe_slice_path(h) -> str:
    """把请求里的 hash 转成切片路径，格式不合法直接拒绝。"""
    if not isinstance(h, str) or not _HASH_RE.match(h):
        raise HTTPException(status_code=400, detail=f"非法的切片标识: {h!r}")
    return os.path.join(STUDIO_AUDIO_DIR, f"{h}.mp3")


def _require_studio():
    if not STUDIO_ENABLED:
        raise HTTPException(status_code=403, detail="录音工作台已关闭（STUDIO_ENABLED=0）")


# 真人录音台账。
# 为什么需要它：切片文件名是 md5(词面)[:12]，真人录音和 TTS 占位「同名同路径」，
# 磁盘上无从区分。只看文件存在与否的话，装入完整基线后
# 「哪些还没录」永远是空集，工作台会直接显示「全部录制完成」。
# 放在 audio/ 下是有意的：rsync 同步切片时它会一起走，本地与 VPS 对录音进度
# 的认知保持一致。
STUDIO_LEDGER = os.path.join(STUDIO_AUDIO_DIR, "..", ".recorded.json")
STUDIO_CHECK_LEDGER = os.path.join(STUDIO_AUDIO_DIR, "..", ".checked.json")
STUDIO_RERECORD_LIST = os.path.join(STUDIO_AUDIO_DIR, "..", ".rerecord.json")

MAX_RECORDING_BYTES = 32 * 1024 * 1024
MAX_SLICE_BYTES     = 4 * 1024 * 1024
MAX_BATCH_BYTES     = 32 * 1024 * 1024
MAX_SYS_BYTES       = 8 * 1024 * 1024
MAX_WORD_COUNT      = 20
MAX_RECORDING_MS    = 5 * 60 * 1000
_STATE_LOCK         = threading.RLock()


def _load_json(path, fallback):
    """读取状态；主文件损坏时读备份，不能静默重置已录进度。"""
    with _STATE_LOCK:
        if not os.path.exists(path) and not os.path.exists(path + ".bak"):
            return fallback
        errors = []
        for candidate in (path, path + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                errors.append(f"{os.path.basename(candidate)}: {exc}")
        raise HTTPException(
            status_code=500,
            detail="录音状态文件损坏，请从备份恢复：" + "; ".join(errors),
        )


def _save_json(path, data) -> None:
    """唯一临时文件 + 原子替换 + 最近一份有效备份。"""
    with _STATE_LOCK:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as current:
                    json.load(current)
                shutil.copy2(path, path + ".bak")
            except Exception:
                # 主文件已坏时不能用它覆盖最后一份好备份。
                pass
        fd, tmp = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".part", dir=parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _save_bytes(path: str, data: bytes) -> None:
    """把音频原子写入目标路径；并发请求不共用 .part 文件。"""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".part", dir=parent,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_ledger() -> dict:
    """读取真人录音台账；结构错误必须显式阻塞，避免覆盖真实进度。"""
    data = _load_json(STUDIO_LEDGER, {})
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=".recorded.json 结构错误，应为对象")
    return data


def _load_check_ledger() -> dict:
    data = _load_json(STUDIO_CHECK_LEDGER, {})
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=".checked.json 结构错误，应为对象")
    return data


def _load_rerecord_list() -> dict:
    data = _load_json(STUDIO_RERECORD_LIST, {"words": []})
    if not isinstance(data, dict) or not isinstance(data.get("words"), list):
        raise HTTPException(status_code=500, detail=".rerecord.json 结构错误，应含 words 数组")
    return data


def _mark_recorded(pairs) -> None:
    """把本次保存的词记进台账，并同步质检/重录进度。

    无论来自 studio 还是 studio2，新录音都必须重新质检，所以删除旧检查状态；
    如果它属于当前重录词表，则同时把该词记为已重录。
    """
    with _STATE_LOCK:
        led = _load_ledger()
        now = _now().strftime("%Y-%m-%d %H:%M:%S")
        for h, text in pairs:
            led[h] = {"text": text, "at": now}
        _save_json(STUDIO_LEDGER, led)

        changed_hashes = {h for h, _text in pairs}
        checked = _load_check_ledger()
        if any(h in checked for h in changed_hashes):
            for h in changed_hashes:
                checked.pop(h, None)
            _save_json(STUDIO_CHECK_LEDGER, checked)

        rerecord = _load_rerecord_list()
        changed = False
        for word in rerecord.get("words", []):
            if isinstance(word, dict) and word.get("hash") in changed_hashes:
                word["done"] = True
                word["rerecorded_at"] = now
                changed = True
        if changed:
            _save_json(STUDIO_RERECORD_LIST, rerecord)


@app.get("/studio")
async def studio_page():
    from fastapi.responses import HTMLResponse, PlainTextResponse
    _require_studio()
    if not os.path.exists(STUDIO_HTML):
        return PlainTextResponse("studio.html 未找到", status_code=404)
    with open(STUDIO_HTML, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/studio/words")
def studio_words():
    """录音台词表 —— 直接从题库生成，不需要手动上传 JSON。

    取词规则与内容包录音清单保持一致，这样算出的
    hash 才能和已有切片文件名对上：
      * 多音字取「单字」本身（前端播的就是单字，组词只是给家长看的参考）
      * 其余类别取 options_json 里所有候选组词
    按 (课序, kp.id) 排列而非字母序 —— 这样每 10 个一组天然属于同一课，
    老师照着课本录更连贯。同一个词在多课出现时只保留首次。

    排序在纯课序之外做了两处调整，都是为了让老师照课本顺序录：

      1. 内容包的冷启动池只是首门正式课的填充来源，排到最后，避免优先录制填充词。
      2. 内容包显式声明的复习课排在同单元正式课之后，符合教材使用顺序。

    排序在 Python 中使用内容包配置，不再从课程编号的末位推断课程类型。

    注意排序同时决定「词归哪一课」——去重只保留首次出现，所以复习课后置会把
    与正课重复的词改判到正课名下（实测 38 个词），词表总数与 hash 集合不变。
    """
    _require_studio()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT kp.id, kp.lesson_seq, kp.target, kp.category, kp.options_json, "
            "       COALESCE(l.unit_id, 0) AS unit_id, "
            "       COALESCE(l.lesson_title,'') AS lesson_title, "
            "       COALESCE(l.lesson_name,'')  AS lesson_name "
            "FROM knowledge_points kp "
            "LEFT JOIN lessons l ON l.lesson_seq = kp.lesson_seq "
            "ORDER BY kp.lesson_seq, kp.id"
        ).fetchall()
        rows = sorted(
            rows,
            key=lambda row: (
                row["lesson_seq"] == selector.COLD_START_LESSON,
                row["unit_id"],
                selector.is_review_lesson(row["lesson_seq"]),
                row["lesson_seq"],
                row["id"],
            ),
        )
    finally:
        conn.close()

    out, seen = [], set()

    def _add(text, pinyin, row, poly=False, example=""):
        text = (text or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        title = (row["lesson_title"] or "").strip()
        name = (row["lesson_name"] or "").strip()
        item = {
            "text": text,
            "pinyin": (pinyin or "").strip(),
            "hash": word_hash(text),
            "lesson_seq": row["lesson_seq"],
            "lesson": f"{title} {name}".strip() or str(row["lesson_seq"]),
        }
        if poly:
            # 录音台据此提示「读 jìn 这个音，如『尽力』」，并标出多音字身份
            item["poly"] = True
            item["example"] = (example or "").strip()
        out.append(item)

    for r in rows:
        opts = r["options_json"] or []
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
                if isinstance(opts, str):     # 历史数据存在双重编码
                    opts = json.loads(opts)
            except Exception:
                opts = []

        if r["category"] == selector.CAT_POLY:
            # 多音字录的是「单字」，但必须读本课那个音 —— 提词器上光一个「尽」
            # 老师不知道该读 jǐn 还是 jìn。把本课读音与例词一起带给前端。
            pick = selector.lesson_reading(opts, r["id"]) or {}
            _add(r["target"], pick.get("pron", ""), r,
                 poly=True, example=pick.get("text", ""))
            continue

        added = False
        for o in opts:
            if isinstance(o, dict) and (o.get("text") or "").strip():
                _add(o["text"], o.get("pinyin", ""), r)
                added = True
        if not added:
            _add(r["target"], "", r)

    return {"words": out, "total": len(out)}


@app.get("/api/studio/status")
def studio_status():
    """哪些词已由真人录过。返回 {recorded: {hash: {text, at}}, count}。

    与 /api/studio/check 的区别：check 只看文件在不在（TTS 占位也算），
    这里只认台账里记过的真人录音。
    """
    _require_studio()
    led = _load_ledger()
    return {"recorded": led, "count": len(led)}


# ── 人工录音质检与重录词表 ─────────────────────────────────────────────
def _check_words_data():
    """返回所有真人录音及其质检状态，顺序与录音台词表完全一致。"""
    all_words = studio_words().get("words", [])
    recorded = _load_ledger()
    checked = _load_check_ledger()
    words = []
    for source in all_words:
        h = source.get("hash")
        rec = recorded.get(h)
        if not isinstance(rec, dict):
            continue
        item = dict(source)
        item["recorded_at"] = rec.get("at", "")
        state = checked.get(h)
        status = state.get("status") if isinstance(state, dict) else None
        item["status"] = status if status in ("checked", "rerecord") else "pending"
        # 重录会覆盖同一路径；用真人录音时间生成版本参数，避开浏览器七天音频缓存。
        item["audio_url"] = f"/audio/w/{h}.mp3?v={word_hash(str(rec.get('at', '')))}"
        words.append(item)
    return words


def _check_stats(words):
    stats = {"total": len(words), "pending": 0, "checked": 0, "rerecord": 0}
    for word in words:
        status = word.get("status", "pending")
        if status not in stats:
            status = "pending"
        stats[status] += 1
    return stats


@app.get("/api/check/words")
def check_words():
    """质检队列：只列已登记的真人录音，不把 TTS 占位算进去。"""
    _require_studio()
    words = _check_words_data()
    rerecord = _load_rerecord_list()
    saved_words = [w for w in rerecord.get("words", []) if isinstance(w, dict)]
    return {
        "words": words,
        "stats": _check_stats(words),
        "rerecord_list": {
            "count": len(saved_words),
            "done": sum(1 for w in saved_words if w.get("done")),
            "created_at": rerecord.get("created_at", ""),
        },
    }


@app.post("/api/check/mark")
async def check_mark(payload: dict):
    """即时保存单词质检结果。status 仅允许 checked / rerecord。"""
    _require_studio()
    h = payload.get("hash")
    _safe_slice_path(h)
    status = payload.get("status")
    if status not in ("checked", "rerecord"):
        raise HTTPException(status_code=400, detail="status 必须是 checked 或 rerecord")
    with _STATE_LOCK:
        recorded = _load_ledger()
        rec = recorded.get(h)
        if not isinstance(rec, dict):
            raise HTTPException(status_code=404, detail="该词没有真人录音")
        checked = _load_check_ledger()
        checked[h] = {
            "status": status,
            "text": rec.get("text", ""),
            "at": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "recorded_at": rec.get("at", ""),
        }
        _save_json(STUDIO_CHECK_LEDGER, checked)
    return {"status": "success", "hash": h, "result": status}


@app.post("/api/check/save_rerecord")
async def check_save_rerecord():
    """把已标记问题的词保存成 studio2 可直接读取的新词表。"""
    _require_studio()
    with _STATE_LOCK:
        words = _check_words_data()
        stats = _check_stats(words)
        if stats["pending"]:
            raise HTTPException(status_code=409, detail=f"还有 {stats['pending']} 个词未检查")
        selected = []
        for word in words:
            if word.get("status") != "rerecord":
                continue
            item = {k: word[k] for k in
                    ("text", "pinyin", "hash", "lesson_seq", "lesson", "poly", "example")
                    if k in word}
            item["done"] = False
            selected.append(item)
        data = {
            "created_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "words": selected,
        }
        _save_json(STUDIO_RERECORD_LIST, data)
    return {"status": "success", "count": len(selected), "studio_url": "/studio2.html"}


@app.get("/api/studio2/words")
def studio2_words():
    """重录工作台词表；由 check 页面生成，保持原 Studio 字段格式。"""
    _require_studio()
    data = _load_rerecord_list()
    words = []
    for source in data.get("words", []):
        if not isinstance(source, dict):
            continue
        item = {k: v for k, v in source.items()
                if k not in ("done", "rerecorded_at")}
        words.append(item)
    return {"words": words, "total": len(words), "created_at": data.get("created_at", "")}


@app.get("/api/studio2/status")
def studio2_status():
    """当前重录词表的完成台账，供复制版 Studio 显示组进度。"""
    _require_studio()
    data = _load_rerecord_list()
    recorded = {}
    for word in data.get("words", []):
        if not isinstance(word, dict) or not word.get("done"):
            continue
        h = word.get("hash")
        if isinstance(h, str) and _HASH_RE.match(h):
            recorded[h] = {
                "text": word.get("text", ""),
                "at": word.get("rerecorded_at", ""),
            }
    return {"recorded": recorded, "count": len(recorded)}


@app.post("/api/studio/check")
async def studio_check(payload: dict):
    """检查哪些 hash 已有音频文件（含 TTS 占位）。返回 {hash: bool}。

    保留此接口是为了兼容；判断「是否已由真人录过」请用 /api/studio/status。
    """
    _require_studio()
    hashes = payload.get("hashes", [])
    if not isinstance(hashes, list) or len(hashes) > 1000:
        raise HTTPException(status_code=400, detail="hashes 必须是至多 1000 项的数组")
    out = {}
    for h in hashes:
        # 查询接口对非法值宽容处理：标记 False 而非整个请求失败
        out[h] = bool(isinstance(h, str) and _HASH_RE.match(h)
                      and os.path.exists(os.path.join(STUDIO_AUDIO_DIR, f"{h}.mp3")))
    return out


@app.post("/api/studio/split")
async def studio_split(request: Request):
    """上传录音 → pydub 按静音切割 → 返回各段 base64。

    依赖两样东西，缺哪样都在这里显式报出来，别让前端只看到 500：
      * python-multipart —— starlette 解析 multipart/form-data 要用
      * ffmpeg + pydub  —— 解码 webm 与导出 mp3 要用
    """
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    import io, base64 as b64

    _require_studio()
    try:
        form = await request.form()
    except AssertionError as e:
        # 缺 python-multipart 时 starlette 在这里抛 AssertionError。原先没接，
        # 冒到前端就是 Internal Server Error，而前端 catch 里写的是「服务器需装
        # ffmpeg/pydub」，把排查方向带偏了整整一轮。
        raise HTTPException(
            status_code=500,
            detail=f"服务器缺少 python-multipart，无法接收录音表单：{e}",
        )
    audio_file = form.get("audio")
    if not audio_file or not hasattr(audio_file, "read"):
        raise HTTPException(status_code=400, detail="缺少 audio 录音文件")
    try:
        word_count = int(form.get("word_count", 0))
        min_silence_len = int(form.get("min_silence_len", 500))
        silence_thresh = int(form.get("silence_thresh", -40))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="切分参数必须是整数")
    if not 1 <= word_count <= MAX_WORD_COUNT:
        raise HTTPException(status_code=400, detail=f"word_count 必须在 1-{MAX_WORD_COUNT} 之间")
    if not 100 <= min_silence_len <= 5000:
        raise HTTPException(status_code=400, detail="min_silence_len 必须在 100-5000ms 之间")
    if not -80 <= silence_thresh <= -5:
        raise HTTPException(status_code=400, detail="silence_thresh 必须在 -80 至 -5 dBFS 之间")

    raw = await audio_file.read(MAX_RECORDING_BYTES + 1)
    if len(raw) > MAX_RECORDING_BYTES:
        raise HTTPException(status_code=413, detail="录音文件超过 32MB")
    if not raw:
        raise HTTPException(status_code=400, detail="录音文件为空")
    try:
        seg = AudioSegment.from_file(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"音频解码失败: {e}")
    if len(seg) > MAX_RECORDING_MS:
        raise HTTPException(status_code=413, detail="单次录音超过 5 分钟")

    chunks = split_on_silence(
        seg, min_silence_len=min_silence_len,
        silence_thresh=silence_thresh, keep_silence=200,
    )
    segments = []
    for c in chunks:
        buf = io.BytesIO()
        c.export(buf, format="mp3", bitrate="64k")
        segments.append({"audio": b64.b64encode(buf.getvalue()).decode(),
                         "duration": round(len(c) / 1000.0, 2)})

    return {"segments": segments, "count": len(segments),
            "expected": word_count, "matched": len(segments) == word_count}


@app.post("/api/studio/save")
async def studio_save(payload: dict):
    """保存已校对的切片。payload: {items: [{hash, text, audio(base64)}]}。

    hash 经 _safe_slice_path 校验（12 位小写十六进制），否则整批拒绝 ——
    未校验时 hash 可含 ../ 穿越出音频目录，造成任意文件写入。
    text 可选，只用于台账可读性。
    """
    import base64 as b64
    _require_studio()

    items = payload.get("items", [])
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_WORD_COUNT:
        raise HTTPException(status_code=400, detail=f"items 必须是 1-{MAX_WORD_COUNT} 项的数组")

    # 先全部校验再落盘：避免写了一半才发现有非法项
    planned = []
    total_bytes = 0
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="items 每项必须是对象")
        h = item.get("hash")
        path = _safe_slice_path(h)
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 64:
            raise HTTPException(status_code=400, detail="text 必须是 1-64 字符的非空字符串")
        text = text.strip()
        if word_hash(text) != h:
            raise HTTPException(status_code=400, detail=f"切片标识与词语不匹配: {text}")
        encoded_audio = item.get("audio") or ""
        if not isinstance(encoded_audio, str) or len(encoded_audio) > (MAX_SLICE_BYTES * 4 // 3 + 8):
            raise HTTPException(status_code=413, detail="单个切片的 base64 数据过大")
        try:
            data = b64.b64decode(encoded_audio, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="audio 不是合法的 base64")
        if not data:
            raise HTTPException(status_code=400, detail="audio 为空")
        if len(data) > MAX_SLICE_BYTES:
            raise HTTPException(status_code=413, detail="单个切片超过 4MB")
        total_bytes += len(data)
        if total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="本批切片超过 32MB")
        planned.append((h, path, data, text))

    for _h, path, data, _t in planned:
        _save_bytes(path, data)

    # 记账：切片文件名是内容 MD5，真人录音与 TTS 占位同名，磁盘上无从区分。
    # 台账让工作台知道哪些是真人录的，从而显示准确进度。重录会刷新时间戳。
    _mark_recorded([(h, t) for h, _p, _d, t in planned])
    return {"status": "success", "saved": len(planned)}


# ── 系统提示音人工录音 ─────────────────────────────────────────────────
# 播放列表里的引导音（开场 / 第N组 / 多音字前后缀 / 收尾）原先全是 TTS 合成。
# 老师可以逐条录真人版覆盖。文件与 TTS 同名同路径（audio/sys/{key}.mp3），
# 播放端无感知；用独立台账 .recorded_sys.json 区分真人/TTS，只供进度显示。
SYS_AUDIO_DIR = os.path.abspath(os.path.join(STUDIO_AUDIO_DIR, "..", "sys"))
SYS_LEDGER    = os.path.join(STUDIO_AUDIO_DIR, "..", ".recorded_sys.json")


def _sys_keys():
    """要录的系统音清单。poly_intro 已弃用（新前端不播），不列入。"""
    from audio_catalog import MAX_GROUPS
    return (["intro", "poly_prefix", "poly_suffix", "outro"]
            + [f"g{n}" for n in range(1, MAX_GROUPS + 1)])


def _load_sys_ledger() -> dict:
    data = _load_json(SYS_LEDGER, {})
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=".recorded_sys.json 结构错误，应为对象")
    return data


@app.get("/api/studio/syswords")
def studio_syswords():
    """系统提示音清单 + 每条是否已有真人录音。"""
    from audio_catalog import SYS_PHRASES
    _require_studio()
    led = _load_sys_ledger()
    words = [{
        "key": k,
        "text": SYS_PHRASES.get(k, ""),
        "recorded": k in led,
        "url": f"/audio/sys/{k}.mp3",
    } for k in _sys_keys()]
    return {"words": words, "total": len(words)}


@app.post("/api/studio/save_sys")
async def studio_save_sys(payload: dict):
    """保存一条系统提示音的真人录音，覆盖 TTS 文件并记账。"""
    import base64 as b64
    import io
    from pydub import AudioSegment
    from audio_catalog import SYS_PHRASES
    _require_studio()
    key = (payload.get("key") or "").strip()
    if key not in _sys_keys():                      # 白名单，防路径穿越
        raise HTTPException(status_code=400, detail=f"非法 key: {key}")
    encoded_audio = payload.get("audio") or ""
    if not isinstance(encoded_audio, str) or len(encoded_audio) > (MAX_SYS_BYTES * 4 // 3 + 8):
        raise HTTPException(status_code=413, detail="系统提示音的 base64 数据过大")
    try:
        data = b64.b64decode(encoded_audio, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="audio 不是合法的 base64")
    if not data:
        raise HTTPException(status_code=400, detail="audio 为空")
    if len(data) > MAX_SYS_BYTES:
        raise HTTPException(status_code=413, detail="系统提示音录音超过 8MB")

    # 浏览器 MediaRecorder 产出 WebM；必须真正转成 MP3，不能只改扩展名。
    try:
        segment = AudioSegment.from_file(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"系统提示音解码失败: {e}")
    if len(segment) > 30_000:
        raise HTTPException(status_code=413, detail="单条系统提示音超过 30 秒")
    encoded = io.BytesIO()
    try:
        segment.export(encoded, format="mp3", bitrate="64k")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"系统提示音转码失败: {e}")

    dest = os.path.join(SYS_AUDIO_DIR, f"{key}.mp3")
    _save_bytes(dest, encoded.getvalue())

    with _STATE_LOCK:
        led = _load_sys_ledger()
        led[key] = {"text": SYS_PHRASES.get(key, ""),
                    "at": _now().strftime("%Y-%m-%d %H:%M:%S")}
        _save_json(SYS_LEDGER, led)
    return {"status": "success", "saved": 1}


@app.get("/api/health")
def health():
    """供 systemd/Docker 健康检查；同时验证静态题库可读。"""
    conn = get_db()
    try:
        lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        knowledge_points = conn.execute(
            "SELECT COUNT(*) FROM knowledge_points"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "status": "ok",
        "version": "v2",
        "timezone": APP_TIMEZONE,
        "database": {"lessons": lessons, "knowledge_points": knowledge_points},
    }


# ================= 📁 静态文件 =================
# 必须放在所有 API 路由（含 /studio）之后 —— 根路径挂载是 catch-all。
# 直接挂运行时 WEB_ROOT，/studio 保存的新录音可立即播放。
# VPS 部署时静态文件由 Caddy 服务；本地直连时由 uvicorn 服务。
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB_DIR = os.getenv("WEB_ROOT", os.path.join(BASE_DIR, "..", "shared", "web"))
_WEB_DIR = os.path.abspath(_WEB_DIR)

if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="www")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8889)
