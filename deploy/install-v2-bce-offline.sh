#!/usr/bin/env bash
# Dictation V2 RC1 — BCE local-package installer
#
# Upload this script, the matching .tar.gz bundle, and SHA256SUMS to /root,
# then run this script as root. GitHub and PyPI are not accessed during install.

set -Eeuo pipefail
IFS=$'\n\t'

readonly RELEASE_VERSION="v2.0.0-rc.1"
readonly PACKAGE_NAME="dictation-v2.0.0-rc.1-offline.tar.gz"
readonly PACKAGE_ROOT="dictation-v2.0.0-rc.1"
readonly INSTALL_ROOT="/opt/dictation"
readonly PRIMARY_DOMAIN="dictation.net.cn"
readonly BACKUP_DOMAIN="v2.fanqiemiao.com"
readonly DIRECT_IP="106.12.77.224"
readonly DEFAULT_AUTH_USER="dictation"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PACKAGE_PATH="${1:-$SCRIPT_DIR/$PACKAGE_NAME}"
readonly CHECKSUM_FILE="$SCRIPT_DIR/SHA256SUMS"
readonly LOG_FILE="/root/dictation-v2-${RELEASE_VERSION#v}-install-$(date +%Y%m%d-%H%M%S).log"

C_OK=$'\033[32m'
C_WARN=$'\033[33m'
C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'
C_OFF=$'\033[0m'

step() { printf '\n%s==> %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '%s  [OK]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s  [!] %s%s\n' "$C_WARN" "$C_OFF" "$*"; }
die() { printf '%s  [X] %s%s\n' "$C_ERR" "$*" "$C_OFF" >&2; exit 1; }

TEMP_DIR=""
cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

exec > >(tee -a "$LOG_FILE") 2>&1
trap 'rc=$?; printf "\n%s  [X] 安装在第 %s 行失败（退出码 %s）。%s\n" "$C_ERR" "$LINENO" "$rc" "$C_OFF" >&2; printf "日志：%s\n" "$LOG_FILE" >&2; exit "$rc"' ERR

prompt_value() {
  local __name="$1" label="$2" default_value="$3" answer
  [[ -r /dev/tty ]] || die "当前不是交互式终端，请通过 Xshell 直接运行本脚本"
  read -r -p "$label [$default_value]: " answer </dev/tty
  printf -v "$__name" '%s' "${answer:-$default_value}"
}

prompt_yes_no() {
  local label="$1" default_answer="${2:-yes}" answer suffix
  if [[ "$default_answer" == "yes" ]]; then suffix="[Y/n]"; else suffix="[y/N]"; fi
  read -r -p "$label $suffix: " answer </dev/tty
  answer="${answer:-$default_answer}"
  case "${answer,,}" in
    y|yes|是) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_password() {
  local first second
  while true; do
    read -r -s -p "设置网页访问密码（正好 6 位）: " first </dev/tty
    printf '\n'
    [[ ${#first} -eq 6 ]] || { warn "密码必须正好为 6 位，请重新输入"; continue; }
    read -r -s -p "再次输入密码: " second </dev/tty
    printf '\n'
    [[ "$first" == "$second" ]] || { warn "两次输入不一致，请重新输入"; continue; }
    BASIC_PASSWORD="$first"
    return 0
  done
}

validate_auth_user() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] \
    || die "访问用户名只能包含字母、数字、点、下划线和连字符"
}

write_env_value() {
  local key="$1" value="$2"
  printf '%s=%q\n' "$key" "$value"
}

[[ $EUID -eq 0 ]] || die "请以 root 身份运行本脚本"

step "检查本地发行文件"
[[ -f "$PACKAGE_PATH" ]] || die "找不到离线包：$PACKAGE_PATH"
[[ -f "$CHECKSUM_FILE" ]] || die "找不到校验文件：$CHECKSUM_FILE"
EXPECTED_LINE="$(awk -v name="$PACKAGE_NAME" '$2 == name || $2 == "*" name { print; exit }' "$CHECKSUM_FILE")"
[[ -n "$EXPECTED_LINE" ]] || die "$CHECKSUM_FILE 中没有 $PACKAGE_NAME 的校验值"
EXPECTED_SHA="$(printf '%s\n' "$EXPECTED_LINE" | awk '{print $1}')"
ACTUAL_SHA="$(sha256sum "$PACKAGE_PATH" | awk '{print $1}')"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] \
  || die "离线包 SHA-256 不匹配；请重新下载，禁止继续安装"
ok "$PACKAGE_NAME / SHA-256 $ACTUAL_SHA"

step "检查系统"
[[ -r /etc/os-release ]] || die "无法识别操作系统"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] \
  || die "本安装包要求 Ubuntu 24.04；当前为 ${PRETTY_NAME:-未知系统}"
case "$(uname -m)" in
  x86_64|aarch64|arm64) ;;
  *) die "不支持的 CPU 架构：$(uname -m)；离线 wheelhouse 只支持 x86_64/ARM64" ;;
esac
ok "${PRETTY_NAME} / $(uname -m)"
ok "安装日志将写入 $LOG_FILE"

step "解开并验证发行内容"
TEMP_DIR="$(mktemp -d /tmp/dictation-v2-rc1.XXXXXX)"
tar -xzf "$PACKAGE_PATH" -C "$TEMP_DIR"
SOURCE_ROOT="$TEMP_DIR/$PACKAGE_ROOT"
[[ -d "$SOURCE_ROOT" ]] || die "离线包结构错误：缺少 $PACKAGE_ROOT"
[[ -f "$SOURCE_ROOT/RELEASE-METADATA" ]] || die "离线包缺少 RELEASE-METADATA"
grep -Fxq "release=$RELEASE_VERSION" "$SOURCE_ROOT/RELEASE-METADATA" \
  || die "离线包版本与安装器不匹配"
python3 "$SOURCE_ROOT/shared/tools/verify_wheelhouse.py" "$SOURCE_ROOT/v2/wheelhouse"
python3 "$SOURCE_ROOT/shared/tools/audio_bundle.py" inventory \
  --audio-dir "$SOURCE_ROOT/chinese/3a/tts" >/dev/null
bash -n "$SOURCE_ROOT/deploy/vps-install.sh"
ok "wheelhouse、894 个标准音频和部署脚本均通过校验"

step "收集安装设置"
AUTH_USER="${DICTATION_AUTH_USER:-}"
BASIC_PASSWORD="${DICTATION_AUTH_PASSWORD:-}"
[[ -n "$AUTH_USER" ]] || prompt_value AUTH_USER "网页访问用户名" "$DEFAULT_AUTH_USER"
[[ -n "$BASIC_PASSWORD" ]] || prompt_password
validate_auth_user "$AUTH_USER"
[[ ${#BASIC_PASSWORD} -eq 6 ]] || die "网页访问密码必须正好为 6 位"

printf '\n即将安装：\n'
printf '  发行版：%s（预发行验证版）\n' "$RELEASE_VERSION"
printf '  目录：%s\n' "$INSTALL_ROOT"
printf '  当前入口：http://%s\n' "$DIRECT_IP"
printf '  长期域名：https://%s\n' "$PRIMARY_DOMAIN"
printf '  备用域名：https://%s\n' "$BACKUP_DOMAIN"
printf '  用户：%s\n' "$AUTH_USER"
printf '  录音工作台：关闭（录音仍在现有 V2 完成后同步）\n'
printf '  网络边界：不访问 GitHub/PyPI；系统包仍通过 Ubuntu APT 安装\n'

if [[ "${DICTATION_ASSUME_YES:-0}" != "1" ]]; then
  prompt_yes_no "确认继续？" yes || die "用户取消安装"
fi

step "检查域名解析"
for name in "$PRIMARY_DOMAIN" "$BACKUP_DOMAIN"; do
  RESOLVED="$(getent ahostsv4 "$name" | awk '{print $1}' | sort -u | paste -sd, - || true)"
  if [[ ",$RESOLVED," == *",$DIRECT_IP,"* ]]; then
    ok "$name → $RESOLVED"
  elif [[ -n "$RESOLVED" ]]; then
    warn "$name 当前解析为 $RESOLVED，而脚本预期为 $DIRECT_IP"
  else
    warn "$name 暂无 A 记录；IP 入口不受影响"
  fi
done

step "安装发行内容"
if [[ -e "$INSTALL_ROOT" ]]; then
  if [[ -f "$INSTALL_ROOT/RELEASE-METADATA" ]] \
      && grep -Fxq "release=$RELEASE_VERSION" "$INSTALL_ROOT/RELEASE-METADATA"; then
    ok "$INSTALL_ROOT 已是同一发行版，将保留数据库并幂等重跑"
  else
    BACKUP_ROOT="${INSTALL_ROOT}.pre-${RELEASE_VERSION#v}-$(date +%Y%m%d-%H%M%S)"
    warn "$INSTALL_ROOT 已存在，可能是先前中断的 git clone 或其他安装"
    if [[ "${DICTATION_ASSUME_YES:-0}" != "1" ]]; then
      prompt_yes_no "是否将它完整移到 $BACKUP_ROOT 后继续？" yes \
        || die "用户选择保留现有目录，安装停止"
    fi
    mv -- "$INSTALL_ROOT" "$BACKUP_ROOT"
    ok "原目录已可恢复地保存为 $BACKUP_ROOT"
    cp -a -- "$SOURCE_ROOT" "$INSTALL_ROOT"
  fi
else
  cp -a -- "$SOURCE_ROOT" "$INSTALL_ROOT"
fi
ok "发行内容已就位：$INSTALL_ROOT"

step "生成 V2 部署配置"
ENV_FILE="$INSTALL_ROOT/deploy/vps.env"
if [[ -f "$ENV_FILE" ]]; then
  ENV_BACKUP="${ENV_FILE}.bak_$(date +%Y%m%d-%H%M%S)"
  cp -a "$ENV_FILE" "$ENV_BACKUP"
  chmod 600 "$ENV_BACKUP"
  ok "原配置已备份到 $ENV_BACKUP"
fi
{
  printf '# Generated by install-dictation-v2-bce-offline.sh\n'
  printf '# Release: %s\n' "$RELEASE_VERSION"
  printf 'DEPLOY_V1=no\n'
  printf 'DEPLOY_V2=yes\n'
  write_env_value V2_DOMAIN "$PRIMARY_DOMAIN"
  write_env_value BASIC_AUTH_USER "$AUTH_USER"
  write_env_value BASIC_AUTH_PASSWORD "$BASIC_PASSWORD"
  printf 'V2_AUDIO_SOURCE=repository\n'
  printf 'CONTENT_ROOT=chinese/3a\n'
  printf 'V2_PORT=8889\n'
  printf 'APP_ROOT=/opt/dictation\n'
  printf 'APP_USER=dictation\n'
  printf 'BACKUP_KEEP_DAYS=30\n'
  printf 'APP_TIMEZONE=Asia/Shanghai\n'
  printf 'STUDIO_ENABLED=0\n'
  printf 'V2_HUMAN_BUNDLE=\n'
  printf 'V2_HUMAN_BUNDLE_SHA256=\n'
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok "已生成 $ENV_FILE（权限 600）"

step "运行 V2 安装器"
chmod +x "$INSTALL_ROOT/deploy/vps-install.sh"
cd "$INSTALL_ROOT"
bash deploy/vps-install.sh

step "配置两个域名和临时 IP 入口"
CADDYFILE="/etc/caddy/Caddyfile"
[[ -f "$CADDYFILE" ]] || die "安装器未生成 $CADDYFILE"
CADDY_BACKUP="${CADDYFILE}.pre-multi-address_$(date +%Y%m%d-%H%M%S)"
cp -a "$CADDYFILE" "$CADDY_BACKUP"
CADDY_TEMP="$(mktemp /tmp/dictation-caddy.XXXXXX)"
if ! awk \
  -v from="$PRIMARY_DOMAIN {" \
  -v to="$PRIMARY_DOMAIN, $BACKUP_DOMAIN, http://$DIRECT_IP {" '
    !replaced && $0 == from { print to; replaced=1; next }
    { print }
    END { if (!replaced) exit 42 }
  ' "$CADDYFILE" > "$CADDY_TEMP"; then
  rm -f "$CADDY_TEMP"
  die "无法在 Caddyfile 中定位 $PRIMARY_DOMAIN 站点块；备份位于 $CADDY_BACKUP"
fi
install -o root -g root -m 644 "$CADDY_TEMP" "$CADDYFILE"
rm -f "$CADDY_TEMP"
caddy fmt --overwrite "$CADDYFILE"
caddy validate --config "$CADDYFILE" >/dev/null
systemctl reload caddy
ok "已启用：https://$PRIMARY_DOMAIN"
ok "已启用：https://$BACKUP_DOMAIN"
ok "已启用：http://$DIRECT_IP"

step "执行最终验收"
systemctl is-active --quiet dictation-v2 \
  || die "dictation-v2 未运行：journalctl -u dictation-v2 -n 80 --no-pager"
HEALTH_JSON="$(curl -fsS --connect-timeout 5 --max-time 20 http://127.0.0.1:8889/api/health)"
printf '%s' "$HEALTH_JSON" | "$INSTALL_ROOT/v2/venv/bin/python" -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("status") == "ok", d
db = d.get("database", {})
assert db.get("lessons") == 43, d
assert db.get("knowledge_points") == 814, d
print("  [OK] 本地健康检查：43 门课程 / 814 个知识点")
'
python3 "$INSTALL_ROOT/shared/tools/audio_bundle.py" inventory \
  --audio-dir "$INSTALL_ROOT/shared/web/audio" >/dev/null
ok "标准音频校验：869 个词条 + 25 个系统提示音"

IP_ROUTE_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' \
  --connect-timeout 5 --max-time 15 -H "Host: $DIRECT_IP" http://127.0.0.1/ || true)"
case "$IP_ROUTE_STATUS" in
  200|401) ok "IP 的 HTTP 路由已生效（HTTP $IP_ROUTE_STATUS）" ;;
  *) die "IP 的 HTTP 路由验收失败（HTTP ${IP_ROUTE_STATUS:-000}）" ;;
esac

printf '\n%s============================================================%s\n' "$C_OK" "$C_OFF"
printf '%sDictation V2 %s 安装完成%s\n' "$C_OK" "$RELEASE_VERSION" "$C_OFF"
printf '当前入口：http://%s\n' "$DIRECT_IP"
printf '长期域名：https://%s\n' "$PRIMARY_DOMAIN"
printf '备用域名：https://%s\n' "$BACKUP_DOMAIN"
printf '访问用户名：%s\n' "$AUTH_USER"
printf '安装日志：%s\n' "$LOG_FILE"
printf '%s============================================================%s\n' "$C_OK" "$C_OFF"
printf '\n%s注意：IP 入口使用 HTTP，6 位 Basic Auth 口令不会被 TLS 加密，仅作为备案期间的临时门槛。%s\n' "$C_WARN" "$C_OFF"
