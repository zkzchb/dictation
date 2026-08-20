#!/usr/bin/env python3
"""tools/stage.py — 为各版本铺装来自 shared/ 的共享资源

V2 的兼容铺装目标是 v2/audio/；正式 VPS 直接使用 shared/web/audio/。
V3（Cloudflare Workers）需要前端和音频切片放在 v3/public/，由 wrangler 打包上传。

用法：
  python tools/stage.py v2    # 铺 shared/web/audio/ → v2/audio/
  python tools/stage.py v3    # 铺 shared/web/ → v3/public/（含 index.html + audio/）
  python tools/stage.py all   # 两者都铺（默认）

注意：
  - 运行目录默认由 chinese/3a/tts 教材基线安装，也可由生成脚本维护
  - 只铺装 w/、sys/ 下的 MP3；真人录音台账、Check 状态和临时文件绝不公开
  - V3 public/ 已列入 .gitignore，每次部署前需重新 stage
"""
import sys
import os
import shutil

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_WEB = os.path.join(ROOT, "shared", "web")


def stage_audio(src, dst):
    """Rebuild a public audio tree using only MP3 files in w/ and sys/."""
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    copied = 0
    for subdir in ("w", "sys"):
        source_dir = os.path.join(src, subdir)
        if not os.path.isdir(source_dir):
            continue
        target_dir = os.path.join(dst, subdir)
        os.makedirs(target_dir, exist_ok=True)
        for name in sorted(os.listdir(source_dir)):
            source = os.path.join(source_dir, name)
            if name.endswith(".mp3") and os.path.isfile(source):
                shutil.copy2(source, os.path.join(target_dir, name))
                copied += 1
    return copied


def stage_v2():
    ok = True

    # 前端：shared/web/index.html 是唯一母本，V2 的副本由此生成
    html_src = os.path.join(SHARED_WEB, "index.html")
    html_dst = os.path.join(ROOT, "v2", "dictation_www", "index.html")
    if os.path.exists(html_src):
        os.makedirs(os.path.dirname(html_dst), exist_ok=True)
        shutil.copy(html_src, html_dst)
        print(f"[OK]  V2: index.html → {html_dst}")
    else:
        print(f"[!]  找不到前端母本: {html_src}")
        ok = False

    # 播放参数配置（前端按 /playback_config.json 加载）
    cfg_src = os.path.join(SHARED_WEB, "playback_config.json")
    cfg_dst = os.path.join(ROOT, "v2", "dictation_www", "playback_config.json")
    if os.path.exists(cfg_src):
        shutil.copy(cfg_src, cfg_dst)
        print(f"[OK]  V2: playback_config.json → {cfg_dst}")

    # 音频切片
    src = os.path.join(SHARED_WEB, "audio")
    dst = os.path.join(ROOT, "v2", "audio")
    if not os.path.isdir(src):
        print(f"[!]  音频目录不存在: {src}")
        print("   请先运行: bash deploy/local-install.sh")
        return False
    n = stage_audio(src, dst)
    print(f"[OK]  V2: audio/  ({n} 个文件) → {dst}")
    return ok


def stage_v3():
    src      = SHARED_WEB
    dst      = os.path.join(ROOT, "v3", "public")
    html_src = os.path.join(src, "index.html")
    html_dst = os.path.join(dst, "index.html")
    audio_src = os.path.join(src, "audio")
    audio_dst = os.path.join(dst, "audio")

    os.makedirs(dst, exist_ok=True)

    if not os.path.exists(html_src):
        print(f"[!]  找不到前端: {html_src}")
        return False
    shutil.copy(html_src, html_dst)
    print(f"[OK]  V3: index.html → {html_dst}")

    # 播放参数配置（前端按 /playback_config.json 加载）
    cfg_src = os.path.join(src, "playback_config.json")
    if os.path.exists(cfg_src):
        shutil.copy(cfg_src, os.path.join(dst, "playback_config.json"))
        print(f"[OK]  V3: playback_config.json → {dst}")

    if os.path.isdir(audio_src):
        n = stage_audio(audio_src, audio_dst)
        print(f"[OK]  V3: audio/   ({n} 个文件) → {audio_dst}")
    else:
        print(f"[!]  音频目录不存在: {audio_src}")
        print("   请先运行: bash deploy/local-install.sh")
        print("   (index.html 已复制，待音频生成后再次运行 stage)")
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in ("v2", "v3", "all"):
        print("用法: python tools/stage.py [v2|v3|all]")
        sys.exit(1)
    if target in ("v2", "all"):
        stage_v2()
    if target in ("v3", "all"):
        stage_v3()
