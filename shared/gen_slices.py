"""shared/gen_slices.py — 预生成所有听写切片（本地运行一次，可重复）

调用有道 TTS 为每个需要的音频生成 MP3，增量更新：已存在的文件直接跳过，
中断后重跑会接着做。

用法：
  set -a && . /etc/dictation/v1.env && set +a
  v1/venv/bin/python shared/gen_slices.py

  放慢速度（被限流时）：
  TTS_INTERVAL=2.0 v1/venv/bin/python shared/gen_slices.py

输出：
  web/audio/sys/{key}.mp3   系统提示音（开场 / 第N组 / 多音字引导 / 收尾）
  web/audio/w/{hash}.mp3    词条与多音字单字，按 MD5(文本)[:12] 命名
  manifest.json             text → {url, pinyin, hash}

有道 errorCode 411 = 访问频率受限。默认间隔 1.0 秒（约 1 QPS）；
如仍被限流，用 TTS_INTERVAL 调大。
"""
import json, os, hashlib, time, uuid
# requests 延迟到真正发请求时才 import：这样 --dry-run 在没装 requests
# 的环境里也能跑（只做统计，不联网）。

HERE      = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(HERE, "web", "audio")
SYS_DIR   = os.path.join(AUDIO_DIR, "sys")
WORD_DIR  = os.path.join(AUDIO_DIR, "w")
KP_FILES  = [os.path.join(HERE, "data", "kp_grade3.json")]

APP_KEY    = os.getenv("YOUDAO_APP_KEY",    "REPLACE_WITH_YOUDAO_APP_KEY")
APP_SECRET = os.getenv("YOUDAO_APP_SECRET", "REPLACE_WITH_YOUDAO_APP_SECRET")
YOUDAO_URL = "https://openapi.youdao.com/ttsapi"
VOICE      = os.getenv("YOUDAO_VOICE", "youxiaoxun")
SPEED      = os.getenv("YOUDAO_SPEED", "0.6")

# 限流：每次真实请求之间至少间隔这么久（秒）。命中缓存不计。
TTS_INTERVAL = float(os.getenv("TTS_INTERVAL", "1.0"))
TTS_RETRY    = int(os.getenv("TTS_RETRY", "3"))

# 复习课 50 词 ÷ 每组 3 = 17 组，取 20 留余量
MAX_GROUPS  = int(os.getenv("MAX_GROUPS", "20"))
SYS_PHRASES = {
    "intro":      "准备听写。每三个词为一组，报三遍。",
    "outro":      "听写完毕，请检查后交卷。",
    # 多音字改为每字一句，由三段拼起来：poly_prefix + 老师录的单字 + poly_suffix，
    # 听感是「多音字：尽（老师的原声），请组词并默写，标注拼音。」
    # 老师只读本课那一个音，题目也只针对这个音组词。
    "poly_prefix": "多音字：",
    "poly_suffix": "，请组词并默写，标注拼音。",
    # 旧的整段引导保留合成，避免尚未更新的前端拿到 404；新前端不再播。
    "poly_intro": "下面是多音字。请写出不同读音的组词，并标上拼音。",
    **{f"g{n}": f"第{n}组。" for n in range(1, MAX_GROUPS + 1)},
}

_last_call = 0.0


def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _truncate(q: str) -> str:
    s = len(q)
    return q if s <= 20 else q[:10] + str(s) + q[-10:]


def fetch_tts(text: str, dest: str) -> bool:
    """合成 text 存到 dest。已存在直接返回 True。

    带全局限流与退避重试 —— 关键点：无论成功失败都要限速，
    否则一旦被限流就会全速空转、连续失败。
    """
    global _last_call
    if os.path.exists(dest):
        return True

    import requests  # 延迟导入：--dry-run 时无需安装

    for attempt in range(1, TTS_RETRY + 1):
        wait = TTS_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)

        salt    = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign    = hashlib.sha256(
            (APP_KEY + _truncate(text) + salt + curtime + APP_SECRET).encode()
        ).hexdigest()
        data = {
            "q": text, "appKey": APP_KEY, "salt": salt, "sign": sign,
            "signType": "v3", "curtime": curtime, "format": "mp3",
            "voiceName": VOICE, "speed": SPEED,
        }
        try:
            r = requests.post(
                YOUDAO_URL, data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            _last_call = time.time()

            if "audio" in r.headers.get("Content-Type", ""):
                with open(dest, "wb") as f:
                    f.write(r.content)
                return True

            body = r.text[:120]
            # 411 = 频率受限，退避后重试；其他错误码重试无意义
            if '"411"' in body and attempt < TTS_RETRY:
                backoff = TTS_INTERVAL * (2 ** attempt)
                print(f"  ! 限流[{text}] 退避 {backoff:.1f}s 后重试 ({attempt}/{TTS_RETRY})")
                time.sleep(backoff)
                continue
            print(f"  x 失败[{text}]: {body}")
            return False
        except Exception as e:
            _last_call = time.time()
            if attempt < TTS_RETRY:
                time.sleep(TTS_INTERVAL * (2 ** attempt))
                continue
            print(f"  x 网络错误[{text}]: {e}")
            return False
    return False


def collect_targets(kp_files):
    """汇总所有需要合成的音频。

    返回 (word_items, manifest)：
      word_items = [(要合成的文本, 目标路径)]
      manifest   = {词面: {url, pinyin, hash}}

    三点容易漏的：
      1. 生字有 word1/word2 两个候选，选词时随机取一个，两个都得有切片；
      2. 多音字要的是「单字」本身的音频（前端播 md5(单字)），不是它的组词；
      3. 易错字（tw1/tw2）都是要听写的，不能跳过。
    """
    texts    = {}   # 词面 → 拼音
    manifest = {}
    for path in kp_files:
        if not os.path.exists(path):
            print(f"  ! 找不到题库: {path}")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for kp in data:
            cat  = kp.get("category", "")
            opts = kp.get("options_json") or []
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except Exception:
                    opts = []

            if cat == "多音字":
                # 前端播的是单字，组词只是给家长看的参考
                t = (kp.get("target") or "").strip()
                if t:
                    texts.setdefault(t, "")
                continue

            # 生字/词语/易错字：所有候选组词都要有切片
            for opt in opts:
                if not isinstance(opt, dict):
                    continue
                t = (opt.get("text") or "").strip()
                if t:
                    texts.setdefault(t, opt.get("pinyin", "") or "")
            if not opts:
                t = (kp.get("target") or "").strip()
                if t:
                    texts.setdefault(t, "")

    items = []
    for t, py in sorted(texts.items()):
        h = word_hash(t)
        items.append((f"{t}。", os.path.join(WORD_DIR, f"{h}.mp3")))
        manifest[t] = {"url": f"/audio/w/{h}.mp3", "pinyin": py, "hash": h}
    return items, manifest


def main():
    os.makedirs(SYS_DIR, exist_ok=True)
    os.makedirs(WORD_DIR, exist_ok=True)

    if APP_KEY.startswith("REPLACE_WITH"):
        print("!! 未设置 YOUDAO_APP_KEY / YOUDAO_APP_SECRET")
        print("   set -a && . /etc/dictation/v1.env && set +a")
        return

    sys_items = [(p, os.path.join(SYS_DIR, f"{k}.mp3"))
                 for k, p in SYS_PHRASES.items()]
    word_items, manifest = collect_targets(KP_FILES)
    todo = sys_items + word_items

    have = sum(1 for _, d in todo if os.path.exists(d))
    print(f"总需 {len(todo)} 个音频（系统音 {len(sys_items)} + 词条 {len(word_items)}）")
    print(f"已存在 {have}，待合成 {len(todo) - have}，间隔 {TTS_INTERVAL}s")
    if len(todo) - have > 0:
        est = (len(todo) - have) * TTS_INTERVAL / 60
        print(f"预计约 {est:.1f} 分钟\n")

    ok = fail = skip = 0
    for i, (text, dest) in enumerate(todo, 1):
        if os.path.exists(dest):
            skip += 1
        elif fetch_tts(text, dest):
            ok += 1
        else:
            fail += 1
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}: 新增 {ok} 跳过 {skip} 失败 {fail}")

    print(f"\n完成: 新增 {ok}，跳过 {skip}，失败 {fail}")
    with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"清单已写入 {os.path.join(HERE, 'manifest.json')}")

    if fail:
        print(f"\n有 {fail} 个失败。若是 411 限流，调大间隔后重跑（已成功的会跳过）：")
        print("  TTS_INTERVAL=2.0 v1/venv/bin/python shared/gen_slices.py")
    else:
        print("\n下一步：预热 V1 缓存（直接复用切片，几乎不调 API）")
        print("  v1/venv/bin/python shared/tools/warm_v1_cache.py")


if __name__ == "__main__":
    main()
