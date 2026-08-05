#!/usr/bin/env python3
"""预热 V1 的 TTS 磁盘缓存 —— 避免出题时被有道限流。

背景
----
V1 是「运行时实时合成」：每次听写要连续调 40+ 次有道 TTS（30 词 + 开场
+ 收尾 + 每组组号），背靠背发很容易被限流，一次失败整个请求就 500。
本脚本提前把所有可能用到的音频灌进 V1 的缓存目录，之后 V1 出题全部命中
缓存、瞬间完成，也不会再触发限流。

两套缓存命名不同但内容相同
--------------------------
  V2 切片   shared/web/audio/w/{md5(词)[:12]}.mp3          内容是「词。」
  V1 缓存   v1/tts_cache/{md5(voice_speed_词。)}.mp3        内容是「词。」

gen_slices.py 合成时用的是 f"{词}。"，V1 请求的也是 f"{词}。"，voice/speed
默认值一致 —— 所以音频字节相同。因此：

  * V2 切片已存在 → 直接复制，零 API 调用
  * 缺失的部分   → 走 TTS，带限流与重试

用法
----
  # 自动（优先复制切片，缺的才合成）
  v1/venv/bin/python shared/tools/warm_v1_cache.py

  # 只看计划、不动文件
  v1/venv/bin/python shared/tools/warm_v1_cache.py --dry-run

  # 有道限流较严时放慢
  v1/venv/bin/python shared/tools/warm_v1_cache.py --interval 0.5

  # 强制全部走 TTS（不复制切片）
  v1/venv/bin/python shared/tools/warm_v1_cache.py --no-copy

密钥从环境变量读，与 V1 一致：
  set -a && . /etc/dictation/v1.env && set +a
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

KP_JSON     = os.path.join(ROOT, "shared", "data", "kp_grade3.json")
SLICE_W_DIR = os.path.join(ROOT, "shared", "web", "audio", "w")
SLICE_S_DIR = os.path.join(ROOT, "shared", "web", "audio", "sys")

YOUDAO_URL = "https://openapi.youdao.com/ttsapi"
APP_KEY    = os.getenv("YOUDAO_APP_KEY", "")
APP_SECRET = os.getenv("YOUDAO_APP_SECRET", "")
VOICE      = os.getenv("YOUDAO_VOICE", "youxiaoxun")
SPEED      = os.getenv("YOUDAO_SPEED", "0.6")

# 与 v1/main.py 完全一致的文案，改这里必须同步改那边
INTRO = "准备听写。每三个词为一组，报三遍。"
OUTRO = "听写完毕，请检查后交卷。"


def v1_cache_name(text: str) -> str:
    """V1 的缓存文件名：md5(voice_speed_text) 全长 hex。"""
    key = f"{VOICE}_{SPEED}_{text}"
    return hashlib.md5(key.encode("utf-8")).hexdigest() + ".mp3"


def v2_slice_name(bare_word: str) -> str:
    """V2 的切片文件名：md5(裸词)[:12]。"""
    return hashlib.md5(bare_word.encode("utf-8")).hexdigest()[:12] + ".mp3"


def _truncate(q: str) -> str:
    """有道 v3 签名的 input 截断规则。"""
    if not q:
        return ""
    n = len(q)
    return q if n <= 20 else q[:10] + str(n) + q[-10:]


def fetch_tts(text: str, dest: str, interval: float, max_retry: int = 3) -> bool:
    """调有道合成并写入 dest。失败重试，退避递增。"""
    import requests

    last_err = None
    for attempt in range(1, max_retry + 1):
        salt    = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign    = hashlib.sha256(
            (APP_KEY + _truncate(text) + salt + curtime + APP_SECRET).encode("utf-8")
        ).hexdigest()
        try:
            r = requests.post(
                YOUDAO_URL,
                data={
                    "q": text, "appKey": APP_KEY, "salt": salt, "sign": sign,
                    "signType": "v3", "curtime": curtime, "format": "mp3",
                    "voiceName": VOICE, "speed": SPEED,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            if "audio" in r.headers.get("Content-Type", ""):
                with open(dest, "wb") as f:
                    f.write(r.content)
                return True
            last_err = r.text[:120]
        except Exception as e:  # 网络异常也重试
            last_err = str(e)

        if attempt < max_retry:
            time.sleep(interval * (2 ** attempt))

    print(f"    [FAIL] {text[:20]} -> {last_err}")
    return False


def load_words():
    """从 kp_grade3.json 取所有可能被听写的词面。

    生字有两个候选组词（出题时随机取一个），两个都要预热。
    多音字段落 V1 不合成，跳过。
    """
    if not os.path.exists(KP_JSON):
        sys.exit(f"找不到词库: {KP_JSON}\n请先运行 shared/tools/convert_wordlist.py")
    with open(KP_JSON, encoding="utf-8") as f:
        kps = json.load(f)

    words = set()
    for kp in kps:
        if kp.get("category") == "多音字":
            continue
        for opt in kp.get("options_json") or []:
            t = (opt.get("text") or "").strip()
            if t:
                words.add(t)
    return sorted(words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=os.getenv(
        "AUDIO_CACHE_DIR", os.path.join(ROOT, "v1", "tts_cache")))
    ap.add_argument("--interval", type=float, default=0.25,
                    help="两次真实 TTS 请求之间的最小间隔秒数")
    ap.add_argument("--max-groups", type=int, default=20,
                    help="预热到第 N 组的组号提示音（50 词 ÷ 3 = 17 组）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-copy", action="store_true",
                    help="不复用 V2 切片，全部走 TTS")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    words     = load_words()

    # V1 会请求的全部字符串 → (请求文本, 可复用的 V2 切片路径 or None)
    tasks = []
    tasks.append((INTRO, os.path.join(SLICE_S_DIR, "intro.mp3")))
    tasks.append((OUTRO, os.path.join(SLICE_S_DIR, "outro.mp3")))
    for n in range(1, args.max_groups + 1):
        tasks.append((f"第{n}组。", os.path.join(SLICE_S_DIR, f"g{n}.mp3")))
    for w in words:
        tasks.append((f"{w}。", os.path.join(SLICE_W_DIR, v2_slice_name(w))))

    print(f"缓存目录 : {cache_dir}")
    print(f"声音/语速: {VOICE} / {SPEED}")
    print(f"待预热   : {len(tasks)} 条（词条 {len(words)} + 系统音 {len(tasks)-len(words)}）")

    if not args.dry_run:
        os.makedirs(cache_dir, exist_ok=True)

    n_have = n_copy = n_tts = n_fail = 0
    need_tts = []

    for text, slice_path in tasks:
        dest = os.path.join(cache_dir, v1_cache_name(text))
        if os.path.exists(dest):
            n_have += 1
            continue
        if (not args.no_copy) and slice_path and os.path.exists(slice_path):
            if args.dry_run:
                n_copy += 1
            else:
                shutil.copyfile(slice_path, dest)
                n_copy += 1
            continue
        need_tts.append((text, dest))

    print(f"\n已在缓存 : {n_have}")
    print(f"可直接复制: {n_copy}   (来自 V2 切片，零 API 调用)")
    print(f"需要合成 : {len(need_tts)}")

    if args.dry_run:
        for t, _ in need_tts[:15]:
            print(f"    TTS: {t}")
        if len(need_tts) > 15:
            print(f"    ... 另 {len(need_tts)-15} 条")
        print("\n(dry-run，未改动任何文件)")
        return

    if need_tts:
        if not APP_KEY or APP_KEY.startswith("REPLACE_"):
            print("\n[!] 未设置 YOUDAO_APP_KEY，无法合成缺失部分。")
            print("    set -a && . /etc/dictation/v1.env && set +a")
            sys.exit(1)

        print(f"\n开始合成（间隔 {args.interval}s，可 Ctrl-C 中断后重跑）…")
        t_prev = 0.0
        for i, (text, dest) in enumerate(need_tts, 1):
            wait = args.interval - (time.time() - t_prev)
            if wait > 0:
                time.sleep(wait)
            ok = fetch_tts(text, dest, args.interval)
            t_prev = time.time()
            if ok:
                n_tts += 1
            else:
                n_fail += 1
            if i % 25 == 0 or i == len(need_tts):
                print(f"  {i}/{len(need_tts)}  成功 {n_tts} 失败 {n_fail}")

    total = len([f for f in os.listdir(cache_dir) if f.endswith(".mp3")])
    print(f"\n完成: 复制 {n_copy}, 合成 {n_tts}, 失败 {n_fail}")
    print(f"缓存内现有 {total} 个文件")
    if n_fail:
        print("有失败项 —— 直接重跑本脚本即可续传（已成功的会跳过）")
    else:
        print("V1 出题现在应当全部命中缓存，不再调用 TTS")


if __name__ == "__main__":
    main()
