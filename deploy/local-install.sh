#!/usr/bin/env bash
# ============================================================================
# 听写小助手 —— 本地 Ubuntu 一键部署（生成音频切片 + 可选试跑）
#
# 用法：
#   cp deploy/local.env.example deploy/local.env
#   nano deploy/local.env          # V1 / 重新生成 TTS 时填写有道密钥
#   chmod 600 deploy/local.env
#   bash deploy/local-install.sh
#
# 做三件事：
#   1. 建 venv 装依赖（V1 / V2）
#   2. 初始化本地数据库（已存在则保留）
#   3. 从仓库教材包安装音频；缺失时可调用 TTS 增量生成
#
# 不需要 root（除了缺 ffmpeg / python3-venv 时装系统包）。
#
# 可选参数：
#   --skip-slices        跳过切片生成
#   --slices-only        只生成切片，不建 venv / 不初始化数据库
#   --serve v1|v2        装完直接启动本地服务器（前台，Ctrl+C 结束）
#   --install-service    装成 systemd 服务，开机自启（仅监听 127.0.0.1）
#   --uninstall-service  移除 systemd 服务（保留数据与切片）
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
INSTALL_SVC=no
UNINSTALL_SVC=no
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-slices)       SKIP_SLICES=yes; shift ;;
    --slices-only)       SLICES_ONLY=yes; shift ;;
    --serve)             SERVE="${2-}"; shift 2 ;;
    --install-service)   INSTALL_SVC=yes; shift ;;
    --uninstall-service) UNINSTALL_SVC=yes; shift ;;
    -h|--help)           sed -n '2,28p' "$0"; exit 0 ;;
    *)                   die "未知参数: $1" ;;
  esac
done
[[ -z "$SERVE" || "$SERVE" == "v1" || "$SERVE" == "v2" ]] \
  || die "--serve 只能是 v1 或 v2"
[[ "$INSTALL_SVC" == "yes" && -n "$SERVE" ]] \
  && die "--install-service 与 --serve 不能同时用（服务已在后台运行，无需前台试跑）"

# ── 移除服务（独立操作，不走后续流程）─────────────────────────────────────
if [[ "$UNINSTALL_SVC" == "yes" ]]; then
  step "移除 systemd 服务"
  command -v systemctl >/dev/null || die "本机没有 systemd"
  for v in v1 v2; do
    unit="dictation-local-$v"
    if systemctl list-unit-files "$unit.service" >/dev/null 2>&1 \
       && [[ -f "/etc/systemd/system/$unit.service" ]]; then
      sudo systemctl disable --now "$unit" >/dev/null 2>&1 || true
      sudo rm -f "/etc/systemd/system/$unit.service"
      ok "已移除 $unit"
    else
      echo "  ${C_DIM}$unit 未安装，跳过${C_OFF}"
    fi
  done
  sudo systemctl daemon-reload
  sudo systemctl reset-failed 2>/dev/null || true
  echo
  ok "服务已移除。数据库与音频切片未受影响。"
  exit 0
fi

# ── 读取配置 ─────────────────────────────────────────────────────────────
step "读取配置"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE
  请先执行：cp deploy/local.env.example deploy/local.env 然后填入有道密钥"

set -a; . "$ENV_FILE"; set +a

: "${SETUP_V1:=no}"       ; : "${SETUP_V2:=yes}"
: "${YOUDAO_VOICE:=youxiaoxun}" ; : "${YOUDAO_SPEED:=0.6}"
: "${TTS_INTERVAL:=1.0}"  ; : "${TTS_RETRY:=3}"
: "${V1_PORT:=8888}"      ; : "${V2_PORT:=8889}"
: "${BIND_HOST:=127.0.0.1}"
: "${STUDIO_ENABLED:=1}"
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
CONTENT_ROOT="$REPO_ROOT/chinese/3a"
CONTENT_AUDIO_DIR="$CONTENT_ROOT/tts"
AUDIO_TOOL="$REPO_ROOT/shared/tools/audio_bundle.py"
export DICTATION_CONTENT_ROOT="$CONTENT_ROOT"
EXISTING="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"

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
for f in chinese/3a/dataset.json chinese/3a/lessons.json chinese/3a/knowledge_points.json \
         chinese/3a/studio_manifest.json chinese/3a/tts.sha256 \
         shared/init_db.py shared/tools/audio_bundle.py; do
  [[ -f "$REPO_ROOT/$f" ]] || die "缺少关键文件: $f（代码不完整？）"
done
ok "题库与建库脚本就位"

# 正式教材包随仓库分发。运行目录为空或不完整时只补缺失文件；-n 保证本地真人
# 录音不会被标准 TTS 覆盖。依赖和关键文件就位后再调用严格校验工具。
if ! python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null 2>&1; then
  if python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT" >/dev/null 2>&1; then
    mkdir -p "$AUDIO_DIR"
    cp -an "$CONTENT_AUDIO_DIR/." "$AUDIO_DIR/"
    EXISTING="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
    ok "已从 chinese/3a 教材包补齐标准音频"
  fi
fi

if ! python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null 2>&1; then
  if [[ "$SKIP_SLICES" == "yes" ]]; then
    [[ "$SETUP_V2" != "yes" ]] \
      || die "V2 音频未通过完整性校验，不能用 --skip-slices 跳过"
  elif is_placeholder "${YOUDAO_APP_KEY-}" || is_placeholder "${YOUDAO_APP_SECRET-}"; then
    die "仓库音频不完整（当前只有 $EXISTING 个），且有道密钥仍是占位符。
  请先确认 chinese/3a 教材包完整；维护者也可填写密钥后重新生成。"
  else
    ok "仓库音频不完整，将使用已配置的有道密钥补齐"
  fi
fi

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
  "$dir/venv/bin/pip" check >/dev/null
  ok "$ver 依赖已从在线软件源安装"

  if [[ -f "$dir/dictation.db" ]]; then
    ok "数据库已存在，跳过初始化（数据保留）"
    "$dir/venv/bin/python" "$REPO_ROOT/shared/tools/migrate_poly_ids.py" \
      "$dir/dictation.db" >/dev/null 2>&1 \
      && ok "poly_ids 列已就绪" \
      || warn "poly_ids 迁移未执行，多音字轮换可能失效"
  else
    "$dir/venv/bin/python" "$REPO_ROOT/shared/init_db.py" \
      --db "$dir/dictation.db" --content-root "$CONTENT_ROOT"
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
  echo "${C_DIM}  完整基线是 894 个文件。中断后重跑会接着做。${C_OFF}"
  echo

  YOUDAO_APP_KEY="${YOUDAO_APP_KEY-}" \
  YOUDAO_APP_SECRET="${YOUDAO_APP_SECRET-}" \
  YOUDAO_VOICE="$YOUDAO_VOICE" \
  YOUDAO_SPEED="$YOUDAO_SPEED" \
  TTS_INTERVAL="$TTS_INTERVAL" \
  TTS_RETRY="$TTS_RETRY" \
    "$GEN_PY" "$REPO_ROOT/shared/gen_slices.py"

  NOW="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  if ! python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null 2>&1; then
    die "切片生成后仍未通过严格清单校验（当前 $NOW 个），请检查上方错误。
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
echo "${C_HEAD}VPS 部署${C_OFF}"
echo "  标准 V2 会直接使用仓库教材包，无需从本机同步 TTS。"
echo "  sync-slices.sh 仅用于维护自定义教材或同步运行中的真人录音。"
echo
echo "${C_HEAD}下一步：部署 Cloudflare（V3）${C_OFF}"
echo "    cp deploy/cloudflare.env.example deploy/cloudflare.env"
echo "    nano deploy/cloudflare.env"
echo "    bash deploy/cloudflare-deploy.sh --skip-slices"
echo "  （切片已在本地生成，用 --skip-slices 直接复用）"
echo

# ── 可选：装成 systemd 服务（仅监听 127.0.0.1）─────────────────────────────
if [[ "$INSTALL_SVC" == "yes" ]]; then
  step "安装 systemd 服务（开机自启，仅本机可访问）"
  command -v systemctl >/dev/null || die "本机没有 systemd，无法装服务"

  RUN_USER="$(id -un)"
  RUN_GROUP="$(id -gn)"
  [[ -f "$REPO_ROOT/.local-run.env" ]] \
    || die "缺少 .local-run.env（先不带 --install-service 跑一次本脚本）"

  # systemd 的 EnvironmentFile 不认 `export` 关键字，只接受纯 KEY=value。
  # .local-run.env 是给 shell `source` 用的（带 export），不能直接喂给 systemd
  # —— 否则 V1 拿不到密钥，合成时会用占位符去请求有道并失败。
  # 这里单独生成一份 systemd 格式的。
  ENV_SVC="$REPO_ROOT/.local-svc.env"
  cat > "$ENV_SVC" <<EOF
# 由 deploy/local-install.sh --install-service 生成（systemd 格式，无 export）
YOUDAO_APP_KEY=${YOUDAO_APP_KEY-}
YOUDAO_APP_SECRET=${YOUDAO_APP_SECRET-}
YOUDAO_VOICE=$YOUDAO_VOICE
YOUDAO_SPEED=$YOUDAO_SPEED
AUDIO_CACHE_DIR=$V1_CACHE_DIR
AUDIO_OUTPUT_DIR=$REPO_ROOT/v1/audio
STUDIO_ENABLED=$STUDIO_ENABLED
EOF
  chmod 600 "$ENV_SVC"
  ok ".local-svc.env（systemd 格式，权限 600）"

  for v in v1 v2; do
    [[ "$v" == "v1" && "$SETUP_V1" != "yes" ]] && continue
    [[ "$v" == "v2" && "$SETUP_V2" != "yes" ]] && continue
    [[ -x "$REPO_ROOT/$v/venv/bin/uvicorn" ]] || { warn "$v venv 不完整，跳过"; continue; }

    port_var="${v^^}_PORT"; port="${!port_var}"
    unit="dictation-local-$v"
    desc=$([[ "$v" == "v1" ]] && echo "实时 TTS 合成" || echo "预录切片 + 录音工作台")

    # 监听地址由 BIND_HOST 决定：
    #   127.0.0.1  仅本机
    #   0.0.0.0    局域网可访问（手机用；同网段任何设备都能打开，且无鉴权）
    sudo tee "/etc/systemd/system/$unit.service" >/dev/null <<EOF
[Unit]
Description=听写小助手 ${v^^}（本地 · $desc）
After=network.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$REPO_ROOT/$v
EnvironmentFile=$ENV_SVC
ExecStart=$REPO_ROOT/$v/venv/bin/uvicorn main:app --host $BIND_HOST --port $port
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
    ok "$unit  →  127.0.0.1:$port"
  done

  sudo systemctl daemon-reload
  units=()
  [[ "$SETUP_V1" == "yes" && -f /etc/systemd/system/dictation-local-v1.service ]] \
    && units+=(dictation-local-v1)
  [[ "$SETUP_V2" == "yes" && -f /etc/systemd/system/dictation-local-v2.service ]] \
    && units+=(dictation-local-v2)
  # set -u 下空数组展开会报错，先兜底
  [[ ${#units[@]} -gt 0 ]] || die "没有可启动的服务（V1/V2 的 venv 都不完整？）"
  sudo systemctl enable --now "${units[@]}"
  sleep 2

  echo
  for u in "${units[@]}"; do
    if systemctl is-active --quiet "$u"; then
      ok "$u 运行中"
    else
      warn "$u 未启动 —— 排查：journalctl -u $u -n 30 --no-pager"
    fi
  done

  echo
  echo "${C_HEAD}访问地址${C_OFF}"
  if [[ "$BIND_HOST" == "127.0.0.1" ]]; then
    [[ "$SETUP_V1" == "yes" ]] && echo "  V1  http://localhost:$V1_PORT"
    [[ "$SETUP_V2" == "yes" ]] && echo "  V2  http://localhost:$V2_PORT"
    echo
    echo "  ${C_DIM}仅监听 127.0.0.1，局域网其他设备访问不到。${C_OFF}"
    echo "  ${C_DIM}要让手机访问，在 deploy/local.env 设 BIND_HOST=0.0.0.0 后重跑。${C_OFF}"
  else
    # 列出本机所有 IPv4，方便直接在手机上输入
    ips="$(ip -4 -o addr show scope global 2>/dev/null \
             | awk '{split($4,a,"/"); print a[1]}' || true)"
    [[ "$SETUP_V2" == "yes" ]] && {
      echo "  ${C_OK}V2（推荐手机用）${C_OFF}"
      echo "    本机    http://localhost:$V2_PORT"
      for ip in $ips; do echo "    局域网  http://$ip:$V2_PORT"; done
    }
    [[ "$SETUP_V1" == "yes" ]] && {
      echo "  V1"
      echo "    本机    http://localhost:$V1_PORT"
      for ip in $ips; do echo "    局域网  http://$ip:$V1_PORT"; done
    }
  fi
  echo
  echo "${C_HEAD}日常运维${C_OFF}"
  echo "  状态    systemctl status dictation-local-v1 dictation-local-v2"
  echo "  日志    journalctl -u dictation-local-v2 -f"
  echo "  重启    sudo systemctl restart dictation-local-v2"
  echo "  移除    bash deploy/local-install.sh --uninstall-service"
  echo
  if [[ "$BIND_HOST" != "127.0.0.1" ]]; then
    echo "${C_WARN}  局域网已开放，且应用本身没有任何鉴权${C_OFF}"
    echo "  ${C_DIM}同一 WiFi 下的设备都能打开、都能提交听写记录。${C_OFF}"
    if [[ "$STUDIO_ENABLED" == "1" ]]; then
      echo "  ${C_DIM}录音工作台 /studio 处于开启状态（可写音频文件到磁盘）。${C_OFF}"
      echo "  ${C_DIM}不需要录音时建议在 deploy/local.env 设 STUDIO_ENABLED=0 后重跑。${C_OFF}"
    else
      echo "  ${C_OK}  录音工作台已关闭（STUDIO_ENABLED=0）${C_OFF}"
    fi
    echo
  fi
  echo "  ${C_DIM}代码更新后需重启：git pull && sudo systemctl restart dictation-local-v1 dictation-local-v2${C_OFF}"
  echo
  exit 0
fi

# ── 可选：直接启动 ───────────────────────────────────────────────────────
if [[ -n "$SERVE" ]]; then
  PORT_VAR="${SERVE^^}_PORT"
  PORT="${!PORT_VAR}"
  step "启动 $SERVE 本地服务器（Ctrl+C 结束）"
  echo "  本机    http://localhost:$PORT"
  if [[ "$BIND_HOST" != "127.0.0.1" ]]; then
    while read -r ip; do
      [[ -n "$ip" ]] && echo "  局域网  http://$ip:$PORT"
    done < <(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' || true)
  fi
  echo
  cd "$REPO_ROOT/$SERVE"
  if [[ "$SERVE" == "v1" ]]; then
    set -a; . "$REPO_ROOT/.local-run.env"; set +a
  fi
  exec "$REPO_ROOT/$SERVE/venv/bin/uvicorn" main:app \
    --reload --host "$BIND_HOST" --port "$PORT"
fi
