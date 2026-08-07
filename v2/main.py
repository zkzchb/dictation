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
@app.get("/api/lessons")
def get_lessons():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT lesson_seq, unit_id AS unit_seq, unit_name, lesson_name "
            "FROM lessons WHERE lesson_seq >= 3100 ORDER BY lesson_seq"
        ).fetchall()
        return [dict(r) for r in rows]
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


@app.get("/studio")
async def studio_page():
    from fastapi.responses import HTMLResponse, PlainTextResponse
    _require_studio()
    if not os.path.exists(STUDIO_HTML):
        return PlainTextResponse("studio.html 未找到", status_code=404)
    with open(STUDIO_HTML, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/studio/check")
async def studio_check(payload: dict):
    """检查哪些 hash 已有录音文件。返回 {hash: bool}。"""
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
    需要系统安装 ffmpeg。
    """
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    import io, base64 as b64

    _require_studio()
    form = await request.form()
    audio_file = form["audio"]
    word_count = int(form.get("word_count", 0))
    min_silence_len = int(form.get("min_silence_len", 500))
    silence_thresh = int(form.get("silence_thresh", -35))

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
    """保存已校对的切片。payload: {items: [{hash, audio(base64)}]}。

    hash 经 _safe_slice_path 校验（12 位小写十六进制），否则整批拒绝 ——
    未校验时 hash 可含 ../ 穿越出音频目录，造成任意文件写入。
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
        path = _safe_slice_path(item.get("hash"))
        try:
            data = b64.b64decode(item.get("audio") or "", validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="audio 不是合法的 base64")
        if not data:
            raise HTTPException(status_code=400, detail="audio 为空")
        planned.append((path, data))

    os.makedirs(STUDIO_AUDIO_DIR, exist_ok=True)
    for path, data in planned:
        tmp = path + ".part"          # 先写临时文件再原子替换，避免半截文件被播放
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return {"status": "success", "saved": len(planned)}


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

