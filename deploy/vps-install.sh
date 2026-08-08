#!/usr/bin/env bash
# ============================================================================
# 听写小助手 —— Ubuntu VPS 一键部署（V1 / V2）
#
# 用法：
#   cp deploy/vps.env.example deploy/vps.env
#   nano deploy/vps.env          # 填密钥与域名
#   chmod 600 deploy/vps.env
#   sudo bash deploy/vps-install.sh
#
# 幂等：可重复运行。已存在的数据库不会被覆盖。
# ============================================================================
set -euo pipefail

# ── 路径与配置 ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/vps.env"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_OFF=$'\033[0m'

step() { echo; echo "${C_HEAD}==> $*${C_OFF}"; }
ok()   { echo "${C_OK}  [OK]${C_OFF} $*"; }
warn() { echo "${C_WARN}  [!] ${C_OFF} $*"; }
die()  { echo "${C_ERR}  [X] $*${C_OFF}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行：sudo bash deploy/vps-install.sh"

# ── 读取配置 ─────────────────────────────────────────────────────────────
step "读取配置"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE
  请先执行：cp deploy/vps.env.example deploy/vps.env 并填写密钥"

set -a; . "$ENV_FILE"; set +a

: "${DEPLOY_V1:=no}"      ; : "${DEPLOY_V2:=no}"
: "${V1_PORT:=8888}"      ; : "${V2_PORT:=8889}"
: "${APP_ROOT:=/opt/dictation}" ; : "${APP_USER:=dictation}"
: "${YOUDAO_VOICE:=youxiaoxun}" ; : "${YOUDAO_SPEED:=0.6}"
: "${TTS_MIN_INTERVAL:=0.2}"    ; : "${TTS_MAX_RETRY:=3}"
: "${BACKUP_KEEP_DAYS:=30}"
# 录音工作台默认开启：VPS 上有 Caddy 的 HTTPS（浏览器录音的前提）和
# basic_auth（挡住陌生人），是邀请老师录真人音频最合适的环境。
: "${STUDIO_ENABLED:=1}"

# 归一化 yes/no
DEPLOY_V1="$(echo "${DEPLOY_V1}" | tr '[:upper:]' '[:lower:]')"
DEPLOY_V2="$(echo "${DEPLOY_V2}" | tr '[:upper:]' '[:lower:]')"

[[ "$DEPLOY_V1" == "yes" || "$DEPLOY_V2" == "yes" ]] \
  || die "DEPLOY_V1 与 DEPLOY_V2 至少有一个要设为 yes"

# ── 校验必填项 ───────────────────────────────────────────────────────────
check_placeholder() {
  local name="$1" val="${2-}"
  [[ -n "$val" ]] || die "$name 未填写（见 deploy/vps.env）"
  case "$val" in
    REPLACE_WITH*) die "$name 仍是占位符，请填入真实值（见 deploy/vps.env）" ;;
  esac
}

check_placeholder BASIC_AUTH_USER     "${BASIC_AUTH_USER-}"
check_placeholder BASIC_AUTH_PASSWORD "${BASIC_AUTH_PASSWORD-}"

if [[ ${#BASIC_AUTH_PASSWORD} -lt 8 ]]; then
  warn "BASIC_AUTH_PASSWORD 短于 8 位，建议换更强的密码"
fi

if [[ "$DEPLOY_V1" == "yes" ]]; then
  check_placeholder V1_DOMAIN "${V1_DOMAIN-}"
  # V1 运行时要合成音频，密钥必须真实
  check_placeholder YOUDAO_APP_KEY    "${YOUDAO_APP_KEY-}"
  check_placeholder YOUDAO_APP_SECRET "${YOUDAO_APP_SECRET-}"
  ok "V1: $V1_DOMAIN (端口 $V1_PORT)"
fi

if [[ "$DEPLOY_V2" == "yes" ]]; then
  check_placeholder V2_DOMAIN "${V2_DOMAIN-}"
  ok "V2: $V2_DOMAIN (端口 $V2_PORT)"
fi

# ── 校验域名解析 ─────────────────────────────────────────────────────────
step "校验域名解析"
MY_IP="$(curl -fsS --max-time 10 https://api.ipify.org || echo "")"
if [[ -z "$MY_IP" ]]; then
  warn "无法取得本机公网 IP，跳过 DNS 校验"
else
  ok "本机公网 IP: $MY_IP"
  command -v dig >/dev/null 2>&1 || apt-get install -y -qq dnsutils >/dev/null 2>&1 || true
  for pair in "V1:${V1_DOMAIN-}" "V2:${V2_DOMAIN-}"; do
    ver="${pair%%:*}"; dom="${pair#*:}"
    [[ "$ver" == "V1" && "$DEPLOY_V1" != "yes" ]] && continue
    [[ "$ver" == "V2" && "$DEPLOY_V2" != "yes" ]] && continue
    [[ -n "$dom" ]] || continue
    resolved="$(dig +short "$dom" A | tail -1 || echo "")"
    if [[ "$resolved" == "$MY_IP" ]]; then
      ok "$dom → $resolved"
    else
      warn "$dom 解析为 '${resolved:-无记录}'，与本机 $MY_IP 不符"
      warn "  Caddy 将无法签发证书。可继续安装，但需先修正 DNS 再访问。"
    fi
  done
fi

# ── 安装系统包 ───────────────────────────────────────────────────────────
step "安装系统包"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip sqlite3 curl ufw rsync dnsutils \
  debian-keyring debian-archive-keyring apt-transport-https
ok "基础包就绪"

# ffmpeg：V1 拼接音频必需；V2 仅录音工作台切割用到，统一装上省事
apt-get install -y -qq ffmpeg
ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"

if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
CADDY_VER="$(caddy version | awk '{print $1}')"
ok "Caddy $CADDY_VER"

# ── 系统用户 ─────────────────────────────────────────────────────────────
step "创建系统用户"
if id "$APP_USER" >/dev/null 2>&1; then
  ok "用户 $APP_USER 已存在"
else
  adduser --system --group --home "$APP_ROOT" --shell /usr/sbin/nologin "$APP_USER"
  ok "已创建 $APP_USER"
fi

# ── 代码位置 ─────────────────────────────────────────────────────────────
step "确认代码位置"
if [[ "$REPO_ROOT" != "$APP_ROOT" ]]; then
  warn "代码在 $REPO_ROOT，而非约定的 $APP_ROOT"
  warn "  将以 $REPO_ROOT 为部署根目录继续"
  APP_ROOT="$REPO_ROOT"
fi
ok "部署根目录: $APP_ROOT"

for f in shared/data/kp_grade3.json shared/data/lessons_grade3.json shared/init_db.py; do
  [[ -f "$APP_ROOT/$f" ]] || die "缺少关键文件: $f（代码不完整？）"
done
ok "题库与建库脚本就位"

# ── 建 venv、装依赖、初始化数据库 ────────────────────────────────────────
setup_version() {
  local ver="$1"
  local dir="$APP_ROOT/$ver"

  step "配置 $ver"
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

  # 数据库：已存在则保留，只补 poly_ids 列
  if [[ -f "$dir/dictation.db" ]]; then
    ok "数据库已存在，跳过初始化（数据保留）"
    "$dir/venv/bin/python" "$APP_ROOT/shared/tools/migrate_poly_ids.py" \
      "$dir/dictation.db" >/dev/null 2>&1 \
      && ok "poly_ids 列已就绪" \
      || warn "poly_ids 迁移未执行，多音字轮换可能失效"
  else
    "$dir/venv/bin/python" "$APP_ROOT/shared/init_db.py" --db "$dir/dictation.db"
    ok "数据库已初始化"
  fi
}

[[ "$DEPLOY_V1" == "yes" ]] && setup_version v1
[[ "$DEPLOY_V2" == "yes" ]] && setup_version v2

# ── 写密钥文件 ───────────────────────────────────────────────────────────
step "写入密钥文件 /etc/dictation/"
mkdir -p /etc/dictation
chmod 755 /etc/dictation

if [[ "$DEPLOY_V1" == "yes" ]]; then
  cat > /etc/dictation/v1.env <<EOF
# 由 deploy/vps-install.sh 生成，请勿手动编辑（改 deploy/vps.env 后重跑脚本）
YOUDAO_APP_KEY=$YOUDAO_APP_KEY
YOUDAO_APP_SECRET=$YOUDAO_APP_SECRET
YOUDAO_VOICE=$YOUDAO_VOICE
YOUDAO_SPEED=$YOUDAO_SPEED
TTS_MIN_INTERVAL=$TTS_MIN_INTERVAL
TTS_MAX_RETRY=$TTS_MAX_RETRY
DICTATION_DB=$APP_ROOT/v1/dictation.db
AUDIO_OUTPUT_DIR=$APP_ROOT/v1/audio
AUDIO_CACHE_DIR=$APP_ROOT/v1/tts_cache
EOF
  chmod 600 /etc/dictation/v1.env
  chown root:root /etc/dictation/v1.env
  ok "/etc/dictation/v1.env (600)"
fi

if [[ "$DEPLOY_V2" == "yes" ]]; then
  cat > /etc/dictation/v2.env <<EOF
# 由 deploy/vps-install.sh 生成，请勿手动编辑（改 deploy/vps.env 后重跑脚本）
DICTATION_DB=$APP_ROOT/v2/dictation.db
WEB_ROOT=$APP_ROOT/shared/web
STUDIO_AUDIO_DIR=$APP_ROOT/shared/web/audio/w
STUDIO_ENABLED=$STUDIO_ENABLED
EOF
  chmod 600 /etc/dictation/v2.env
  chown root:root /etc/dictation/v2.env
  ok "/etc/dictation/v2.env (600)"
fi

# ── 目录与权限 ───────────────────────────────────────────────────────────
step "设置目录权限"
mkdir -p "$APP_ROOT/shared/web/audio/w" "$APP_ROOT/shared/web/audio/sys"
[[ "$DEPLOY_V1" == "yes" ]] && mkdir -p "$APP_ROOT/v1/audio" "$APP_ROOT/v1/tts_cache"

chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
# Caddy 以 caddy 用户读静态文件，需要目录可进入、文件可读
chmod 755 "$APP_ROOT" "$APP_ROOT/shared" "$APP_ROOT/shared/web"
find "$APP_ROOT/shared/web" -type d -exec chmod 755 {} \;
ok "属主 $APP_USER，静态目录 755"

# vps.env 含明文密钥，收紧权限
chmod 600 "$ENV_FILE" 2>/dev/null || true

# ── systemd 服务 ─────────────────────────────────────────────────────────
write_service() {
  local ver="$1" port="$2" desc="$3"
  cat > "/etc/systemd/system/dictation-$ver.service" <<EOF
[Unit]
Description=$desc
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_ROOT/$ver
EnvironmentFile=/etc/dictation/$ver.env
ExecStart=$APP_ROOT/$ver/venv/bin/uvicorn main:app --host 127.0.0.1 --port $port
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_ROOT/$ver $APP_ROOT/shared/web/audio

[Install]
WantedBy=multi-user.target
EOF
  ok "dictation-$ver.service"
}

step "安装 systemd 服务"
SERVICES=()
if [[ "$DEPLOY_V1" == "yes" ]]; then
  write_service v1 "$V1_PORT" "Dictation V1 API (runtime TTS)"
  SERVICES+=("dictation-v1")
fi
if [[ "$DEPLOY_V2" == "yes" ]]; then
  write_service v2 "$V2_PORT" "Dictation V2 API (pre-recorded slices)"
  SERVICES+=("dictation-v2")
fi

systemctl daemon-reload
systemctl enable --now "${SERVICES[@]}" >/dev/null 2>&1
sleep 3
for s in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$s"; then
    ok "$s 运行中"
  else
    warn "$s 未启动，诊断：journalctl -u $s -n 30 --no-pager"
  fi
done

# ── Caddy 配置 ───────────────────────────────────────────────────────────
step "配置 Caddy"

# basic_auth 指令名随版本变化：2.8+ 用 basic_auth，2.7- 用 basicauth
CADDY_MAJOR="$(echo "${CADDY_VER#v}" | cut -d. -f1)"
CADDY_MINOR="$(echo "${CADDY_VER#v}" | cut -d. -f2)"
if [[ "$CADDY_MAJOR" -gt 2 ]] || { [[ "$CADDY_MAJOR" -eq 2 ]] && [[ "$CADDY_MINOR" -ge 8 ]]; }; then
  AUTH_DIRECTIVE="basic_auth"
else
  AUTH_DIRECTIVE="basicauth"
fi

PW_HASH="$(caddy hash-password --plaintext "$BASIC_AUTH_PASSWORD")"
ok "口令哈希已生成（$AUTH_DIRECTIVE）"

[[ -f /etc/caddy/Caddyfile ]] && \
  cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak_$(date +%F_%H%M%S)"

CADDYFILE=/etc/caddy/Caddyfile
: > "$CADDYFILE"

if [[ "$DEPLOY_V1" == "yes" ]]; then
  cat >> "$CADDYFILE" <<EOF
$V1_DOMAIN {
    $AUTH_DIRECTIVE {
        $BASIC_AUTH_USER $PW_HASH
    }
    encode zstd gzip

    handle /api/* {
        reverse_proxy 127.0.0.1:$V1_PORT {
            transport http {
                dial_timeout 30s
                # V1 首次合成 30 词可能耗时 1-2 分钟
                response_header_timeout 300s
            }
        }
    }

    # V1 实时合成的成品音频
    handle /audio/* {
        root * $APP_ROOT/v1
        header Cache-Control "public, max-age=3600"
        file_server
    }

    handle {
        root * $APP_ROOT/v1/dictation_www
        file_server
    }
}

EOF
fi

if [[ "$DEPLOY_V2" == "yes" ]]; then
  cat >> "$CADDYFILE" <<EOF
$V2_DOMAIN {
    $AUTH_DIRECTIVE {
        $BASIC_AUTH_USER $PW_HASH
    }
    encode zstd gzip

    handle /api/* {
        reverse_proxy 127.0.0.1:$V2_PORT {
            transport http {
                dial_timeout 30s
                response_header_timeout 60s
            }
        }
    }

    # 录音工作台（上传录音、按静音切割）
    handle /studio* {
        reverse_proxy 127.0.0.1:$V2_PORT {
            transport http {
                dial_timeout 30s
                response_header_timeout 300s
            }
        }
    }

    # 预录切片：内容不变，长缓存
    handle /audio/* {
        root * $APP_ROOT/shared/web
        header Cache-Control "public, max-age=604800"
        file_server
    }

    handle {
        root * $APP_ROOT/shared/web
        file_server
    }
}

EOF
fi

caddy validate --config "$CADDYFILE" >/dev/null 2>&1 \
  || die "Caddyfile 校验失败：caddy validate --config $CADDYFILE"
ok "Caddyfile 校验通过"

systemctl reload caddy 2>/dev/null || systemctl restart caddy
ok "Caddy 已重载"

# ── 防火墙 ───────────────────────────────────────────────────────────────
step "配置防火墙"
ufw allow OpenSSH  >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1
ufw allow 80/tcp   >/dev/null 2>&1
ufw allow 443/tcp  >/dev/null 2>&1
ufw allow 443/udp  >/dev/null 2>&1   # HTTP/3
ufw --force enable >/dev/null 2>&1
ok "已放行 22 / 80 / 443（8888、8889 仅回环，不对外）"

# ── 数据库自动备份 ───────────────────────────────────────────────────────
step "安装每日备份"
mkdir -p /var/backups/dictation/v1 /var/backups/dictation/v2

cat > /usr/local/bin/backup-dictation.sh <<EOF
#!/bin/bash
# 由 deploy/vps-install.sh 生成
set -euo pipefail
for v in v1 v2; do
    SRC="$APP_ROOT/\$v/dictation.db"
    DEST="/var/backups/dictation/\$v/dictation_\$(date +%F).db"
    [ -f "\$SRC" ] || continue
    sqlite3 "\$SRC" ".backup '\$DEST'"
    gzip -f "\$DEST"
done
find /var/backups/dictation -name '*.db.gz' -mtime +$BACKUP_KEEP_DAYS -delete
EOF

chmod +x /usr/local/bin/backup-dictation.sh
/usr/local/bin/backup-dictation.sh || warn "首次备份未成功（库可能还是空的）"

if ! crontab -l 2>/dev/null | grep -q backup-dictation; then
  ( crontab -l 2>/dev/null; echo "0 3 * * * /usr/local/bin/backup-dictation.sh" ) | crontab -
fi
ok "每日 3:00 备份，保留 $BACKUP_KEEP_DAYS 天"

# ── 健康检查 ─────────────────────────────────────────────────────────────
step "健康检查"

check_api() {
  local ver="$1" port="$2"
  local body
  body="$(curl -fsS --max-time 15 "http://127.0.0.1:$port/api/lessons" 2>/dev/null || echo "")"
  if [[ -z "$body" ]]; then
    warn "$ver /api/lessons 无响应 —— journalctl -u dictation-$ver -n 30 --no-pager"
    return 1
  fi
  local n
  n="$(printf '%s' "$body" | grep -o '"lesson_seq"' | wc -l | tr -d ' ')"
  if [[ "$n" -gt 0 ]]; then
    ok "$ver 接口正常，返回 $n 门课程"
  else
    warn "$ver 接口有响应但课程数为 0（数据库可能未灌题库）"
  fi
}

[[ "$DEPLOY_V1" == "yes" ]] && check_api v1 "$V1_PORT" || true
[[ "$DEPLOY_V2" == "yes" ]] && check_api v2 "$V2_PORT" || true

# V2 音频切片
if [[ "$DEPLOY_V2" == "yes" ]]; then
  SLICES="$(find "$APP_ROOT/shared/web/audio" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$SLICES" -gt 100 ]]; then
    ok "音频切片 $SLICES 个"
  else
    warn "音频切片只有 $SLICES 个 —— V2 将没有声音"
    warn "  请在本地生成后上传（见 DEPLOY-vps.md 第 1、5 步）："
    warn "  rsync -avz shared/web/audio/ root@本机IP:$APP_ROOT/shared/web/audio/"
  fi
fi

# ── 完成 ─────────────────────────────────────────────────────────────────
echo
echo "${C_HEAD}============================================================${C_OFF}"
echo "${C_OK}  部署完成${C_OFF}"
echo "${C_HEAD}============================================================${C_OFF}"
echo
[[ "$DEPLOY_V1" == "yes" ]] && echo "  V1  https://$V1_DOMAIN"
[[ "$DEPLOY_V2" == "yes" ]] && echo "  V2  https://$V2_DOMAIN"
echo "  账号  $BASIC_AUTH_USER / (deploy/vps.env 中设置的密码)"
echo
echo "  首次访问时 Caddy 会自动申请证书，观察签发："
echo "    journalctl -u caddy -f"
echo
if [[ "$DEPLOY_V2" == "yes" ]] && [[ "${SLICES:-0}" -le 100 ]]; then
  echo "${C_WARN}  待办：上传音频切片，否则 V2 无声音${C_OFF}"
  echo "    本地执行 python shared/gen_slices.py 后 rsync 上传"
  echo
fi
if [[ "$DEPLOY_V1" == "yes" ]]; then
  echo "  建议：预热 V1 缓存，避免出题时被有道限流"
  echo "    cd $APP_ROOT && set -a && . /etc/dictation/v1.env && set +a"
  echo "    v1/venv/bin/python shared/tools/warm_v1_cache.py"
  echo "    chown -R $APP_USER:$APP_USER v1/tts_cache"
  echo
fi
echo "  运维：systemctl status ${SERVICES[*]} --no-pager"
echo
