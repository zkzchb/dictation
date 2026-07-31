"""听写小助手 V2 API —— 预录音切片版，Ubuntu 部署。

与 V1 的核心区别：
  * 运行时不再调用 TTS，也不再使用 ffmpeg 拼接音频；
  * 音频切片由 shared/gen_slices.py 预生成，以静态文件形式服务；
  * /api/generate_daily 直接在每个词上附带切片 URL；
  * /api/generate_audio 接口已移除；
  * 前端用播放列表驱动 <audio>，选好词表即可直接播放。
"""
import os, json, hashlib, sqlite3
from datetime import datetime, timedelta
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
            "FROM lessons WHERE lesson_seq > 0 ORDER BY lesson_seq"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/generate_daily/{lesson_seq}")
def generate_daily(lesson_seq: int, mode: str = "daily"):
    conn = get_db()
    try:
        final, seen = [], set()

        def push(row, word_type):
            text, pinyin = extract_word_info(row["target"], row["options_json"])
            if text not in seen:
                final.append({
                    "id": row["id"], "target": text, "pinyin": pinyin,
                    "word_type": word_type, "audio_url": audio_url_for(text),
                })
                seen.add(text)

        if mode == "daily":
            for row in conn.execute(
                "SELECT id, target, category, options_json FROM knowledge_points "
                "WHERE lesson_seq = ? AND category != ?",
                (lesson_seq, EXCLUDE_CATEGORY)
            ).fetchall():
                push(row, "new_char" if "生字" in row["category"] else "new_word")

            new_ids = {w["id"] for w in final}
            quota   = DAILY_TARGET - len(final)
            if quota > 0:
                for row in conn.execute(
                    "SELECT kp.id, kp.target, kp.category, kp.options_json "
                    "FROM user_memory um JOIN knowledge_points kp ON um.kp_id = kp.id "
                    "WHERE um.user_id = ? AND um.error_count > 0 AND um.status = 0 "
                    "  AND kp.category != ? "
                    "ORDER BY um.next_review_date ASC, um.error_count DESC LIMIT ?",
                    (USER_ID, EXCLUDE_CATEGORY, quota + 20)
                ).fetchall():
                    if len(final) >= DAILY_TARGET: break
                    if row["id"] not in new_ids: push(row, "wrong")
        else:
            seqs = [r[0] for r in conn.execute(
                "SELECT lesson_seq FROM lessons WHERE unit_id = ?", (lesson_seq,)
            ).fetchall()]
            if seqs:
                ph = ",".join("?" * len(seqs))
                for row in conn.execute(
                    f"SELECT kp.id, kp.target, kp.category, kp.options_json, um.error_count "
                    f"FROM knowledge_points kp "
                    f"LEFT JOIN user_memory um ON kp.id = um.kp_id AND um.user_id = ? "
                    f"WHERE kp.lesson_seq IN ({ph}) AND kp.category != ? "
                    f"ORDER BY COALESCE(um.error_count,0) DESC, RANDOM() LIMIT 60",
                    (USER_ID, *seqs, EXCLUDE_CATEGORY)
                ).fetchall():
                    if len(final) >= DAILY_TARGET: break
                    push(row, "wrong" if row["error_count"] else "review")
        return {"data": final}
    finally:
        conn.close()


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
            "INSERT INTO dictation_history (user_id, dictation_type, scope_id, score) "
            "VALUES (?, ?, ?, ?)",
            (payload.user_id, payload.dictation_type, payload.scope_id, score),
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8889)

