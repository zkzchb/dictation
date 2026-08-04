#!/usr/bin/env bash
# =============================================================================
# 听写小助手 —— 本地 Ubuntu 一键部署（V1 + V2）
#
# 适用场景：在本机（10.10.10.10 这类内网机器）跑 V1/V2，主要用于录音与试用。
# 与 VPS 部署的差异：不配域名、不装 Caddy、不申请证书，直接 IP:端口 访问。
#
# 用法（在 git 仓库根目录执行）：
#     chmod +x deploy-local.sh
#     ./deploy-local.sh
#
# 幂等：可反复执行。已完成的步骤会跳过，代码更新后重跑即可。
#
# 完成后：
#     V1  http://localhost:8888        运行时 TTS（需有道密钥）
#     V2  http://localhost:8889        预录切片
#     录音工作台  http://localhost:8889/studio
#
# ⚠ 录音必须用 localhost 访问（麦克风要求安全上下文，http://IP 会被浏览器拒绝）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=python3
VENV_V1="$ROOT/v1/venv"
VENV_V2="$ROOT/v2/venv"
DB_V1="$ROOT/v1/dictation.db"
DB_V2="$ROOT/v2/dictation.db"
SERVICE_USER="${SUDO_USER:-$USER}"

info() { printf '\n\033[1;32m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31m[X] %s\033[0m\n' "$1"; exit 1; }

[[ -f "$ROOT/shared/selector.py" ]] || die "请在仓库根目录执行（找不到 shared/selector.py）"
cd "$ROOT"

# ── 1. 系统依赖 ───────────────────────────────────────────────────────────
info "1/7 安装系统依赖"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v $PY >/dev/null 2>&1; then
    sudo apt update
    # ffmpeg: V1 拼接音频、V2 studio 切割录音都需要
    sudo apt install -y python3 python3-venv python3-pip ffmpeg sqlite3
else
    echo "已具备 python3 / ffmpeg，跳过 apt"
fi
$PY --version
ffmpeg -version | head -1

# ── 2. 虚拟环境 ───────────────────────────────────────────────────────────
info "2/7 建立虚拟环境"
for v in v1 v2; do
    venv="$ROOT/$v/venv"
    if [[ ! -x "$venv/bin/python" ]]; then
        $PY -m venv "$venv"
        echo "  创建 $v/venv"
    fi
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet -r "$ROOT/$v/requirements.txt"
    echo "  $v 依赖就绪"
done
"$VENV_V1/bin/python" -c "import fastapi,pydantic,requests,pydub; print('  V1 import OK')"
"$VENV_V2/bin/python" -c "import fastapi,pydantic,pydub; print('  V2 import OK')"

# ── 3. 词表 JSON ──────────────────────────────────────────────────────────
info "3/7 检查词表数据"
if [[ ! -f "$ROOT/shared/data/kp_grade3.json" ]]; then
    warn "缺少 shared/data/kp_grade3.json"
    echo "  该文件由 convert_wordlist.py 从 xlsx 生成，但 xlsx 未入库。"
    echo "  若已 git pull 到最新版应自带此 JSON；否则请在 Windows 侧生成后拷入。"
    die "词表数据缺失，无法建库"
fi
"$VENV_V2/bin/python" - <<'PY'
import json
kp  = json.load(open("shared/data/kp_grade3.json", encoding="utf-8"))
les = json.load(open("shared/data/lessons_grade3.json", encoding="utf-8"))
print(f"  课程 {len(les)} 门, 知识点 {len(kp)} 条")
PY

# ── 4. 建库（已存在则跳过，保护数据）──────────────────────────────────────
info "4/7 初始化数据库"
for pair in "v1:$DB_V1" "v2:$DB_V2"; do
    v="${pair%%:*}"; db="${pair##*:}"
    if [[ -f "$db" ]]; then
        n=$(sqlite3 "$db" "SELECT COUNT(*) FROM dictation_history" 2>/dev/null || echo 0)
        echo "  $v 库已存在（$n 次听写记录），跳过。如需重建："
        echo "      shared/init_db.py --db $db --force"
    else
        "$VENV_V2/bin/python" "$ROOT/shared/init_db.py" --db "$db"
    fi
done

# ── 5. 音频目录与铺装 ─────────────────────────────────────────────────────
info "5/7 铺装前端与音频"
mkdir -p "$ROOT/shared/web/audio/w" "$ROOT/shared/web/audio/sys"
mkdir -p "$ROOT/v1/audio" "$ROOT/v1/tts_cache"
"$VENV_V2/bin/python" "$ROOT/tools/stage.py" v2 || warn "stage 部分失败（音频未生成时属正常）"

n_w=$(find "$ROOT/shared/web/audio/w" -name '*.mp3' 2>/dev/null | wc -l)
n_s=$(find "$ROOT/shared/web/audio/sys" -name '*.mp3' 2>/dev/null | wc -l)
echo "  当前切片: 词条 $n_w 个, 系统提示音 $n_s 个"
if [[ "$n_w" -eq 0 ]]; then
    warn "尚无音频切片 —— V2 现在能出词表但播放无声"
    echo "      方案A 先用 TTS 打底: YOUDAO_APP_KEY=x YOUDAO_APP_SECRET=y \\"
    echo "                            $VENV_V1/bin/python shared/gen_slices.py"
    echo "      方案B 直接真人录音: 访问 http://localhost:8889/studio"
fi

# ── 6. systemd 服务 ───────────────────────────────────────────────────────
info "6/7 配置 systemd 服务"
ENV_DIR=/etc/dictation
sudo mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_DIR/v1.env" ]]; then
    sudo tee "$ENV_DIR/v1.env" >/dev/null <<EOF
# V1 需要有道 TTS 实时合成，请填入真实密钥后 systemctl restart dictation-v1
YOUDAO_APP_KEY=REPLACE_WITH_YOUDAO_APP_KEY
YOUDAO_APP_SECRET=REPLACE_WITH_YOUDAO_APP_SECRET
AUDIO_OUTPUT_DIR=$ROOT/v1/audio
AUDIO_CACHE_DIR=$ROOT/v1/tts_cache
EOF
    sudo chmod 600 "$ENV_DIR/v1.env"
    warn "已生成 $ENV_DIR/v1.env，V1 使用前需填入有道密钥"
fi
[[ -f "$ENV_DIR/v2.env" ]] || {
    sudo tee "$ENV_DIR/v2.env" >/dev/null <<EOF
DICTATION_DB=$DB_V2
EOF
    sudo chmod 600 "$ENV_DIR/v2.env"
}

for v in v1 v2; do
    port=$([[ "$v" == v1 ]] && echo 8888 || echo 8889)
    desc=$([[ "$v" == v1 ]] && echo "TTS on-demand" || echo "pre-recorded slices + studio")
    sudo tee "/etc/systemd/system/dictation-$v.service" >/dev/null <<EOF
[Unit]
Description=Dictation $v ($desc)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$ROOT/$v
EnvironmentFile=$ENV_DIR/$v.env
ExecStart=$ROOT/$v/venv/bin/uvicorn main:app --host 0.0.0.0 --port $port
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now dictation-v1 dictation-v2
sleep 3

# ── 7. 验收 ───────────────────────────────────────────────────────────────
info "7/7 验收"
for pair in "V1:8888" "V2:8889"; do
    name="${pair%%:*}"; port="${pair##*:}"
    if curl -sf "http://127.0.0.1:$port/api/lessons" -o /tmp/_les.json 2>/dev/null; then
        cnt=$($PY -c "import json;print(len(json.load(open('/tmp/_les.json'))))" 2>/dev/null || echo '?')
        echo "  $name (:$port) OK — 课程 $cnt 门"
    else
        warn "$name (:$port) 未响应 —— journalctl -u dictation-${name,,} -n 30"
    fi
done

if curl -sf "http://127.0.0.1:8889/api/generate_daily/3111" -o /tmp/_wl.json 2>/dev/null; then
    $PY - <<'PY'
import json
d = json.load(open('/tmp/_wl.json'))
print(f"  出题验证 OK — 3111: {len(d['data'])} 词, {len(d.get('polyphonic_section',[]))} 多音字")
PY
fi
rm -f /tmp/_les.json /tmp/_wl.json

cat <<EOF

────────────────────────────────────────────────────────────
部署完成

  V1 听写      http://localhost:8888
  V2 听写      http://localhost:8889
  录音工作台   http://localhost:8889/studio

  ⚠ 录音请务必用 localhost 打开（http://10.10.10.10 麦克风会被浏览器拒绝）

常用命令
  sudo systemctl restart dictation-v2
  journalctl -u dictation-v2 -f
  ./deploy-local.sh                  # 代码更新后重跑（幂等）

下一步
  1) 生成音频：真人录音走 /studio，或 TTS 打底见上文提示
  2) 录完后同步给 V3：python tools/stage.py v3
────────────────────────────────────────────────────────────
EOF
