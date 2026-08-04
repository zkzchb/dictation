"""听写小助手 API —— 生产级单用户版本。

合并了两代实现的优点：
  * 真实音频引擎（有道 TTS 合成 + 磁盘缓存 + 逐词时间轴），来自新版；
  * 服务端算分 + 间隔重复记忆引擎（连对晋级/错词降级/复习排期），来自旧版备份。

所有路径与密钥均可用环境变量覆盖，默认值为本机可直接运行的相对路径，
因此在 Windows 本地和 Linux 服务器上都能启动。
"""
import os
import json
import time
import uuid
import hashlib
import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random

# 共用选词引擎与播放参数（shared/）
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
import selector  # noqa: E402

PLAYBACK_CFG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "shared", "web", "playback_config.json")
PLAYBACK_DEFAULTS = {
    "group_size": 3, "repeat_times": 3,
    "gap_intro_ms": 2000, "gap_group_head_ms": 1000,
    "gap_between_words_ms": 1000, "gap_between_groups_ms": 2000,
    "base_gap_ms": [6500, 6000, 2500],
    "per_char_gap_ms": [1500, 1000, 300],
    "max_write_gap_ms": 14000,
    "gap_polyphonic_ms": 8000, "playback_rate": 1.0,
}

def load_playback_cfg():
    cfg = dict(PLAYBACK_DEFAULTS)
    try:
        with open(PLAYBACK_CFG_PATH, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if k in cfg:
                    cfg[k] = v
    except Exception:
        pass
    return cfg

def write_gap_ms(cfg, pass_idx, text):
    """书写停顿按字数缩放: min(base + (字数-1)*perChar, max)"""
    chars = max(1, len(text or ""))
    base = cfg["base_gap_ms"][pass_idx]
    per = cfg["per_char_gap_ms"][pass_idx]
    return min(base + (chars - 1) * per, cfg["max_write_gap_ms"])

# ================= ⚙️ 配置（环境变量优先，默认值本机可跑）=================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DICTATION_DB", os.path.join(BASE_DIR, "dictation.db"))
AUDIO_OUTPUT_DIR = os.getenv("AUDIO_OUTPUT_DIR", os.path.join(BASE_DIR, "audio"))
CACHE_DIR = os.getenv("AUDIO_CACHE_DIR", os.path.join(AUDIO_OUTPUT_DIR, "cache"))

# ⚠️ 这些密钥曾以明文提交，应视为已泄露并尽快在有道控制台轮换。
# 轮换后建议改为仅从环境变量读取（删掉下面的默认值）。
YOUDAO_APP_KEY = os.getenv("YOUDAO_APP_KEY", "REPLACE_WITH_YOUDAO_APP_KEY")
YOUDAO_APP_SECRET = os.getenv("YOUDAO_APP_SECRET", "REPLACE_WITH_YOUDAO_APP_SECRET")
YOUDAO_URL = "https://openapi.youdao.com/ttsapi"
YOUDAO_VOICE = os.getenv("YOUDAO_VOICE", "youxiaoxun")
YOUDAO_SPEED = os.getenv("YOUDAO_SPEED", "0.6")

USER_ID = 1              # 单用户 MVP，固定用户
DAILY_TARGET = 30        # 每日词表目标数量
EXCLUDE_CATEGORY = "易混淆字"  # 该类只有选字题、无拼音可听写，一律排除

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="听写小助手 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ================= 📦 数据模型 =================
class WordItem(BaseModel):
    text: str
    pinyin: str = ""


class WordResult(BaseModel):
    kp_id: int
    is_correct: bool


class SubmitPayload(BaseModel):
    dictation_type: str
    scope_id: int
    results: List[WordResult]
    user_id: int = USER_ID


# ================= 🛠️ 辅助函数 =================
def extract_word_info(target, options_json):
    """从 options_json 中提取可听写的组词与拼音。

    题库里 target 常常是单个生字（如"诗"），真正要听写的是它的组词
    （如"诗人"）。这里取第一个组词的 text/pinyin；解析失败则回退到 target。
    """
    word_text, word_pinyin = target, ""
    try:
        opts = json.loads(options_json)
        if isinstance(opts, str):
            opts = json.loads(opts)
        if isinstance(opts, list) and opts and isinstance(opts[0], dict):
            word_text = opts[0].get("text") or target
            word_pinyin = opts[0].get("pinyin", "")
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return word_text, word_pinyin


def _next_review(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


# ================= 📡 出题接口 =================
@app.get("/api/lessons")
def get_lessons():
    """课程目录（供前端下拉菜单）。lesson_seq >= 3100 为正式课。"""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT lesson_seq, unit_id AS unit_seq, unit_name, lesson_name
            FROM lessons WHERE lesson_seq >= 3100 ORDER BY lesson_seq ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _select_opt(row):
    """生字随机取组词，其他类型取第一个。"""
    opts_json = row["options_json"]
    if row["category"] == "生字":
        try:
            opts = json.loads(opts_json)
            if isinstance(opts, str):
                opts = json.loads(opts)
            valid = [o for o in opts if isinstance(o, dict) and o.get("text")]
            if valid:
                return random.choice(valid)
        except Exception:
            pass
    text, pinyin = extract_word_info(row["target"], opts_json)
    return {"text": text, "pinyin": pinyin}


@app.get("/api/generate_daily/{lesson_seq}")
def generate_daily(lesson_seq: int, mode: str = "daily"):
    """生成词表（梯队算法见 shared/selector.py 与设计文档 §5）。

    V1 不返回 audio_url —— 音频由 /api/generate_audio 实时合成。
    """
    conn = get_db()
    try:
        words, poly = selector.build_word_list(conn, lesson_seq, user_id=USER_ID)
    finally:
        conn.close()

    data = [{
        "id": w["id"], "target": w["target"],
        "pinyin": w["pinyin"], "word_type": w["word_type"],
    } for w in words]
    return {"data": data, "polyphonic_section": poly}


# ================= 📝 提交批改 + 间隔重复引擎 =================
@app.post("/api/submit_dictation")
def submit_dictation(payload: SubmitPayload):
    """服务端算分并更新记忆库。

    分数 = 正确数 / 总数 * 100，由后端计算并写入 dictation_history —— 前端
    只上报每题对错，无法伪造分数。记忆库按间隔重复更新：
      * 答对：correct_streak+1，连对满 3 次判定为"已掌握"(status=1)，
              下次复习日按 2^streak 天指数后延；
      * 答错：error_count+1，streak 归零，status 回到 0，明天就要复习。
    整个提交在单个事务内完成，出错则整体回滚。
    """
    if not payload.results:
        raise HTTPException(status_code=400, detail="results 不能为空")

    conn = get_db()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    correct = sum(1 for r in payload.results if r.is_correct)
    score = round(correct / len(payload.results) * 100, 2)

    try:
        cursor.execute(
            "INSERT INTO dictation_history (user_id, dictation_type, scope_id, score)"
            " VALUES (?, ?, ?, ?)",
            (payload.user_id, payload.dictation_type, payload.scope_id, score),
        )
        history_id = cursor.lastrowid

        for r in payload.results:
            cursor.execute(
                "INSERT INTO dictation_items (history_id, kp_id, is_correct) VALUES (?, ?, ?)",
                (history_id, r.kp_id, 1 if r.is_correct else 0),
            )
            mem = cursor.execute(
                "SELECT id, error_count, correct_streak FROM user_memory"
                " WHERE user_id = ? AND kp_id = ?",
                (payload.user_id, r.kp_id),
            ).fetchone()

            if r.is_correct:
                if mem:
                    streak = mem["correct_streak"] + 1
                    cursor.execute(
                        "UPDATE user_memory SET status=?, correct_streak=?,"
                        " last_tested_date=?, next_review_date=? WHERE id=?",
                        (1 if streak >= 3 else 0, streak, today,
                         _next_review(2 ** streak), mem["id"]),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO user_memory (user_id, kp_id, status, error_count,"
                        " correct_streak, last_tested_date, next_review_date)"
                        " VALUES (?, ?, 0, 0, 1, ?, ?)",
                        (payload.user_id, r.kp_id, today, _next_review(2)),
                    )
            else:
                if mem:
                    cursor.execute(
                        "UPDATE user_memory SET status=0, error_count=?, correct_streak=0,"
                        " last_tested_date=?, next_review_date=? WHERE id=?",
                        (mem["error_count"] + 1, today, _next_review(1), mem["id"]),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO user_memory (user_id, kp_id, status, error_count,"
                        " correct_streak, last_tested_date, next_review_date)"
                        " VALUES (?, ?, 0, 1, 0, ?, ?)",
                        (payload.user_id, r.kp_id, today, _next_review(1)),
                    )

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

    return {"status": "success", "score": score,
            "correct": correct, "total": len(payload.results)}


@app.get("/api/dictation_history")
def get_dictation_history(start_date: str, end_date: str):
    """打卡日历数据：区间内每天取最高分。"""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT date(created_at) AS d, MAX(score) AS s
            FROM dictation_history
            WHERE user_id = ? AND date(created_at) BETWEEN ? AND ?
            GROUP BY date(created_at)
        """, (USER_ID, start_date, end_date)).fetchall()
        return {r["d"]: r["s"] for r in rows}
    finally:
        conn.close()


# ================= 🎙️ TTS 语音引擎 =================
# pydub 依赖 ffmpeg，且只有音频接口用得到。故在函数内部延迟 import，
# 这样即便未装 ffmpeg/pydub，出题与算分接口仍可正常工作。


def _truncate_text(q: str) -> str:
    """有道 v3 签名要求的 input 截断规则。"""
    if not q:
        return ""
    size = len(q)
    return q if size <= 20 else q[:10] + str(size) + q[-10:]


def get_audio_segment(text: str):
    """合成单段语音，按内容 MD5 缓存到磁盘，避免重复调用 TTS。"""
    from pydub import AudioSegment
    cache_key = f"{YOUDAO_VOICE}_{YOUDAO_SPEED}_{text}"
    md5_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
    filepath = os.path.join(CACHE_DIR, f"{md5_hash}.mp3")

    if not os.path.exists(filepath):
        salt = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign_str = (YOUDAO_APP_KEY + _truncate_text(text) + salt
                    + curtime + YOUDAO_APP_SECRET)
        sign = hashlib.sha256(sign_str.encode("utf-8")).hexdigest()
        data = {
            "q": text, "appKey": YOUDAO_APP_KEY, "salt": salt, "sign": sign,
            "signType": "v3", "curtime": curtime, "format": "mp3",
            "voiceName": YOUDAO_VOICE, "speed": YOUDAO_SPEED,
        }
        resp = requests.post(
            YOUDAO_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if "audio" not in resp.headers.get("Content-Type", ""):
            raise RuntimeError(f"有道 TTS 返回错误: {resp.text[:200]}")
        with open(filepath, "wb") as f:
            f.write(resp.content)
    return AudioSegment.from_mp3(filepath)


def build_dictation_audio(word_list: List[WordItem]):
    """按 playback_config.json 拼成完整听写音频，返回 (文件名, 时间轴)。

    停顿规则与 V2/V3 前端一致：三遍分工（写汉字/写拼音/检查），
    每遍的书写停顿按词的字数线性缩放。
    """
    from pydub import AudioSegment
    cfg = load_playback_cfg()
    gs, rt = cfg["group_size"], cfg["repeat_times"]

    final_audio = AudioSegment.empty()
    timeline, t_ms = [], 0

    intro = get_audio_segment("准备听写。每三个词为一组，报三遍。")
    final_audio += intro + AudioSegment.silent(duration=cfg["gap_intro_ms"])
    t_ms += len(intro) + cfg["gap_intro_ms"]

    for i in range(0, len(word_list), gs):
        group = word_list[i:i + gs]
        head = get_audio_segment(f"第{i // gs + 1}组。")
        final_audio += head + AudioSegment.silent(duration=cfg["gap_group_head_ms"])
        t_ms += len(head) + cfg["gap_group_head_ms"]

        for pass_idx in range(rt):
            longest = max((len(w.text or "") for w in group), default=1)
            for k, w in enumerate(group):
                start = t_ms
                seg = get_audio_segment(f"{w.text}。")
                is_last = (k == len(group) - 1)
                gap = (write_gap_ms(cfg, pass_idx, "x" * longest)
                       if is_last else cfg["gap_between_words_ms"])
                final_audio += seg + AudioSegment.silent(duration=gap)
                t_ms += len(seg) + gap
                timeline.append({
                    "text": w.text, "pinyin": w.pinyin,
                    "start": start / 1000.0, "end": t_ms / 1000.0,
                })
        final_audio += AudioSegment.silent(duration=cfg["gap_between_groups_ms"])
        t_ms += cfg["gap_between_groups_ms"]

    final_audio += get_audio_segment("听写完毕，请检查后交卷。")
    filename = f"dictation_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.mp3"
    final_audio.export(os.path.join(AUDIO_OUTPUT_DIR, filename),
                       format="mp3", bitrate="64k")
    return filename, timeline


@app.post("/api/generate_audio")
async def api_generate_audio(word_list: List[WordItem]):
    if not word_list:
        raise HTTPException(status_code=400, detail="word_list 不能为空")
    try:
        filename, timeline = await asyncio.to_thread(build_dictation_audio, word_list)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音生成失败: {e}")
    return {"audio_url": f"/audio/{filename}", "timeline": timeline}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)


