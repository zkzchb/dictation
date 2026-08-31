#!/usr/bin/env bash
# Synchronize canonical MP3 files between dictation-content and a V2 VPS.
# A direction is required so recordings are never overwritten by accident.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_OFF=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$C_HEAD" "$*" "$C_OFF"; }
ok() { printf '%s  [OK]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s  [!] %s%s\n' "$C_WARN" "$C_OFF" "$*"; }
die() { printf '%s  [X] %s%s\n' "$C_ERR" "$*" "$C_OFF" >&2; exit 1; }

MODE=""
TARGET=""
DRY_RUN=no
CONTENT_ROOT="../dictation-content/packs/zh-cn/primary-3a"
REMOTE_PROGRAM_ROOT="/opt/dictation"
REMOTE_CONTENT_ROOT="/opt/dictation-content/packs/zh-cn/primary-3a"
REMOTE_STATE_ROOT="/var/lib/dictation"
APP_USER="dictation"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull) MODE=pull; shift ;;
    --push) MODE=push; shift ;;
    --dry-run) DRY_RUN=yes; shift ;;
    --content-root) CONTENT_ROOT="${2-}"; shift 2 ;;
    --remote-program-root) REMOTE_PROGRAM_ROOT="${2-}"; shift 2 ;;
    --remote-content-root) REMOTE_CONTENT_ROOT="${2-}"; shift 2 ;;
    --remote-state-root) REMOTE_STATE_ROOT="${2-}"; shift 2 ;;
    --user) APP_USER="${2-}"; shift 2 ;;
    -h|--help)
      printf '%s\n' \
        'Usage:' \
        '  bash deploy/sync-slices.sh --pull root@host [options]' \
        '  bash deploy/sync-slices.sh --push root@host [options]' \
        '' \
        '--pull imports VPS recordings into dictation-content and refreshes hashes.' \
        '--push deploys the canonical content audio to the VPS runtime.'
      exit 0 ;;
    -*) die "未知参数: $1" ;;
    *) [[ -z "$TARGET" ]] || die "只能指定一个远端主机"; TARGET="$1"; shift ;;
  esac
done

[[ "$MODE" == "pull" || "$MODE" == "push" ]] || die "必须明确指定 --pull 或 --push"
[[ -n "$TARGET" ]] || die "缺少远端主机，例如 root@203.0.113.10"
[[ "$TARGET" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || die "远端主机格式不安全"
for remote_path in "$REMOTE_PROGRAM_ROOT" "$REMOTE_CONTENT_ROOT" "$REMOTE_STATE_ROOT"; do
  [[ "$remote_path" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "远端路径包含不安全字符: $remote_path"
done
[[ "$APP_USER" =~ ^[A-Za-z0-9._-]+$ ]] || die "远端运行用户格式不安全"

[[ "$CONTENT_ROOT" == /* ]] || CONTENT_ROOT="$REPO_ROOT/$CONTENT_ROOT"
CONTENT_ROOT="$(realpath -m -- "$CONTENT_ROOT")"
LOCAL_AUDIO="$CONTENT_ROOT/tts"
AUDIO_TOOL="$REPO_ROOT/shared/tools/audio_bundle.py"
REMOTE_AUDIO="$REMOTE_STATE_ROOT/web/audio"

command -v rsync >/dev/null 2>&1 || die "缺少 rsync"
command -v ssh >/dev/null 2>&1 || die "缺少 ssh"
python3 "$REPO_ROOT/shared/content_pack.py" "$CONTENT_ROOT" >/dev/null \
  || die "本地内容包无效"

step "检查 SSH 与远端目录"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" \
  "test -f '$REMOTE_PROGRAM_ROOT/shared/tools/audio_bundle.py' && test -d '$REMOTE_AUDIO'"
ok "远端程序和运行音频目录存在"

RSYNC=(-avz --progress --include='/w/***' --include='/sys/***' --exclude='*')
[[ "$DRY_RUN" == "yes" ]] && RSYNC+=(--dry-run)

if [[ "$MODE" == "pull" ]]; then
  step "验证远端运行音频"
  ssh "$TARGET" \
    "DICTATION_CONTENT_ROOT='$REMOTE_CONTENT_ROOT' python3 '$REMOTE_PROGRAM_ROOT/shared/tools/audio_bundle.py' inventory --audio-dir '$REMOTE_AUDIO' >/dev/null"
  ok "远端音频与远端内容包一致"

  if [[ "$DRY_RUN" != "yes" ]]; then
    BACKUP="$CONTENT_ROOT/tts.backup-$(date +%Y%m%d-%H%M%S)"
    cp -a "$LOCAL_AUDIO" "$BACKUP"
    ok "已创建可恢复备份: $BACKUP"
  fi
  step "从 VPS 导入 MP3"
  rsync "${RSYNC[@]}" "$TARGET:$REMOTE_AUDIO/" "$LOCAL_AUDIO/"
  if [[ "$DRY_RUN" == "yes" ]]; then
    ok "试运行结束，未修改本地内容"
    exit 0
  fi
  DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
    python3 "$AUDIO_TOOL" build-dataset --content-root "$CONTENT_ROOT"
  DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
    python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT"
  ok "录音已导入内容包；请人工试听、提交并发布新的内容版本"
  exit 0
fi

step "验证本地内容音频"
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT"
step "把内容包 MP3 部署到 VPS"
rsync "${RSYNC[@]}" "$LOCAL_AUDIO/" "$TARGET:$REMOTE_AUDIO/"
if [[ "$DRY_RUN" == "yes" ]]; then
  ok "试运行结束，未修改远端"
  exit 0
fi
ssh "$TARGET" "
  set -e
  chown -R '$APP_USER:$APP_USER' '$REMOTE_AUDIO'
  DICTATION_CONTENT_ROOT='$REMOTE_CONTENT_ROOT' \
    python3 '$REMOTE_PROGRAM_ROOT/shared/tools/audio_bundle.py' \
    inventory --audio-dir '$REMOTE_AUDIO' >/dev/null
  systemctl restart dictation-v2
"
ok "远端音频通过校验，V2 已重启"
