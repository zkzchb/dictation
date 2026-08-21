#!/usr/bin/env bash
# Dictation V2 interactive VPS installer shared by the online and offline entrypoints.

set -Eeuo pipefail
IFS=$'\n\t'

readonly DEPENDENCY_SOURCE="${1:-}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

C_OK=$'\033[32m'
C_WARN=$'\033[33m'
C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'
C_OFF=$'\033[0m'

step() { printf '\n%s==> %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '%s  [OK]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s  [!] %s%s\n' "$C_WARN" "$C_OFF" "$*"; }
die() { printf '%s  [X] %s%s\n' "$C_ERR" "$*" "$C_OFF" >&2; exit 1; }

prompt_value() {
  local __name="$1" label="$2" default_value="$3" answer
  [[ -r /dev/tty ]] || die "当前不是交互式终端，请直接在 SSH/Xshell 中运行"
  if [[ -n "$default_value" ]]; then
    read -r -p "$label [$default_value]: " answer </dev/tty
    printf -v "$__name" '%s' "${answer:-$default_value}"
  else
    read -r -p "$label（可留空）: " answer </dev/tty
    printf -v "$__name" '%s' "$answer"
  fi
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
    read -r -s -p "网页访问密码: " first </dev/tty
    printf '\n'
    [[ -n "$first" ]] || { warn "密码不能为空"; continue; }
    read -r -s -p "再次输入密码: " second </dev/tty
    printf '\n'
    [[ "$first" == "$second" ]] || { warn "两次输入不一致"; continue; }
    AUTH_PASSWORD="$first"
    return 0
  done
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

validate_domain() {
  [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] \
    || die "域名格式不正确：$1"
}

validate_ip() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] \
    || die "当前安装器只接受 IPv4 地址：$1"
  local part
  IFS='.' read -r -a parts <<< "$1"
  for part in "${parts[@]}"; do
    (( 10#$part <= 255 )) || die "IPv4 地址不正确：$1"
  done
  IFS=$'\n\t'
}

write_env_value() {
  local key="$1" value="$2"
  printf '%s=%q\n' "$key" "$value"
}

[[ "$DEPENDENCY_SOURCE" == "online" || "$DEPENDENCY_SOURCE" == "offline" ]] \
  || die "内部调用错误：依赖来源必须是 online 或 offline"
[[ $EUID -eq 0 ]] || die "请以 root 身份运行安装脚本"

step "设置访问入口"
PUBLIC_IP="${DICTATION_PUBLIC_IP:-}"
PRIMARY_DOMAIN="${DICTATION_PRIMARY_DOMAIN:-}"
BACKUP_DOMAINS="${DICTATION_BACKUP_DOMAINS:-}"
ENABLE_HTTPS="${DICTATION_ENABLE_HTTPS:-}"

[[ -n "$PUBLIC_IP" ]] || prompt_value PUBLIC_IP "公网 IPv4；仅用域名时可留空" ""
[[ -n "$PRIMARY_DOMAIN" ]] || prompt_value PRIMARY_DOMAIN "主域名；仅用 IP 时可留空" ""
[[ -n "$BACKUP_DOMAINS" ]] || prompt_value BACKUP_DOMAINS "备用域名，多个用英文逗号分隔" ""

[[ -z "$PUBLIC_IP" ]] || validate_ip "$PUBLIC_IP"
[[ -z "$PRIMARY_DOMAIN" ]] || validate_domain "$PRIMARY_DOMAIN"

DOMAINS=()
[[ -z "$PRIMARY_DOMAIN" ]] || DOMAINS+=("$PRIMARY_DOMAIN")
if [[ -n "$BACKUP_DOMAINS" ]]; then
  IFS=',' read -r -a raw_domains <<< "$BACKUP_DOMAINS"
  IFS=$'\n\t'
  for domain in "${raw_domains[@]}"; do
    domain="$(trim "$domain")"
    [[ -z "$domain" ]] && continue
    validate_domain "$domain"
    DOMAINS+=("$domain")
  done
fi

if (( ${#DOMAINS[@]} > 0 )); then
  if [[ -z "$ENABLE_HTTPS" ]]; then
    if prompt_yes_no "域名现在是否已具备备案/DNS/80、443 端口条件，立即启用 HTTPS？" yes; then
      ENABLE_HTTPS=yes
    else
      ENABLE_HTTPS=no
    fi
  fi
else
  ENABLE_HTTPS=no
fi
ENABLE_HTTPS="${ENABLE_HTTPS,,}"
[[ "$ENABLE_HTTPS" == "yes" || "$ENABLE_HTTPS" == "no" ]] \
  || die "DICTATION_ENABLE_HTTPS 只能是 yes 或 no"

SITE_ADDRESSES=()
[[ -z "$PUBLIC_IP" ]] || SITE_ADDRESSES+=("http://$PUBLIC_IP")
if [[ "$ENABLE_HTTPS" == "yes" ]]; then
  SITE_ADDRESSES+=("${DOMAINS[@]}")
fi
(( ${#SITE_ADDRESSES[@]} > 0 )) \
  || die "没有可启用的访问入口；暂不启用域名 HTTPS 时必须填写公网 IP"

V2_SITE_ADDRESSES="$(IFS=', '; printf '%s' "${SITE_ADDRESSES[*]}")"
PENDING_DOMAINS=""
if [[ "$ENABLE_HTTPS" == "no" && ${#DOMAINS[@]} -gt 0 ]]; then
  PENDING_DOMAINS="$(IFS=','; printf '%s' "${DOMAINS[*]}")"
  warn "本次只启用 IP HTTP；备案完成后重跑本脚本并选择启用 HTTPS"
fi

step "设置访问保护"
AUTH_USER="${DICTATION_AUTH_USER:-}"
AUTH_PASSWORD="${DICTATION_AUTH_PASSWORD:-}"
[[ -n "$AUTH_USER" ]] || prompt_value AUTH_USER "网页访问用户名" "dictation"
[[ "$AUTH_USER" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "用户名只能包含字母、数字、点、下划线和连字符"
[[ -n "$AUTH_PASSWORD" ]] || prompt_password
if [[ ${#AUTH_PASSWORD} -lt 12 ]]; then
  warn "当前密码不足 12 位，只适合作为临时访问门槛"
fi

STUDIO_ENABLED="${DICTATION_STUDIO_ENABLED:-}"
if [[ -z "$STUDIO_ENABLED" ]]; then
  if prompt_yes_no "是否启用 V2 录音工作台？" no; then
    STUDIO_ENABLED=1
  else
    STUDIO_ENABLED=0
  fi
fi
[[ "$STUDIO_ENABLED" == "0" || "$STUDIO_ENABLED" == "1" ]] \
  || die "DICTATION_STUDIO_ENABLED 只能是 0 或 1"

printf '\n即将安装：\n'
printf '  版本：Dictation V2\n'
printf '  依赖来源：%s\n' "$DEPENDENCY_SOURCE"
printf '  安装目录：%s\n' "$REPO_ROOT"
printf '  当前入口：%s\n' "$V2_SITE_ADDRESSES"
if [[ -n "$PENDING_DOMAINS" ]]; then printf '  待备案域名：%s\n' "$PENDING_DOMAINS"; fi
printf '  访问用户：%s\n' "$AUTH_USER"
printf '  录音工作台：%s\n' "$([[ "$STUDIO_ENABLED" == "1" ]] && echo 开启 || echo 关闭)"

if [[ "${DICTATION_ASSUME_YES:-0}" != "1" ]]; then
  prompt_yes_no "确认继续？" yes || die "用户取消安装"
fi

step "写入部署配置"
ENV_FILE="$SCRIPT_DIR/vps.env"
if [[ -f "$ENV_FILE" ]]; then
  ENV_BACKUP="${ENV_FILE}.bak_$(date +%Y%m%d-%H%M%S)"
  cp -a -- "$ENV_FILE" "$ENV_BACKUP"
  chmod 600 "$ENV_BACKUP"
  ok "原配置已备份到 $ENV_BACKUP"
fi
{
  printf '# Generated by install-v2-common.sh\n'
  printf 'DEPLOY_V1=no\nDEPLOY_V2=yes\n'
  write_env_value V2_SITE_ADDRESSES "$V2_SITE_ADDRESSES"
  write_env_value V2_PENDING_DOMAINS "$PENDING_DOMAINS"
  write_env_value V2_DEPENDENCY_SOURCE "$DEPENDENCY_SOURCE"
  write_env_value BASIC_AUTH_USER "$AUTH_USER"
  write_env_value BASIC_AUTH_PASSWORD "$AUTH_PASSWORD"
  printf 'V2_AUDIO_SOURCE=repository\n'
  printf 'CONTENT_ROOT=chinese/3a\n'
  printf 'V2_PORT=8889\n'
  write_env_value APP_ROOT "$REPO_ROOT"
  printf 'APP_USER=dictation\n'
  printf 'BACKUP_KEEP_DAYS=30\n'
  printf 'APP_TIMEZONE=Asia/Shanghai\n'
  printf 'STUDIO_ENABLED=%s\n' "$STUDIO_ENABLED"
  printf 'V2_HUMAN_BUNDLE=\nV2_HUMAN_BUNDLE_SHA256=\n'
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok "$ENV_FILE（权限 600）"

step "运行公共部署核心"
bash "$SCRIPT_DIR/vps-install.sh"

printf '\n%sDictation V2 安装完成%s\n' "$C_OK" "$C_OFF"
printf '访问入口：%s\n' "$V2_SITE_ADDRESSES"
if [[ -n "$PENDING_DOMAINS" ]]; then
  printf '%s备案完成后重新运行同一入口脚本，即可启用域名 HTTPS。%s\n' "$C_WARN" "$C_OFF"
fi

