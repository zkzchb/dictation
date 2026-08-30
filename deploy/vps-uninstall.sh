#!/usr/bin/env bash
# ============================================================================
# 听写小助手 —— 卸载（VPS）
#
#   sudo bash deploy/vps-uninstall.sh
#
# 移除：systemd 服务、Caddy 站点配置、密钥文件、备份 cron、系统用户
# 保留：程序、外部内容包、$STATE_ROOT 运行数据和备份
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/vps.env"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_HEAD=$'\033[1;36m'; C_OFF=$'\033[0m'
step() { echo; echo "${C_HEAD}==> $*${C_OFF}"; }
ok()   { echo "${C_OK}  [OK]${C_OFF} $*"; }
warn() { echo "${C_WARN}  [!] ${C_OFF} $*"; }

[[ $EUID -eq 0 ]] || { echo "请用 root 运行：sudo bash deploy/vps-uninstall.sh" >&2; exit 1; }

APP_ROOT=/opt/dictation
STATE_ROOT=/var/lib/dictation
APP_USER=dictation
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
  : "${APP_ROOT:=/opt/dictation}"
  : "${STATE_ROOT:=/var/lib/dictation}"
  : "${APP_USER:=dictation}"
fi

echo
echo "将移除服务与系统配置，保留程序、内容、运行数据和备份。"
read -r -p "确认继续？输入 yes: " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { echo "已取消"; exit 0; }

step "停止并移除 systemd 服务"
for s in dictation-v2; do
  if systemctl list-unit-files | grep -q "^$s.service"; then
    systemctl disable --now "$s" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$s.service"
    ok "已移除 $s"
  fi
done
systemctl daemon-reload

step "移除 Caddy 站点配置"
if [[ -f /etc/caddy/Caddyfile ]]; then
  cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak_$(date +%F_%H%M%S)"
  : > /etc/caddy/Caddyfile
  ok "Caddyfile 已清空（原文件已备份为 .bak_*）"
  systemctl reload caddy 2>/dev/null || systemctl restart caddy 2>/dev/null || true
fi

step "移除密钥文件"
rm -f /etc/dictation/v2.env
rmdir /etc/dictation 2>/dev/null || true
ok "/etc/dictation 已清理"

step "移除备份 cron"
crontab -l 2>/dev/null | grep -v backup-dictation | crontab - 2>/dev/null || true
rm -f /usr/local/bin/backup-dictation.sh
ok "cron 与备份脚本已移除（历史备份保留在 /var/backups/dictation）"

step "移除系统用户"
if id "$APP_USER" >/dev/null 2>&1; then
  deluser --system "$APP_USER" >/dev/null 2>&1 || true
  ok "已删除用户 $APP_USER"
fi

echo
echo "${C_HEAD}============================================================${C_OFF}"
echo "${C_OK}  卸载完成${C_OFF}"
echo "${C_HEAD}============================================================${C_OFF}"
echo
echo "  仍保留（如需彻底清除请手动删除）："
echo "    程序          $APP_ROOT"
echo "    运行数据      $STATE_ROOT"
echo "    历史备份      /var/backups/dictation"
echo
