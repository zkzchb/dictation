"""shared/gen_slices.py — 预生成所有听写切片（本地运行一次）

首次运行调用有道 TTS 为每个词条生成 MP3 切片，后续重跑跳过已存在文件（增量更新）。

用法：
  cd shared/
  pip install requests
  python gen_slices.py

  或使用环境变量覆盖密钥：
  YOUDAO_APP_KEY=xxx YOUDAO_APP_SECRET=yyy python gen_slices.py

输出：
  audio/sys/{key}.mp3   — 系统提示音（开场/第N组/收尾）
  audio/w/{hash}.mp3    — 词条切片，按文本 MD5[:12] 命名
  manifest.json         — text → {url, pinyin, hash} 映射，供调试

部署前把 audio/ 分发到各版本：
  cp -r audio/ ../v2/audio/
  cp -r audio/ ../v3/public/audio/
"""
import json, os, hashlib, time, uuid
import requests

HERE      = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(HERE, "web", "audio")
SYS_DIR   = os.path.join(AUDIO_DIR, "sys")
WORD_DIR  = os.path.join(AUDIO_DIR, "w")
KP_FILES  = [os.path.join(HERE, "data", f"kp_part{i}.json") for i in range(4)]

APP_KEY    = os.getenv("YOUDAO_APP_KEY",    "REPLACE_WITH_YOUDAO_APP_KEY")
APP_SECRET = os.getenv("YOUDAO_APP_SECRET", "REPLACE_WITH_YOUDAO_APP_SECRET")
YOUDAO_URL = "https://openapi.youdao.com/ttsapi"
VOICE      = os.getenv("YOUDAO_VOICE", "youxiaoxun")
SPEED      = os.getenv("YOUDAO_SPEED", "0.6")

MAX_GROUPS  = 12   # 30词÷3最多10组，留余量
SYS_PHRASES = {
    "intro": "准备听写。每三个词为一组，报三遍。",
    "outro": "听写完毕，请检查后交卷。",
    **{f"g{n}": f"第{n}组。" for n in range(1, MAX_GROUPS + 1)},
}

def word_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

def _truncate(q: str) -> str:
    s = len(q)
    return q if s <= 20 else q[:10] + str(s) + q[-10:]

def fetch_tts(text: str, dest: str) -> bool:
    """下载 TTS 音频到 dest；已存在则直接返回 True（跳过）。"""
    if os.path.exists(dest):
        return True
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
        r = requests.post(YOUDAO_URL, data=data,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          timeout=30)
        if "audio" in r.headers.get("Content-Type", ""):
            with open(dest, "wb") as f:
                f.write(r.content)
            return True
        print(f"  ✗ TTS 失败 [{text}]: {r.text[:100]}")
    except Exception as e:
        print(f"  ✗ 网络错误 [{text}]: {e}")
    return False

def extract_words(kp_files):
    """从题库 JSON 提取所有可听写词条（排除易混淆字，按文本去重）。"""
    words = {}   # text → pinyin
    for path in kp_files:
        if not os.path.exists(path):
            print(f"  ⚠ 找不到题库: {path}")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for kp in data:
            if kp.get("category") == "易混淆字":
                continue
            text, pinyin = kp["target"], ""
            try:
                opts = kp["options_json"]
                if isinstance(opts, str): opts = json.loads(opts)
                if isinstance(opts, list) and opts and isinstance(opts[0], dict):
                    text   = opts[0].get("text") or kp["target"]
                    pinyin = opts[0].get("pinyin", "")
            except Exception:
                pass
            if text not in words:
                words[text] = pinyin
    return words

def main():
    os.makedirs(SYS_DIR, exist_ok=True)
    os.makedirs(WORD_DIR, exist_ok=True)

    print("=== 1/2 系统提示音 ===")
    for key, phrase in SYS_PHRASES.items():
        dest = os.path.join(SYS_DIR, f"{key}.mp3")
        if os.path.exists(dest):
            print(f"  {key}: 已存在，跳过")
        else:
            ok = fetch_tts(phrase, dest)
            print(f"  {key}: {'✓' if ok else '✗'}")
            if ok: time.sleep(0.15)

    print("\n=== 2/2 词条切片 ===")
    words = extract_words(KP_FILES)
    print(f"共 {len(words)} 个唯一词条…")

    manifest, ok, fail, skip = {}, 0, 0, 0
    for i, (text, pinyin) in enumerate(words.items()):
        h    = word_hash(text)
        dest = os.path.join(WORD_DIR, f"{h}.mp3")
        url  = f"/audio/w/{h}.mp3"
        manifest[text] = {"url": url, "pinyin": pinyin, "hash": h}
        if os.path.exists(dest):
            skip += 1
        elif fetch_tts(f"{text}。", dest):
            ok += 1
            time.sleep(0.15)
        else:
            fail += 1
        if (i + 1) % 50 == 0:
            print(f"  进度 {i+1}/{len(words)}: +{ok} 跳过 {skip} 失败 {fail}")

    print(f"\n完成: 新增 {ok}，跳过 {skip}，失败 {fail}")
    with open(os.path.join(HERE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"清单已写入 manifest.json")
    print(f"\n下一步（把切片分发到各版本）:")
    print(f"  cp -r {AUDIO_DIR} ../v2/audio/")
    print(f"  cp -r {AUDIO_DIR} ../v3/public/audio/")

if __name__ == "__main__":
    main()

