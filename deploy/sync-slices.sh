#!/usr/bin/env bash
# ============================================================================
# 听写小助手 —— 在本地与 VPS 之间同步音频切片
#
# 推送（默认，本地 → VPS）：
#   bash deploy/sync-slices.sh root@1.2.3.4
#   bash deploy/sync-slices.sh root@1.2.3.4 --dry-run     # 只看会传什么
#   bash deploy/sync-slices.sh root@1.2.3.4 --path /srv/dictation
#
# 拉回（VPS → 本地）：老师在 VPS 的 /studio 上录完真人音频后，取回本机
#   bash deploy/sync-slices.sh root@1.2.3.4 --pull
#   bash deploy/sync-slices.sh root@1.2.3.4 --pull --dry-run
#
# 在本地（跑过 local-install.sh 的那台）执行。
# 幂等：rsync 增量，只传新增或变化的文件。
#
# 拉回模式默认会先备份本地切片目录，因为真人录音会覆盖同名的 TTS 切片。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
step() { echo; echo "${C_HEAD}==> $*${C_OFF}"; }
ok()   { echo "${C_OK}  [OK]${C_OFF} $*"; }
warn() { echo "${C_WARN}  [!] ${C_OFF} $*"; }
die()  { echo "${C_ERR}  [X] $*${C_OFF}" >&2; exit 1; }

TARGET=""
REMOTE_ROOT="/opt/dictation"
DRY_RUN=no
APP_USER="dictation"
PULL=no
NO_BACKUP=no

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=yes; shift ;;
    --pull)      PULL=yes; shift ;;
    --no-backup) NO_BACKUP=yes; shift ;;
    --path)      REMOTE_ROOT="${2-}"; shift 2 ;;
    --user)      APP_USER="${2-}"; shift 2 ;;
    -h|--help)   sed -n '2,19p' "$0"; exit 0 ;;
    -*)          die "未知参数: $1" ;;
    *)           TARGET="$1"; shift ;;
  esac
done

[[ -n "$TARGET" ]] || die "缺少目标主机。用法：
  bash deploy/sync-slices.sh root@你的VPS_IP           # 推送到 VPS
  bash deploy/sync-slices.sh root@你的VPS_IP --pull    # 从 VPS 拉回"

LOCAL_AUDIO="$REPO_ROOT/shared/web/audio"

command -v rsync >/dev/null 2>&1 || die "缺少 rsync：sudo apt install -y rsync"

# ── 本地检查 ─────────────────────────────────────────────────────────────
step "检查本地切片"
if [[ "$PULL" == "yes" ]]; then
  # 拉回模式：本地目录可以不存在（首次从 VPS 取），自动建
  mkdir -p "$LOCAL_AUDIO"
  COUNT="$(find "$LOCAL_AUDIO" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  ok "本地现有 $COUNT 个（拉回后可能被覆盖）"
else
  [[ -d "$LOCAL_AUDIO" ]] || die "本地没有切片目录 $LOCAL_AUDIO
  请先执行：bash deploy/local-install.sh"

  COUNT="$(find "$LOCAL_AUDIO" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$COUNT" -gt 0 ]] || die "本地切片数为 0，请先执行：bash deploy/local-install.sh"
  if [[ "$COUNT" -lt 500 ]]; then
    warn "只有 $COUNT 个切片，可能未生成完整（正常约 500+）"
  else
    ok "本地切片 $COUNT 个"
  fi
  SIZE="$(du -sh "$LOCAL_AUDIO" 2>/dev/null | awk '{print $1}')"
  ok "总大小 $SIZE"

  [[ -f "$LOCAL_AUDIO/sys/intro.mp3" ]] \
    && ok "开场提示音存在" \
    || warn "缺少 sys/intro.mp3"
fi

# ── 连通性 ───────────────────────────────────────────────────────────────
step "检查 SSH 连通性"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" "true" 2>/dev/null \
  || die "无法免密登录 $TARGET
  请先配置 SSH 密钥：ssh-copy-id $TARGET
  （或先手动 ssh $TARGET 确认能连上）"
ok "可连接 $TARGET"

ssh "$TARGET" "test -d '$REMOTE_ROOT'" 2>/dev/null \
  || die "远端不存在目录 $REMOTE_ROOT
  请先在 VPS 上完成部署（git clone + deploy/vps-install.sh），
  或用 --path 指定实际路径。"
ok "远端目录 $REMOTE_ROOT 存在"

# ── 同步 ─────────────────────────────────────────────────────────────────
RSYNC_FLAGS=(-avz --progress)
[[ "$DRY_RUN" == "yes" ]] && RSYNC_FLAGS+=(--dry-run)

REMOTE_AUDIO="$REMOTE_ROOT/shared/web/audio"

if [[ "$PULL" == "yes" ]]; then
  # ── VPS → 本地 ──────────────────────────────────────────────────────────
  REMOTE_COUNT="$(ssh "$TARGET" "find '$REMOTE_AUDIO' -name '*.mp3' 2>/dev/null | wc -l" | tr -d ' ')"
  [[ "$REMOTE_COUNT" -gt 0 ]] || die "远端切片数为 0，没有可拉回的内容"
  ok "远端切片 $REMOTE_COUNT 个"

  # 真人录音会覆盖同名 TTS 切片（文件名是内容 MD5，同一个词就是同一个名）。
  # 覆盖前留个备份，万一录坏了还能回退。
  if [[ "$DRY_RUN" != "yes" && "$NO_BACKUP" != "yes" && "$COUNT" -gt 0 ]]; then
    BACKUP="$REPO_ROOT/shared/web/audio.bak_$(date +%F_%H%M%S)"
    cp -a "$LOCAL_AUDIO" "$BACKUP"
    ok "已备份本地切片 → $(basename "$BACKUP")"
  fi

  if [[ "$DRY_RUN" == "yes" ]]; then
    step "试运行：从 $TARGET 拉回（不实际写入本地）"
  else
    step "从 $TARGET:$REMOTE_AUDIO/ 拉回到本地"
  fi

  rsync "${RSYNC_FLAGS[@]}" \
    "$TARGET:$REMOTE_AUDIO/" \
    "$LOCAL_AUDIO/"

  if [[ "$DRY_RUN" == "yes" ]]; then
    echo; ok "试运行结束，未改动本地。去掉 --dry-run 即可实际拉回。"; exit 0
  fi
  ok "拉回完成"

  NEW_COUNT="$(find "$LOCAL_AUDIO" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  step "校验"
  if [[ "$NEW_COUNT" == "$REMOTE_COUNT" ]]; then
    ok "本地 $NEW_COUNT 个，与远端一致"
  else
    warn "本地 $NEW_COUNT 个，远端 $REMOTE_COUNT 个 —— 可重跑补齐"
  fi

  # 本地若装了常驻服务，重启让新切片生效（浏览器可能还需强刷）
  if systemctl list-unit-files 2>/dev/null | grep -q '^dictation-local-v2.service'; then
    sudo systemctl restart dictation-local-v2 2>/dev/null \
      && ok "dictation-local-v2 已重启" \
      || warn "重启 dictation-local-v2 失败，可手动执行"
  fi

  echo
  echo "${C_HEAD}============================================================${C_OFF}"
  echo "${C_OK}  已从 VPS 拉回切片${C_OFF}"
  echo "${C_HEAD}============================================================${C_OFF}"
  echo
  echo "  真人录音已取回本地。若要同步到 Cloudflare（V3）："
  echo "    bash deploy/cloudflare-deploy.sh --skip-slices"
  echo
  [[ -n "${BACKUP-}" ]] && {
    echo "  覆盖前的备份：$(basename "$BACKUP")"
    echo "  ${C_DIM}确认新录音无问题后可删除：rm -rf '$BACKUP'${C_OFF}"
    echo
  }
  exit 0
fi

# ── 本地 → VPS ────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "yes" ]]; then
  step "试运行（--dry-run，不实际传输）"
else
  step "同步切片到 $TARGET:$REMOTE_AUDIO/"
fi

# 尾部斜杠很重要：传目录内容而非目录本身
rsync "${RSYNC_FLAGS[@]}" \
  "$LOCAL_AUDIO/" \
  "$TARGET:$REMOTE_AUDIO/"

if [[ "$DRY_RUN" == "yes" ]]; then
  echo
  ok "试运行结束，未改动远端。去掉 --dry-run 即可实际同步。"
  exit 0
fi
ok "传输完成"

# ── 远端收尾：属主 + 重启 + 校验 ──────────────────────────────────────────
step "修正远端属主并重启 V2"
ssh "$TARGET" "
  set -e
  if id '$APP_USER' >/dev/null 2>&1; then
    chown -R '$APP_USER:$APP_USER' '$REMOTE_AUDIO'
    echo '  属主已设为 $APP_USER'
  else
    echo '  [!] 远端无用户 $APP_USER，跳过 chown'
  fi
  find '$REMOTE_AUDIO' -type d -exec chmod 755 {} \; 2>/dev/null || true
  if systemctl list-unit-files 2>/dev/null | grep -q '^dictation-v2.service'; then
    systemctl restart dictation-v2
    echo '  dictation-v2 已重启'
  else
    echo '  [!] 未找到 dictation-v2 服务，跳过重启'
  fi
"
ok "远端收尾完成"

step "校验远端切片"
REMOTE_COUNT="$(ssh "$TARGET" "find '$REMOTE_AUDIO' -name '*.mp3' 2>/dev/null | wc -l" | tr -d ' ')"
if [[ "$REMOTE_COUNT" == "$COUNT" ]]; then
  ok "远端 $REMOTE_COUNT 个，与本地一致"
else
  warn "远端 $REMOTE_COUNT 个，本地 $COUNT 个 —— 数量不一致，可重跑本脚本补齐"
fi

echo
echo "${C_HEAD}============================================================${C_OFF}"
echo "${C_OK}  切片同步完成${C_OFF}"
echo "${C_HEAD}============================================================${C_OFF}"
echo
echo "  验收（把域名和口令换成你的）："
echo "    curl -sI -u '用户名:密码' https://v2.你的域名/audio/sys/intro.mp3 | head -3"
echo "  应返回 200 与 Content-Type: audio/mpeg"
echo
echo "  浏览器打开 https://v2.你的域名 试听一次即可确认。"
echo