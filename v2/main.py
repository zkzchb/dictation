"""听写小助手 V2 API —— 预录音切片版，Ubuntu 部署。

与 V1 的核心区别：
  * 运行时不再调用 TTS，也不再使用 ffmpeg 拼接音频；
  * 音频切片由 shared/gen_slices.py 预生成，以静态文件形式服务；
  * /api/generate_daily 直接在每个词上附带切片 URL；
  * /api/generate_audio 接口已移除；
  * 前端用播放列表驱动 <audio>，选好词表即可直接播放。
"""
import os, re, sys, json, hashlib, sqlite3, random
from datetime import datetime, timedelta
from typing import List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 引入共用选词引擎（shared/selector.py）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import selector  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DB_PATH          = os.getenv("DICTATION_DB", os.path.join(BASE_DIR, "dictation.db"))
USER_ID          = 1
DAILY_TARGET     = 30
EXCLUDE_CATEGORY = "易混淆字"

app = FastAPI(title="听写小助手 V2 API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── 数据模型 ─────────────────────────────────────────────────────────────
class WordResult(BaseModel):
    kp_id: int
    is_correct: bool

class SubmitPayload(BaseModel):
    dictation_type: str
    scope_id: int
    results: List[WordResult]
    user_id: int = USER_ID
    # 本次实际播报的多音字 kp_id。多音字不判分、不入 dictation_items，
    # 所以只有前端知道播了哪些 —— 记下来才能实现「连续两次出现则休息一轮」。
    poly_ids: List[int] = []

# ── 辅助函数 ─────────────────────────────────────────────────────────────
def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

def audio_url_for(text: str) -> str:
    return f"/audio/w/{word_hash(text)}.mp3"

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
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

# ── 接口 ─────────────────────────────────────────────────────────────────

def _lesson_row(r):
    """把 lessons 行转成前端直接可用的结构（与 V1/V3 保持一致）。

    补两个字段，避免前端自己拼字符串、也避免它暴露内部编号：
      is_review  lesson_seq 末位为 0 即单元复习课，前端据此把两个下拉菜单分开
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
    # 与 selector.is_review_lesson() 保持一致：末位 0 是复习课，但 3000
    # 是冷启动填充池（二年级总复习），选词时按正式课走，不能算复习课。
    # 不排除的话它会落进「单元复习」下拉，而后端按正式课出题 —— 前后端判定打架。
    d["is_review"] = seq % 10 == 0 and seq != 3000
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
            "FROM lessons WHERE lesson_seq >= 3100 ORDER BY lesson_seq"
        ).fetchall()
        return [_lesson_row(r) for r in rows]
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
      * 正式课（lid 末位非 0）：30 词，7 级梯队，附 2 个多音字段落
      * 复习课（lid 末位为 0）：50 词，3 级梯队，无多音字
    模式由 lesson_seq 自动判定，mode 参数仅作兼容保留。
    """
    conn = get_db()
    try:
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
    if not payload.results:
        raise HTTPException(status_code=400, detail="results 不能为空")
    conn = get_db(); cursor = conn.cursor()
    today   = datetime.now().strftime("%Y-%m-%d")
    correct = sum(1 for r in payload.results if r.is_correct)
    score   = round(correct / len(payload.results) * 100, 2)
    try:
        cursor.execute(
            "INSERT INTO dictation_history "
            "(user_id, dictation_type, scope_id, score, poly_ids) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload.user_id, payload.dictation_type, payload.scope_id, score,
             ",".join(str(i) for i in payload.poly_ids)),
        )
        hid = cursor.lastrowid
        for r in payload.results:
            cursor.execute(
                "INSERT INTO dictation_items (history_id, kp_id, is_correct) VALUES (?,?,?)",
                (hid, r.kp_id, 1 if r.is_correct else 0),
            )
            mem = cursor.execute(
                "SELECT id, error_count, correct_streak FROM user_memory "
                "WHERE user_id=? AND kp_id=?", (payload.user_id, r.kp_id)
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
                        (payload.user_id, r.kp_id, *vals))
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
                        (payload.user_id, r.kp_id, err, today, _next_review(1)))
        conn.commit()
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
    return {"status": "success", "score": score, "correct": correct, "total": len(payload.results)}


@app.get("/api/dictation_history")
def get_dictation_history(start_date: str, end_date: str):
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
# 切片保存到 shared/web/audio/w/，与 gen_slices.py 输出一致，可互换
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
# 磁盘上无从区分。只看文件存在与否的话，gen_slices.py 生成完 869 个占位后
# 「哪些还没录」永远是空集，工作台会直接显示「全部录制完成」。
# 放在 audio/ 下是有意的：rsync 同步切片时它会一起走，本地与 VPS 对录音进度
# 的认知保持一致。
STUDIO_LEDGER = os.path.join(STUDIO_AUDIO_DIR, "..", ".recorded.json")


def _load_ledger() -> dict:
    """读台账。读不出来就当空 —— 它只影响进度显示，不该阻塞录音。"""
    try:
        with open(STUDIO_LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mark_recorded(pairs) -> None:
    """把本次保存的词记进台账。重录同一个词会覆盖时间戳。"""
    led = _load_ledger()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for h, text in pairs:
        led[h] = {"text": text, "at": now}
    os.makedirs(os.path.dirname(os.path.abspath(STUDIO_LEDGER)), exist_ok=True)
    tmp = STUDIO_LEDGER + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STUDIO_LEDGER)


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

    取词规则与 shared/gen_slices.py 的 collect_targets 保持一致，这样算出的
    hash 才能和已有切片文件名对上：
      * 多音字取「单字」本身（前端播的就是单字，组词只是给家长看的参考）
      * 其余类别取 options_json 里所有候选组词
    按 (课序, kp.id) 排列而非字母序 —— 这样每 10 个一组天然属于同一课，
    老师照着课本录更连贯。同一个词在多课出现时只保留首次。

    排序在纯课序之外做了两处调整，都是为了让老师照课本顺序录：

      1. COLD_START_LESSON(3000，二年级总复习) 只是第一门正式课的填充池，极少
         真正播到。它的课序最小，若按原序会排在最前，老师一开工录的全是填充词。
      2. 复习课 lid 末位为 0（3110、3120…），数字上小于本单元正课（3111…），
         按原序会排在本单元之前。老师手里的课本是先正课后复习。

    两处都用 SQLite 布尔表达式返回 0/1 的特性做排序键：命中的行键为 1，自然沉到
    同级末尾。整数除法 kp.lesson_seq / 10 取单元号（3110 与 3111 同属 311）。

    注意排序同时决定「词归哪一课」——去重只保留首次出现，所以复习课后置会把
    与正课重复的词改判到正课名下（实测 38 个词），词表总数与 hash 集合不变。
    """
    _require_studio()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT kp.id, kp.lesson_seq, kp.target, kp.category, kp.options_json, "
            "       COALESCE(l.lesson_title,'') AS lesson_title, "
            "       COALESCE(l.lesson_name,'')  AS lesson_name "
            "FROM knowledge_points kp "
            "LEFT JOIN lessons l ON l.lesson_seq = kp.lesson_seq "
            "ORDER BY (kp.lesson_seq = ?), "        # 冷启动填充课整体沉到末尾
            "         kp.lesson_seq / 10, "          # 再按单元
            "         (kp.lesson_seq % 10 = 0), "    # 单元内正课在前、复习课在后
            "         kp.lesson_seq, kp.id",
            (selector.COLD_START_LESSON,)
        ).fetchall()
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


@app.post("/api/studio/check")
async def studio_check(payload: dict):
    """检查哪些 hash 已有音频文件（含 TTS 占位）。返回 {hash: bool}。

    保留此接口是为了兼容；判断「是否已由真人录过」请用 /api/studio/status。
    """
    _require_studio()
    out = {}
    for h in payload.get("hashes", []):
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
    audio_file = form["audio"]
    word_count = int(form.get("word_count", 0))
    min_silence_len = int(form.get("min_silence_len", 500))
    silence_thresh = int(form.get("silence_thresh", -40))

    raw = await audio_file.read()
    try:
        seg = AudioSegment.from_file(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"音频解码失败: {e}")

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
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items 必须是数组")

    # 先全部校验再落盘：避免写了一半才发现有非法项
    planned = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="items 每项必须是对象")
        h = item.get("hash")
        path = _safe_slice_path(h)
        try:
            data = b64.b64decode(item.get("audio") or "", validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="audio 不是合法的 base64")
        if not data:
            raise HTTPException(status_code=400, detail="audio 为空")
        planned.append((h, path, data, item.get("text") or ""))

    os.makedirs(STUDIO_AUDIO_DIR, exist_ok=True)
    for _h, path, data, _t in planned:
        tmp = path + ".part"          # 先写临时文件再原子替换，避免半截文件被播放
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

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
    from gen_slices import MAX_GROUPS
    return (["intro", "poly_prefix", "poly_suffix", "outro"]
            + [f"g{n}" for n in range(1, MAX_GROUPS + 1)])


def _load_sys_ledger() -> dict:
    try:
        with open(SYS_LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.get("/api/studio/syswords")
def studio_syswords():
    """系统提示音清单 + 每条是否已有真人录音。"""
    from gen_slices import SYS_PHRASES
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
    from gen_slices import SYS_PHRASES
    _require_studio()
    key = (payload.get("key") or "").strip()
    if key not in _sys_keys():                      # 白名单，防路径穿越
        raise HTTPException(status_code=400, detail=f"非法 key: {key}")
    try:
        data = b64.b64decode(payload.get("audio") or "", validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="audio 不是合法的 base64")
    if not data:
        raise HTTPException(status_code=400, detail="audio 为空")

    os.makedirs(SYS_AUDIO_DIR, exist_ok=True)
    dest = os.path.join(SYS_AUDIO_DIR, f"{key}.mp3")
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)

    led = _load_sys_ledger()
    led[key] = {"text": SYS_PHRASES.get(key, ""),
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(os.path.dirname(os.path.abspath(SYS_LEDGER)), exist_ok=True)
    stmp = SYS_LEDGER + ".part"
    with open(stmp, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=1)
    os.replace(stmp, SYS_LEDGER)
    return {"status": "success", "saved": 1}


# ================= 📁 静态文件 =================
# 必须放在所有 API 路由（含 /studio）之后 —— 根路径挂载是 catch-all。
# 直接挂 shared/web/ 而非 stage 后的副本，好处是 /studio 录进
# shared/web/audio/w/ 的切片立刻可播，无需重新 stage。
# VPS 部署时这部分由 Caddy 负责；本地直连时由 uvicorn 自己发。
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB_DIR = os.getenv("WEB_ROOT", os.path.join(BASE_DIR, "..", "shared", "web"))
_WEB_DIR = os.path.abspath(_WEB_DIR)

if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="www")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8889)
