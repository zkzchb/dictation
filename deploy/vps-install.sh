#!/usr/bin/env bash
# Idempotent Dictation V2 installer for an Ubuntu VPS.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ENV_FILE="$SCRIPT_DIR/vps.env"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_OFF=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '%s  [OK]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s  [!] %s%s\n' "$C_WARN" "$C_OFF" "$*"; }
die() { printf '%s  [X] %s%s\n' "$C_ERR" "$*" "$C_OFF" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请以 root 身份运行：sudo bash deploy/vps-install.sh"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE；先运行 deploy/install-v2-online.sh 或复制示例"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${V2_PORT:=8889}"
DETECTED_SSH_PORT="${SSH_CONNECTION-}"
if [[ "$DETECTED_SSH_PORT" == *" "* ]]; then
  DETECTED_SSH_PORT="${DETECTED_SSH_PORT##* }"
else
  DETECTED_SSH_PORT=22
fi
: "${SSH_PORT:=$DETECTED_SSH_PORT}"
: "${APP_ROOT:=/opt/dictation}"
: "${CONTENT_ROOT:=/opt/dictation-content/packs/zh-cn/primary-3a}"
: "${STATE_ROOT:=/var/lib/dictation}"
: "${APP_USER:=dictation}"
: "${BACKUP_KEEP_DAYS:=30}"
: "${APP_TIMEZONE:=Asia/Shanghai}"
: "${STUDIO_ENABLED:=0}"
: "${V2_DEPENDENCY_SOURCE:=online}"

check_value() {
  local name="$1" value="${2-}"
  [[ -n "$value" ]] || die "$name 未填写"
  case "$value" in REPLACE_WITH*) die "$name 仍是占位符" ;; esac
}

check_value V2_SITE_ADDRESSES "${V2_SITE_ADDRESSES-}"
check_value BASIC_AUTH_USER "${BASIC_AUTH_USER-}"
check_value BASIC_AUTH_PASSWORD "${BASIC_AUTH_PASSWORD-}"
[[ "$BASIC_AUTH_USER" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "BASIC_AUTH_USER 只能包含字母、数字、点、下划线和连字符"
[[ "$APP_USER" =~ ^[A-Za-z0-9._-]+$ ]] || die "APP_USER 格式不安全"
[[ "$V2_SITE_ADDRESSES" =~ ^[A-Za-z0-9.:/,_-]+$ ]] \
  || die "V2_SITE_ADDRESSES 包含不安全字符"
[[ "$APP_TIMEZONE" =~ ^[A-Za-z0-9._+-]+/[A-Za-z0-9._+-]+$ ]] \
  || die "APP_TIMEZONE 格式不安全"
[[ ${#BASIC_AUTH_PASSWORD} -ge 12 ]] || warn "访问密码少于 12 位"
[[ "$V2_DEPENDENCY_SOURCE" == "online" || "$V2_DEPENDENCY_SOURCE" == "offline" ]] \
  || die "V2_DEPENDENCY_SOURCE 只能是 online 或 offline"
[[ "$STUDIO_ENABLED" == "0" || "$STUDIO_ENABLED" == "1" ]] \
  || die "STUDIO_ENABLED 只能是 0 或 1"
[[ "$V2_PORT" =~ ^[0-9]+$ ]] && (( V2_PORT >= 1 && V2_PORT <= 65535 )) \
  || die "V2_PORT 必须是 1..65535"
[[ "$SSH_PORT" =~ ^[0-9]+$ ]] && (( SSH_PORT >= 1 && SSH_PORT <= 65535 )) \
  || die "SSH_PORT 必须是 1..65535"
[[ "$APP_ROOT" == /* && "$CONTENT_ROOT" == /* && "$STATE_ROOT" == /* ]] \
  || die "APP_ROOT、CONTENT_ROOT 与 STATE_ROOT 必须是绝对路径"
for path_value in "$APP_ROOT" "$CONTENT_ROOT" "$STATE_ROOT"; do
  [[ "$path_value" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || die "生产路径只能包含字母、数字、点、下划线、连字符和斜杠: $path_value"
done
[[ "$REPO_ROOT" == "$APP_ROOT" ]] \
  || die "程序检出位于 $REPO_ROOT，但 APP_ROOT 配置为 $APP_ROOT"

WEB_ROOT="$STATE_ROOT/web"
DB_PATH="$STATE_ROOT/v2/dictation.db"
AUDIO_TOOL="$APP_ROOT/shared/tools/audio_bundle.py"
VENV="$APP_ROOT/v2/venv"

step "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip sqlite3 ffmpeg curl ufw rsync cron ca-certificates passwd
if ! command -v caddy >/dev/null 2>&1; then
  if ! apt-get install -y -qq caddy; then
    apt-get install -y -qq software-properties-common
    add-apt-repository -y universe
    apt-get update -qq
    apt-get install -y -qq caddy
  fi
fi
ok "Ubuntu 运行依赖已就绪"

step "创建运行用户与状态目录"
if ! id "$APP_USER" >/dev/null 2>&1; then
  adduser --system --group --home "$STATE_ROOT" --shell /usr/sbin/nologin "$APP_USER"
else
  account_record="$(getent passwd "$APP_USER")" \
    || die "无法读取现有运行账户: $APP_USER"
  IFS=: read -r account_name _ account_uid account_gid _ account_home account_shell \
    <<< "$account_record"
  [[ "$account_name" == "$APP_USER" ]] || die "运行账户记录异常: $APP_USER"
  [[ "$account_uid" =~ ^[0-9]+$ ]] && ((account_uid < 1000)) \
    || die "拒绝把普通登录账户用作运行账户: $APP_USER"
  case "$account_shell" in
    /usr/sbin/nologin|/sbin/nologin|/bin/false|/usr/bin/false) ;;
    *) die "运行账户必须禁止登录: $APP_USER" ;;
  esac
  account_group_gid="$(getent group "$APP_USER" 2>/dev/null | cut -d: -f3 || true)"
  [[ "$account_group_gid" == "$account_gid" ]] \
    || die "运行账户必须使用同名主组: $APP_USER"
  case "$account_home" in
    "$APP_ROOT"|"$STATE_ROOT") ;;
    *) die "运行账户 home 不属于程序或状态目录: $APP_USER" ;;
  esac
  if [[ "$account_home" != "$STATE_ROOT" ]]; then
    usermod --home "$STATE_ROOT" "$APP_USER"
  fi
fi
mkdir -p "$STATE_ROOT/v2" "$WEB_ROOT" /etc/dictation
chmod 755 "$STATE_ROOT" "$STATE_ROOT/v2" "$WEB_ROOT" /etc/dictation
ok "运行用户: $APP_USER；状态目录: $STATE_ROOT"

step "验证程序与外部内容包"
python3 "$APP_ROOT/shared/content_pack.py" "$CONTENT_ROOT" \
  || die "内容包无效: $CONTENT_ROOT"
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT" \
  || die "内容包音频未通过校验"
python3 -m compileall -q "$APP_ROOT/v2" "$APP_ROOT/shared"
ok "程序和内容包通过静态检查"

step "安装 V2 Python 依赖"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
if [[ "$V2_DEPENDENCY_SOURCE" == "offline" ]]; then
  [[ -f "$APP_ROOT/shared/tools/verify_wheelhouse.py" ]] \
    || die "当前分支没有离线 wheelhouse 校验工具"
  "$VENV/bin/python" "$APP_ROOT/shared/tools/verify_wheelhouse.py" \
    "$APP_ROOT/v2/wheelhouse"
  "$VENV/bin/pip" install --quiet --no-index \
    --find-links "$APP_ROOT/v2/wheelhouse" -r "$APP_ROOT/v2/requirements.txt"
else
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$APP_ROOT/v2/requirements.txt"
fi
"$VENV/bin/pip" check >/dev/null
ok "V2 Python 依赖一致"

step "迁移旧运行状态（如存在）"
if [[ ! -f "$DB_PATH" && -f "$APP_ROOT/v2/dictation.db" ]]; then
  install -m 0640 "$APP_ROOT/v2/dictation.db" "$DB_PATH"
  ok "已复制旧数据库到独立状态目录"
fi
if [[ ! -d "$WEB_ROOT/audio" && -d "$APP_ROOT/shared/web/audio" ]]; then
  cp -a "$APP_ROOT/shared/web/audio" "$WEB_ROOT/audio"
  ok "已复制旧音频与录音台账到独立状态目录"
fi

step "同步数据库与运行时静态资源"
if [[ -f "$DB_PATH" ]]; then
  "$VENV/bin/python" "$APP_ROOT/shared/tools/migrate_poly_ids.py" "$DB_PATH" >/dev/null
  "$VENV/bin/python" "$APP_ROOT/shared/sync_content.py" \
    --db "$DB_PATH" --content-root "$CONTENT_ROOT"
else
  "$VENV/bin/python" "$APP_ROOT/shared/init_db.py" \
    --db "$DB_PATH" --content-root "$CONTENT_ROOT"
fi
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  "$VENV/bin/python" "$APP_ROOT/tools/stage.py" v2 \
    --content-root "$CONTENT_ROOT" --web-root "$WEB_ROOT"
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  "$VENV/bin/python" "$AUDIO_TOOL" inventory --audio-dir "$WEB_ROOT/audio" >/dev/null
cp "$WEB_ROOT/deployment.json" "$STATE_ROOT/deployment.json"
chown -R "$APP_USER:$APP_USER" "$STATE_ROOT"
find "$WEB_ROOT" -type d -exec chmod 755 {} \;
ok "数据库、前端和音频已经就位"

step "写入 V2 运行环境"
cat > /etc/dictation/v2.env <<EOF
DICTATION_CONTENT_ROOT=$CONTENT_ROOT
DICTATION_DB=$DB_PATH
WEB_ROOT=$WEB_ROOT
STUDIO_AUDIO_DIR=$WEB_ROOT/audio/w
STUDIO_ENABLED=$STUDIO_ENABLED
APP_TIMEZONE=$APP_TIMEZONE
EOF
chmod 600 /etc/dictation/v2.env
chown root:root /etc/dictation/v2.env
ok "/etc/dictation/v2.env"

step "安装 systemd 服务"
cat > /etc/systemd/system/dictation-v2.service <<EOF
[Unit]
Description=Dictation V2 API
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_ROOT/v2
EnvironmentFile=/etc/dictation/v2.env
ExecStart=$VENV/bin/uvicorn main:app --host 127.0.0.1 --port $V2_PORT
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_ROOT

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable dictation-v2 >/dev/null
systemctl restart dictation-v2
sleep 3
systemctl is-active --quiet dictation-v2 \
  || die "V2 未启动；运行 journalctl -u dictation-v2 -n 50 --no-pager 排查"
ok "dictation-v2 运行中"

step "配置 Caddy"
CADDY_VERSION="$(caddy version | awk '{print $1}')"
CADDY_MAJOR="$(printf '%s' "${CADDY_VERSION#v}" | cut -d. -f1)"
CADDY_MINOR="$(printf '%s' "${CADDY_VERSION#v}" | cut -d. -f2)"
if (( CADDY_MAJOR > 2 || (CADDY_MAJOR == 2 && CADDY_MINOR >= 8) )); then
  AUTH_DIRECTIVE=basic_auth
else
  AUTH_DIRECTIVE=basicauth
fi
PASSWORD_HASH="$(caddy hash-password --plaintext "$BASIC_AUTH_PASSWORD")"
[[ -f /etc/caddy/Caddyfile ]] \
  && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak_$(date +%F_%H%M%S)"
cat > /etc/caddy/Caddyfile <<EOF
$V2_SITE_ADDRESSES {
    $AUTH_DIRECTIVE {
        $BASIC_AUTH_USER $PASSWORD_HASH
    }
    encode zstd gzip
    request_body {
        max_size 48MB
    }
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
        Permissions-Policy "microphone=(self)"
    }

    handle /api/* {
        reverse_proxy 127.0.0.1:$V2_PORT
    }
    handle /studio* {
        reverse_proxy 127.0.0.1:$V2_PORT {
            transport http {
                response_header_timeout 300s
            }
        }
    }

    @audioState path /audio/.recorded.json /audio/.checked.json /audio/.rerecord.json /audio/.recorded_sys.json /audio/*.bak /audio/*.part /audio/w/*.part /audio/sys/*.part
    respond @audioState 404

    handle /audio/* {
        root * $WEB_ROOT
        header Cache-Control "private, no-store"
        file_server
    }
    handle {
        root * $WEB_ROOT
        file_server
    }
}
EOF
caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl reload caddy 2>/dev/null || systemctl restart caddy
ok "Caddy 配置有效"

step "配置防火墙"
ufw allow "$SSH_PORT/tcp" >/dev/null 2>&1
ufw allow 80/tcp >/dev/null 2>&1
ufw allow 443/tcp >/dev/null 2>&1
ufw allow 443/udp >/dev/null 2>&1
ufw --force enable >/dev/null 2>&1
ok "仅开放 SSH、HTTP 与 HTTPS"

step "安装每日备份"
mkdir -p /var/backups/dictation/v2 /var/backups/dictation/audio
cat > /usr/local/bin/backup-dictation.sh <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
sqlite3 "$DB_PATH" ".backup '/var/backups/dictation/v2/dictation_\$(date +%F).db'"
gzip -f "/var/backups/dictation/v2/dictation_\$(date +%F).db"
tar -C "$WEB_ROOT" -czf "/var/backups/dictation/audio/audio_\$(date +%F).tar.gz" audio deployment.json
find /var/backups/dictation -name '*.db.gz' -mtime +$BACKUP_KEEP_DAYS -delete
find /var/backups/dictation/audio -name 'audio_*.tar.gz' -mtime +$BACKUP_KEEP_DAYS -delete
EOF
chmod 755 /usr/local/bin/backup-dictation.sh
/usr/local/bin/backup-dictation.sh
if ! crontab -l 2>/dev/null | grep -q '/usr/local/bin/backup-dictation.sh'; then
  (crontab -l 2>/dev/null || true; printf '0 3 * * * /usr/local/bin/backup-dictation.sh\n') | crontab -
fi
ok "数据库与录音每日备份，保留 $BACKUP_KEEP_DAYS 天"

step "健康检查"
HEALTH="$(curl -fsS --max-time 15 "http://127.0.0.1:$V2_PORT/api/health")" \
  || die "V2 健康检查无响应"
printf '%s' "$HEALTH" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="ok"; assert d["database"]["lessons"]>0; assert d["database"]["knowledge_points"]>0' \
  || die "V2 健康检查内容异常: $HEALTH"
ok "API、数据库和音频通过验收"

printf '\n%sDictation V2 VPS 部署完成%s\n' "$C_OK" "$C_OFF"
printf '  入口: %s\n' "$V2_SITE_ADDRESSES"
printf '  程序: %s\n' "$APP_ROOT"
printf '  内容: %s\n' "$CONTENT_ROOT"
printf '  状态: %s\n' "$STATE_ROOT"
