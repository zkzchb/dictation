"""听写小助手 V3 API —— Cloudflare Workers + D1 部署版。

与 V2 相比唯一的基础设施差异：
  * 数据库用 D1（async prepare/bind/run，而非 sqlite3）
  * 运行在 Workers Pyodide 环境（而非 Ubuntu uvicorn）
  * 通过 asgi.fetch 桥接 FastAPI 到 Worker fetch handler

API 合约、间隔重复算法、出题逻辑与 V2 完全一致。
"""
from workers import WorkerEntrypoint, Response
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List
import json, hashlib, asgi
import selector_d1  # 选词引擎(D1异步版)
from datetime import datetime, timedelta

USER_ID = 1
EXCLUDE_CATEGORY = "易混淆字"

app = FastAPI(title="听写小助手 V3 API")

# ── 数据模型 ────────────────────────────────────────────────────────────
class WordResult(BaseModel):
    kp_id: int
    is_correct: bool

class SubmitPayload(BaseModel):
    dictation_type: str
    scope_id: int
    results: List[WordResult]
    user_id: int = USER_ID
    # 本次实际播报的多音字 kp_id。多音字不判分、不入 dictation_items，
    # 所以单独记在 dictation_history.poly_ids，供休息规则读取。
    poly_ids: List[int] = []

# ── Worker 入口点 ────────────────────────────────────────────────────────
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

# ── 辅助函数 ─────────────────────────────────────────────────────────────
def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

def audio_url_for(text: str) -> str:
    return f"./audio/w/{word_hash(text)}.mp3"

def extract_word_info(target, options_json):
    text, pinyin = target, ""
    try:
        opts = json.loads(options_json) if isinstance(options_json, str) else options_json
        if isinstance(opts, str): opts = json.loads(opts)
        if isinstance(opts, list) and opts and isinstance(opts[0], dict):
            text   = opts[0].get("text") or target
            pinyin = opts[0].get("pinyin", "")
    except Exception:
        pass
    return text, pinyin

def _next_review(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _rows_of(result):
    """把 D1 查询结果取成 Python list[dict]。

    不同 workers-py / Pyodide 版本行为不一致：新版 D1 绑定返回的
    result.results 已经是普通 list[dict]，旧版是 JsProxy、需要 .to_py()。
    对已是 list 的对象调 .to_py() 会抛
        AttributeError: 'list' object has no attribute 'to_py'
    所以这里按能力判断，两种运行时都能用。
    """
    rows = result.results
    to_py = getattr(rows, "to_py", None)
    return to_py() if callable(to_py) else list(rows)

# ── 接口 ─────────────────────────────────────────────────────────────────

def _lesson_row(d, runtime):
    """把 lessons 行转成前端直接可用的结构（与 V2 保持一致）。

    补两个字段，避免前端自己拼字符串、也避免它暴露内部编号：
      is_review  由内容包声明是否为复习课，前端据此把两个下拉菜单分开
      label      给人看的名字，三种情形：
                   复习课            第一单元 单元复习
                   title 已含在 name 语文园地一
                   其余              第1课 - 大青树下的小学
    """
    seq = d["lesson_seq"]
    title = (d.get("lesson_title") or "").strip()
    name = (d.get("lesson_name") or "").strip()
    unit = (d.get("unit_name") or "").strip()
    d["is_review"] = selector_d1.is_review_lesson(seq, runtime)
    if d["is_review"]:
        d["label"] = f"{unit} {name}".strip()
    elif title and title not in name:
        d["label"] = f"{title} - {name}"
    else:
        d["label"] = name or title
    return d


@app.get("/api/lessons")
async def get_lessons(req: Request):
    env = req.scope["env"]
    runtime = await selector_d1.load_runtime(env.DB)
    result = await env.DB.prepare(
        "SELECT lesson_seq, unit_id AS unit_seq, unit_name, lesson_name, "
        "       COALESCE(lesson_title, '') AS lesson_title "
        "FROM lessons ORDER BY lesson_seq"
    ).run()
    return [
        _lesson_row(row, runtime) for row in _rows_of(result)
        if row["lesson_seq"] != runtime["cold_start_lesson"]
    ]


@app.get("/api/generate_daily/{lesson_seq}")
async def generate_daily(lesson_seq: int, req: Request, mode: str = "daily"):
    """生成词表。梯队算法见 selector_d1.py 与设计文档 §5。

    目标词数、复习课和多音字数量全部来自部署的内容包。
    """
    env = req.scope["env"]
    runtime = await selector_d1.load_runtime(env.DB)
    if mode not in ("daily", "unit"):
        raise HTTPException(status_code=400, detail="mode 必须是 daily 或 unit")
    if (mode == "unit") != selector_d1.is_review_lesson(lesson_seq, runtime):
        raise HTTPException(status_code=400, detail="mode 与课程类型不匹配")
    exists = _rows_of(
        await env.DB.prepare(
            "SELECT 1 AS found FROM lessons WHERE lesson_seq = ?"
        ).bind(lesson_seq).run()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="课程不存在")
    words, poly = await selector_d1.build_word_list(
        env.DB, lesson_seq, user_id=USER_ID, runtime=runtime
    )

    data = [{
        "id": w["id"], "target": w["target"], "pinyin": w["pinyin"],
        "word_type": w["word_type"], "category": w["category"],
        "audio_url": audio_url_for(w["target"]),
    } for w in words]
    for p in poly:
        p["audio_url"] = audio_url_for(p["character"])

    return {"data": data, "polyphonic_section": poly}


@app.post("/api/submit_dictation")
async def submit_dictation(payload: SubmitPayload, req: Request):
    if not payload.results:
        raise HTTPException(status_code=400, detail="results 不能为空")
    env     = req.scope["env"]
    today   = datetime.now().strftime("%Y-%m-%d")
    correct = sum(1 for r in payload.results if r.is_correct)
    score   = round(correct / len(payload.results) * 100, 2)

    # 1. 写入听写历史，取得 history_id
    hist = await env.DB.prepare(
        "INSERT INTO dictation_history "
        "(user_id, dictation_type, scope_id, score, poly_ids) "
        "VALUES (?, ?, ?, ?, ?)"
    ).bind(payload.user_id, payload.dictation_type, payload.scope_id, score,
           ",".join(str(i) for i in payload.poly_ids)).run()
    history_id = hist.meta.last_row_id

    # 2. 批量读取已有的记忆行，减少后续网络往返
    kp_ids = [r.kp_id for r in payload.results]
    ph     = ",".join("?" * len(kp_ids))
    mem_rows = _rows_of(await env.DB.prepare(
        f"SELECT kp_id, id, error_count, correct_streak FROM user_memory "
        f"WHERE user_id = ? AND kp_id IN ({ph})"
    ).bind(payload.user_id, *kp_ids).run())
    mem_map = {r["kp_id"]: r for r in mem_rows}

    # 3. 构建 batch：dictation_items + user_memory upsert
    stmts = []
    for r in payload.results:
        stmts.append(env.DB.prepare(
            "INSERT INTO dictation_items (history_id, kp_id, is_correct) VALUES (?,?,?)"
        ).bind(history_id, r.kp_id, 1 if r.is_correct else 0))

        mem  = mem_map.get(r.kp_id)
        if r.is_correct:
            streak     = (mem["correct_streak"] if mem else 0) + 1
            status     = 1 if streak >= 3 else 0
            err_count  = mem["error_count"] if mem else 0
            review_day = min(2 ** streak, 30)
        else:
            streak     = 0
            status     = 0
            err_count  = (mem["error_count"] if mem else 0) + 1
            review_day = 1

        stmts.append(env.DB.prepare(
            "INSERT INTO user_memory "
            "(user_id,kp_id,status,error_count,correct_streak,last_tested_date,next_review_date) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,kp_id) DO UPDATE SET "
            "  status=excluded.status, error_count=excluded.error_count, "
            "  correct_streak=excluded.correct_streak, "
            "  last_tested_date=excluded.last_tested_date, "
            "  next_review_date=excluded.next_review_date"
        ).bind(payload.user_id, r.kp_id, status, err_count, streak,
               today, _next_review(review_day)))

    await env.DB.batch(stmts)
    return {"status": "success", "score": score,
            "correct": correct, "total": len(payload.results)}


@app.get("/api/dictation_history")
async def get_dictation_history(start_date: str, end_date: str, req: Request):
    env = req.scope["env"]
    rows = _rows_of(await env.DB.prepare(
        "SELECT date(created_at) AS d, MAX(score) AS s "
        "FROM dictation_history "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "GROUP BY date(created_at)"
    ).bind(USER_ID, start_date, end_date).run())
    return {r["d"]: r["s"] for r in rows}
