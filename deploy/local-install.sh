#!/usr/bin/env bash
# ============================================================================
# 听写小助手 —— 本地 Ubuntu 一键部署（生成音频切片 + 可选试跑）
#
# 用法：
#   cp deploy/local.env.example deploy/local.env
#   nano deploy/local.env          # 填有道密钥
#   chmod 600 deploy/local.env
#   bash deploy/local-install.sh
#
# 做三件事：
#   1. 建 venv 装依赖（V1 / V2）
#   2. 初始化本地数据库（已存在则保留）
#   3. 生成音频切片到 shared/web/audio/（增量，可中断重跑）
#
# 不需要 root（除了缺 ffmpeg / python3-venv 时装系统包）。
#
# 可选参数：
#   --skip-slices   跳过切片生成
#   --slices-only   只生成切片，不建 venv / 不初始化数据库
#   --serve v1|v2   装完直接启动本地服务器
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/local.env"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
step() { echo; echo "${C_HEAD}==> $*${C_OFF}"; }
ok()   { echo "${C_OK}  [OK]${C_OFF} $*"; }
warn() { echo "${C_WARN}  [!] ${C_OFF} $*"; }
die()  { echo "${C_ERR}  [X] $*${C_OFF}" >&2; exit 1; }

SKIP_SLICES=no
SLICES_ONLY=no
SERVE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-slices) SKIP_SLICES=yes; shift ;;
    --slices-only) SLICES_ONLY=yes; shift ;;
    --serve)       SERVE="${2-}"; shift 2 ;;
    -h|--help)     sed -n '2,26p' "$0"; exit 0 ;;
    *)             die "未知参数: $1" ;;
  esac
done
[[ -z "$SERVE" || "$SERVE" == "v1" || "$SERVE" == "v2" ]] \
  || die "--serve 只能是 v1 或 v2"

# ── 读取配置 ─────────────────────────────────────────────────────────────
step "读取配置"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE
  请先执行：cp deploy/local.env.example deploy/local.env 然后填入有道密钥"

set -a; . "$ENV_FILE"; set +a

: "${SETUP_V1:=yes}"      ; : "${SETUP_V2:=yes}"
: "${YOUDAO_VOICE:=youxiaoxun}" ; : "${YOUDAO_SPEED:=0.6}"
: "${TTS_INTERVAL:=1.0}"  ; : "${TTS_RETRY:=3}"
: "${V1_PORT:=8888}"      ; : "${V2_PORT:=8889}"
: "${WARM_V1_CACHE:=yes}"

SETUP_V1="$(echo "$SETUP_V1" | tr '[:upper:]' '[:lower:]')"
SETUP_V2="$(echo "$SETUP_V2" | tr '[:upper:]' '[:lower:]')"
WARM_V1_CACHE="$(echo "$WARM_V1_CACHE" | tr '[:upper:]' '[:lower:]')"
[[ "$SLICES_ONLY" == "yes" ]] && { SETUP_V1=no; SETUP_V2=no; }

is_placeholder() {
  case "${1-}" in ""|REPLACE_WITH*) return 0 ;; *) return 1 ;; esac
}

# 切片生成需要真实密钥；已有足够切片时可以不填
AUDIO_DIR="$REPO_ROOT/shared/web/audio"
EXISTING="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"

NEED_KEYS=no
if [[ "$SKIP_SLICES" != "yes" && "$EXISTING" -lt 500 ]]; then
  NEED_KEYS=yes
fi
if [[ "$NEED_KEYS" == "yes" ]]; then
  if is_placeholder "${YOUDAO_APP_KEY-}" || is_placeholder "${YOUDAO_APP_SECRET-}"; then
    die "需要生成切片（当前只有 $EXISTING 个），但 YOUDAO_APP_KEY / YOUDAO_APP_SECRET 仍是占位符。
  请编辑 deploy/local.env 填入真实密钥；
  若不想现在生成切片，加 --skip-slices 重跑。"
  fi
  ok "有道密钥已配置"
fi
ok "仓库根目录: $REPO_ROOT"
ok "现有切片: $EXISTING 个"

# ── 检查系统依赖 ─────────────────────────────────────────────────────────
step "检查系统依赖"

MISSING_PKGS=()
command -v python3 >/dev/null 2>&1 || MISSING_PKGS+=("python3")
python3 -c "import venv" 2>/dev/null || MISSING_PKGS+=("python3-venv")
command -v sqlite3  >/dev/null 2>&1 || MISSING_PKGS+=("sqlite3")
command -v rsync    >/dev/null 2>&1 || MISSING_PKGS+=("rsync")

# ffmpeg：V1 拼接音频必需；V2 仅录音工作台切割用到
if [[ "$SETUP_V1" == "yes" ]] || [[ "$SETUP_V2" == "yes" ]]; then
  command -v ffmpeg >/dev/null 2>&1 || MISSING_PKGS+=("ffmpeg")
fi

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
  warn "缺少系统包: ${MISSING_PKGS[*]}"
  if command -v sudo >/dev/null 2>&1; then
    echo "  正在安装（需要 sudo 密码）…"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING_PKGS[@]}"
    ok "系统包已安装"
  else
    die "请手动安装后重跑：
  sudo apt update && sudo apt install -y ${MISSING_PKGS[*]}"
  fi
else
  ok "系统包齐备"
fi

ok "Python $(python3 -V 2>&1 | awk '{print $2}')"
command -v ffmpeg >/dev/null 2>&1 && ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

# 题库文件
for f in shared/data/kp_grade3.json shared/data/lessons_grade3.json shared/init_db.py; do
  [[ -f "$REPO_ROOT/$f" ]] || die "缺少关键文件: $f（代码不完整？）"
done
ok "题库与建库脚本就位"

# ── 建 venv、装依赖、初始化数据库 ────────────────────────────────────────
setup_version() {
  local ver="$1"
  local dir="$REPO_ROOT/$ver"

  step "配置 $ver 本地环境"
  [[ -d "$dir" ]] || die "找不到目录 $dir"

  if [[ ! -x "$dir/venv/bin/python" ]]; then
    python3 -m venv "$dir/venv"
    ok "已建 venv"
  else
    ok "venv 已存在"
  fi

  "$dir/venv/bin/pip" install --quiet --upgrade pip
  "$dir/venv/bin/pip" install --quiet -r "$dir/requirements.txt"
  ok "依赖已安装"

  if [[ -f "$dir/dictation.db" ]]; then
    ok "数据库已存在，跳过初始化（数据保留）"
    "$dir/venv/bin/python" "$REPO_ROOT/shared/tools/migrate_poly_ids.py" \
      "$dir/dictation.db" >/dev/null 2>&1 \
      && ok "poly_ids 列已就绪" \
      || warn "poly_ids 迁移未执行，多音字轮换可能失效"
  else
    "$dir/venv/bin/python" "$REPO_ROOT/shared/init_db.py" --db "$dir/dictation.db"
    ok "数据库已初始化"
  fi
}

[[ "$SETUP_V1" == "yes" ]] && setup_version v1
[[ "$SETUP_V2" == "yes" ]] && setup_version v2

# ── 生成音频切片 ─────────────────────────────────────────────────────────
step "生成音频切片"

# gen_slices.py 只需要 requests。优先用已建好的 V1 venv（含 requests），
# 否则建一个最小的临时环境，避免动系统 Python。
pick_python() {
  if [[ -x "$REPO_ROOT/v1/venv/bin/python" ]]; then
    echo "$REPO_ROOT/v1/venv/bin/python"; return
  fi
  local gv="$REPO_ROOT/.venv-gen"
  if [[ ! -x "$gv/bin/python" ]]; then
    python3 -m venv "$gv" >/dev/null
    "$gv/bin/pip" install --quiet --upgrade pip
    "$gv/bin/pip" install --quiet requests
  fi
  echo "$gv/bin/python"
}

if [[ "$SKIP_SLICES" == "yes" ]]; then
  ok "按 --skip-slices 跳过（现有 $EXISTING 个）"
else
  GEN_PY="$(pick_python)"
  ok "使用 $(basename "$(dirname "$(dirname "$GEN_PY")")")/venv 运行生成脚本"
  echo "${C_DIM}  首次生成约 500+ 个文件，需数分钟。中断后重跑会接着做。${C_OFF}"
  echo

  YOUDAO_APP_KEY="${YOUDAO_APP_KEY-}" \
  YOUDAO_APP_SECRET="${YOUDAO_APP_SECRET-}" \
  YOUDAO_VOICE="$YOUDAO_VOICE" \
  YOUDAO_SPEED="$YOUDAO_SPEED" \
  TTS_INTERVAL="$TTS_INTERVAL" \
  TTS_RETRY="$TTS_RETRY" \
    "$GEN_PY" "$REPO_ROOT/shared/gen_slices.py"

  NOW="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$NOW" -lt 100 ]]; then
    die "切片生成后仍只有 $NOW 个，请检查上方错误。
  若是 errorCode 411（限流），把 deploy/local.env 里的 TTS_INTERVAL 调大到 2.0 后重跑。"
  fi
  ok "切片共 $NOW 个（本次新增 $((NOW - EXISTING)) 个）"
  EXISTING="$NOW"
fi

# 关键文件存在性检查
[[ -f "$AUDIO_DIR/sys/intro.mp3" ]] \
  && ok "开场提示音就位" \
  || warn "缺少 sys/intro.mp3，播放开场可能无声"

# ── 预热 V1 缓存 ─────────────────────────────────────────────────────────
# v1/main.py 默认缓存目录是 v1/audio/cache，而 warm_v1_cache.py 默认写
# v1/tts_cache —— 两者不一致会导致预热白做。这里统一指定到 v1/tts_cache，
# 与 vps-install.sh 写进 /etc/dictation/v1.env 的 AUDIO_CACHE_DIR 保持一致。
V1_CACHE_DIR="$REPO_ROOT/v1/tts_cache"

if [[ "$SETUP_V1" == "yes" && "$WARM_V1_CACHE" == "yes" && "$EXISTING" -gt 100 ]]; then
  step "预热 V1 缓存"
  echo "${C_DIM}  从已生成的切片复制，几乎不消耗有道额度${C_OFF}"
  mkdir -p "$V1_CACHE_DIR"
  YOUDAO_APP_KEY="${YOUDAO_APP_KEY-}" \
  YOUDAO_APP_SECRET="${YOUDAO_APP_SECRET-}" \
  YOUDAO_VOICE="$YOUDAO_VOICE" \
  YOUDAO_SPEED="$YOUDAO_SPEED" \
  AUDIO_CACHE_DIR="$V1_CACHE_DIR" \
    "$REPO_ROOT/v1/venv/bin/python" "$REPO_ROOT/shared/tools/warm_v1_cache.py" \
      --cache-dir "$V1_CACHE_DIR" \
    || warn "预热未完全成功，不影响出题（V1 会在请求时按需合成）"
  CACHED="$(find "$V1_CACHE_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  ok "V1 缓存 $CACHED 个文件 → $V1_CACHE_DIR"
fi

# ── 写本地运行用的环境变量文件 ───────────────────────────────────────────
step "生成本地启动脚本"

cat > "$REPO_ROOT/.local-run.env" <<EOF
# 由 deploy/local-install.sh 生成，供本地试跑用（已在 .gitignore）
export YOUDAO_APP_KEY='${YOUDAO_APP_KEY-}'
export YOUDAO_APP_SECRET='${YOUDAO_APP_SECRET-}'
export YOUDAO_VOICE='$YOUDAO_VOICE'
export YOUDAO_SPEED='$YOUDAO_SPEED'
export AUDIO_CACHE_DIR='$V1_CACHE_DIR'
export AUDIO_OUTPUT_DIR='$REPO_ROOT/v1/audio'
EOF
chmod 600 "$REPO_ROOT/.local-run.env"
ok ".local-run.env（含密钥，权限 600）"

# ── 完成 ─────────────────────────────────────────────────────────────────
echo
echo "${C_HEAD}============================================================${C_OFF}"
echo "${C_OK}  本地部署完成${C_OFF}"
echo "${C_HEAD}============================================================${C_OFF}"
echo
echo "  音频切片   $EXISTING 个  →  shared/web/audio/"
[[ "$SETUP_V1" == "yes" ]] && echo "  V1 环境    v1/venv  +  v1/dictation.db"
[[ "$SETUP_V2" == "yes" ]] && echo "  V2 环境    v2/venv  +  v2/dictation.db"
echo
echo "${C_HEAD}本地试跑${C_OFF}"
if [[ "$SETUP_V2" == "yes" ]]; then
  echo "  V2（推荐，切片直接播，无需等待合成）："
  echo "    bash deploy/local-install.sh --skip-slices --serve v2"
  echo "  或手动："
  echo "    cd v2 && ./venv/bin/uvicorn main:app --reload --port $V2_PORT"
  echo "    浏览器打开 http://localhost:$V2_PORT"
fi
if [[ "$SETUP_V1" == "yes" ]]; then
  echo
  echo "  V1（运行时合成，需要密钥）："
  echo "    bash deploy/local-install.sh --skip-slices --serve v1"
  echo "  或手动："
  echo "    source .local-run.env"
  echo "    cd v1 && ./venv/bin/uvicorn main:app --reload --port $V1_PORT"
fi
echo
echo "${C_HEAD}下一步：同步切片到 VPS${C_OFF}"
echo "  VPS 部署完成后，回到本机执行："
echo "    bash deploy/sync-slices.sh root@你的VPS_IP"
echo
echo "${C_HEAD}下一步：部署 Cloudflare（V3）${C_OFF}"
echo "    cp deploy/cloudflare.env.example deploy/cloudflare.env"
echo "    nano deploy/cloudflare.env"
echo "    bash deploy/cloudflare-deploy.sh --skip-slices"
echo "  （切片已在本地生成，用 --skip-slices 直接复用）"
echo

# ── 可选：直接启动 ───────────────────────────────────────────────────────
if [[ -n "$SERVE" ]]; then
  PORT_VAR="${SERVE^^}_PORT"
  PORT="${!PORT_VAR}"
  step "启动 $SERVE 本地服务器（Ctrl+C 结束）"
  echo "  http://localhost:$PORT"
  echo
  cd "$REPO_ROOT/$SERVE"
  if [[ "$SERVE" == "v1" ]]; then
    set -a; . "$REPO_ROOT/.local-run.env"; set +a
  fi
  exec "$REPO_ROOT/$SERVE/venv/bin/uvicorn" main:app --reload --port "$PORT"
fi
