#!/usr/bin/env bash
# Run on a trusted local Ubuntu workstation to replace one Dictation VPS.
#
# The remote deployment is not touched until a private snapshot has passed
# archive, checksum, and SQLite integrity checks. If installation or acceptance
# fails after deletion starts, the snapshot is restored automatically.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly PROGRAM_TAG="v2.1.0-rc.1"
readonly PROGRAM_COMMIT="85e84c339faf92b9b4d10b2097cfca0e275828e2"
readonly CONTENT_TAG="content-v1.0.0"
readonly CONTENT_COMMIT="1e79970a34216edde9e31d2156ccd6bc000f8573"
readonly DATASET_SHA256="b8ea48ecfb9302ba4fc05b6dfca32786360db111d76df90a865e937db6cfc43a"
readonly PACK_ID="chinese-3a"
readonly CONTENT_VERSION="1.0.0"
readonly EXPECTED_LESSONS=43
readonly EXPECTED_PUBLIC_LESSONS=42
readonly EXPECTED_KNOWLEDGE_POINTS=814
readonly EXPECTED_AUDIO_FILES=894
readonly EXPECTED_WORDS=30
readonly EXPECTED_POLYPHONIC=2

REMOTE_HOST=""
REMOTE_USER="root"
REMOTE_PORT="22"
IDENTITY_FILE=""
ENV_FILE=""
LOG_ROOT=""
PREFLIGHT_ONLY=0
VALIDATE_ENV_ONLY=0
ALLOW_CADDY_REPLACE=0

usage() {
  cat <<'EOF'
Usage:
  bash deploy/vps-fresh-redeploy.sh --host HOST --env-file FILE [options]

Required:
  --host HOST              BCE/VPS IPv4, DNS name, or SSH config alias
  --env-file FILE          Private deploy/vps.env-compatible configuration

Options:
  --user USER              SSH user (default: root; otherwise passwordless sudo)
  --port PORT              SSH port (default: 22)
  --identity-file FILE     SSH private key
  --log-dir DIR            Private local log root
  --preflight-only         Run read-only remote checks and stop
  --validate-env-only      Validate FILE locally and stop (HOST is not required)
  --allow-caddy-replace    Permit replacing an unrecognized existing Caddyfile
  -h, --help               Show this help

The full run asks for an exact interactive confirmation. Raw logs stay private
on the workstation and VPS. Only the generated sanitized Markdown report is
suitable for a public repository or Pull Request.
EOF
}

die() {
  printf '[X] %s\n' "$*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

ok() {
  printf '[OK] %s\n' "$*"
}

while (($#)); do
  case "$1" in
    --host) REMOTE_HOST="${2-}"; shift 2 ;;
    --user) REMOTE_USER="${2-}"; shift 2 ;;
    --port) REMOTE_PORT="${2-}"; shift 2 ;;
    --identity-file) IDENTITY_FILE="${2-}"; shift 2 ;;
    --env-file) ENV_FILE="${2-}"; shift 2 ;;
    --log-dir) LOG_ROOT="${2-}"; shift 2 ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    --validate-env-only) VALIDATE_ENV_ONLY=1; shift ;;
    --allow-caddy-replace) ALLOW_CADDY_REPLACE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[[ -n "$ENV_FILE" ]] || die "缺少 --env-file"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "配置必须是普通文件且不能是软链接: $ENV_FILE"

validate_env_file() {
  python3 - "$ENV_FILE" "$REMOTE_PORT" <<'PY'
import os
from pathlib import Path
import re
import shlex
import stat
import sys

path = Path(sys.argv[1])
ssh_port = sys.argv[2]
if not ssh_port.isdigit() or not 1 <= int(ssh_port) <= 65535:
    raise SystemExit("--port 必须是 1..65535")
info = path.stat()
mode = stat.S_IMODE(info.st_mode)
if mode & 0o077:
    raise SystemExit(f"配置权限必须仅限文件所有者，当前为 {mode:04o}；请运行 chmod 600")
if info.st_uid != os.getuid():
    raise SystemExit("配置文件必须由当前本地用户所有")

values = {}
assignment = re.compile(r"^(?:export[ \\t]+)?([A-Z][A-Z0-9_]*)=(.*)$")
for number, original in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    line = original.strip()
    if not line or line.startswith("#"):
        continue
    match = assignment.fullmatch(line)
    if not match:
        raise SystemExit(f"第 {number} 行不是受支持的 KEY=VALUE 格式")
    key, raw = match.groups()
    if "$(" in raw or "`" in raw or "<(" in raw or ">(" in raw:
        raise SystemExit(f"第 {number} 行包含不允许的 Shell 求值语法")
    try:
        parsed = shlex.split(raw, comments=False, posix=True)
    except ValueError as exc:
        raise SystemExit(f"第 {number} 行无法解析: {exc}") from exc
    if len(parsed) != 1:
        raise SystemExit(f"第 {number} 行的值必须是一个 Shell 字符串；含空格时请加引号")
    values[key] = parsed[0]

for key in ("V2_SITE_ADDRESSES", "BASIC_AUTH_USER", "BASIC_AUTH_PASSWORD"):
    value = values.get(key, "")
    if not value or value.startswith("REPLACE_WITH"):
        raise SystemExit(f"{key} 未填写或仍是占位符")

sites = values["V2_SITE_ADDRESSES"]
documentation_addresses = ("192.0.2.", "198.51.100.", "203.0.113.")
if any(address in sites for address in documentation_addresses) or not re.fullmatch(
    r"[A-Za-z0-9.:/,_-]+", sites
):
    raise SystemExit("V2_SITE_ADDRESSES 仍是示例值或格式不安全")
if not re.fullmatch(r"[A-Za-z0-9._-]+", values["BASIC_AUTH_USER"]):
    raise SystemExit("BASIC_AUTH_USER 格式不安全")
if len(values["BASIC_AUTH_PASSWORD"]) < 12:
    raise SystemExit("BASIC_AUTH_PASSWORD 至少需要 12 个字符")

fixed = {
    "APP_ROOT": "/opt/dictation",
    "CONTENT_ROOT": "/opt/dictation-content/packs/zh-cn/primary-3a",
    "STATE_ROOT": "/var/lib/dictation",
    "V2_PORT": "8889",
    "APP_USER": "dictation",
    "V2_DEPENDENCY_SOURCE": "online",
}
for key, expected in fixed.items():
    if values.get(key, expected) != expected:
        raise SystemExit(f"{key} 必须为 {expected}，防止删除或验收错误目标")
if values.get("SSH_PORT", ssh_port) != ssh_port:
    raise SystemExit("配置中的 SSH_PORT 必须与命令行 --port 一致")
if values.get("STUDIO_ENABLED", "0") not in {"0", "1"}:
    raise SystemExit("STUDIO_ENABLED 只能是 0 或 1")
PY
}

command -v python3 >/dev/null 2>&1 || die "本地缺少 python3"
validate_env_file
ok "私有 VPS 配置通过本地安全校验（未显示任何配置值）"

if ((VALIDATE_ENV_ONLY)); then
  exit 0
fi

[[ -n "$REMOTE_HOST" ]] || die "缺少 --host"
[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9._-]+$ && "$REMOTE_HOST" != -* ]] \
  || die "--host 格式不安全"
[[ "$REMOTE_USER" =~ ^[A-Za-z0-9._-]+$ && "$REMOTE_USER" != -* ]] \
  || die "--user 格式不安全"
[[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] && ((REMOTE_PORT >= 1 && REMOTE_PORT <= 65535)) \
  || die "--port 必须是 1..65535"
if [[ -n "$IDENTITY_FILE" ]]; then
  [[ -f "$IDENTITY_FILE" && ! -L "$IDENTITY_FILE" ]] \
    || die "SSH 私钥必须是普通文件且不能是软链接"
fi

for command_name in ssh scp python3 tee mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || die "本地缺少命令: $command_name"
done

readonly RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
if [[ -z "$LOG_ROOT" ]]; then
  LOG_ROOT="${XDG_STATE_HOME:-${HOME:?HOME 未设置}}/dictation/verification/bce"
fi
mkdir -p -- "$LOG_ROOT"
chmod 700 -- "$LOG_ROOT"
readonly RUN_DIR="$LOG_ROOT/$RUN_ID"
mkdir -p -- "$RUN_DIR"
chmod 700 -- "$RUN_DIR"
readonly RAW_LOG="$RUN_DIR/raw.log"
readonly RESULT_JSON="$RUN_DIR/result.json"
readonly SANITIZED_REPORT="$RUN_DIR/ubuntu-vps-bce.md"
touch "$RAW_LOG"
chmod 600 "$RAW_LOG"
exec > >(tee -a "$RAW_LOG") 2>&1

readonly LOCAL_TMP="$(mktemp -d)"
chmod 700 "$LOCAL_TMP"
readonly LOCAL_HELPER="$LOCAL_TMP/remote-helper.sh"
readonly CONTROL_PATH="$LOCAL_TMP/ssh-control"
readonly REMOTE_HELPER="/tmp/dictation-redeploy-$RUN_ID.sh"
readonly REMOTE_ENV="/tmp/dictation-redeploy-$RUN_ID.env"
readonly REMOTE_RESULT="$REMOTE_ENV.result.json"

COMMON_SSH_OPTIONS=(
  -o "ConnectTimeout=15"
  -o "ServerAliveInterval=15"
  -o "ServerAliveCountMax=3"
  -o "StrictHostKeyChecking=accept-new"
  -o "ControlMaster=auto"
  -o "ControlPersist=180"
  -o "ControlPath=$CONTROL_PATH"
)
SSH_OPTIONS=(-p "$REMOTE_PORT" "${COMMON_SSH_OPTIONS[@]}")
SCP_OPTIONS=(-P "$REMOTE_PORT" "${COMMON_SSH_OPTIONS[@]}")
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_OPTIONS+=(-i "$IDENTITY_FILE")
  SCP_OPTIONS+=(-i "$IDENTITY_FILE")
fi
readonly REMOTE_TARGET="$REMOTE_USER@$REMOTE_HOST"

REMOTE_UPLOADED=0
DESTRUCTIVE_STARTED=0
DEPLOY_COMPLETE=0

write_remote_helper() {
  cat > "$LOCAL_HELPER" <<'REMOTE'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly PHASE="${1-}"
readonly RUN_ID="${2-}"
readonly ENV_FILE="${3-}"
readonly ALLOW_CADDY_REPLACE="${4:-0}"
readonly REMOTE_SSH_PORT="${5:-22}"

readonly PROGRAM_TAG="v2.1.0-rc.1"
readonly PROGRAM_COMMIT="85e84c339faf92b9b4d10b2097cfca0e275828e2"
readonly PROGRAM_URL="https://github.com/zkzchb/dictation.git"
readonly CONTENT_TAG="content-v1.0.0"
readonly CONTENT_COMMIT="1e79970a34216edde9e31d2156ccd6bc000f8573"
readonly CONTENT_URL="https://github.com/zkzchb/dictation-content.git"
readonly DATASET_SHA256="b8ea48ecfb9302ba4fc05b6dfca32786360db111d76df90a865e937db6cfc43a"
readonly PACK_ID="chinese-3a"
readonly CONTENT_VERSION="1.0.0"
readonly EXPECTED_LESSONS=43
readonly EXPECTED_PUBLIC_LESSONS=42
readonly EXPECTED_KNOWLEDGE_POINTS=814
readonly EXPECTED_AUDIO_FILES=894
readonly EXPECTED_WORDS=30
readonly EXPECTED_POLYPHONIC=2

readonly APP_ROOT="/opt/dictation"
readonly CONTENT_REPO="/opt/dictation-content"
readonly CONTENT_ROOT="/opt/dictation-content/packs/zh-cn/primary-3a"
readonly STATE_ROOT="/var/lib/dictation"
readonly BACKUP_BASE="/var/backups/dictation"
readonly BACKUP_ROOT="$BACKUP_BASE/pre-redeploy/$RUN_ID"
readonly ACCEPT_ROOT="$BACKUP_BASE/acceptance/$RUN_ID"
readonly REMOTE_LOG="$ACCEPT_ROOT/raw.log"
readonly RESULT_JSON="$ACCEPT_ROOT/result.json"
readonly SAFE_RESULT_COPY="$ENV_FILE.result.json"

die() {
  printf '[X] %s\n' "$*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

ok() {
  printf '[OK] %s\n' "$*"
}

[[ "$RUN_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || die "运行标识格式异常"
[[ "$PHASE" =~ ^(preflight|backup|destroy|install|accept|rollback)$ ]] || die "未知阶段: $PHASE"
[[ "$ALLOW_CADDY_REPLACE" == "0" || "$ALLOW_CADDY_REPLACE" == "1" ]] \
  || die "Caddy 覆盖开关异常"
[[ "$REMOTE_SSH_PORT" =~ ^[0-9]+$ ]] \
  && ((REMOTE_SSH_PORT >= 1 && REMOTE_SSH_PORT <= 65535)) \
  || die "SSH 端口异常"
[[ $EUID -eq 0 ]] || die "远端阶段必须以 root 运行"

if [[ "$PHASE" != "preflight" ]]; then
  [[ ! -L "$BACKUP_BASE" ]] || die "$BACKUP_BASE 不能是软链接"
  mkdir -p "$ACCEPT_ROOT"
  chmod 700 "$BACKUP_BASE" "$ACCEPT_ROOT"
  touch "$REMOTE_LOG"
  chmod 600 "$REMOTE_LOG"
  exec > >(tee -a "$REMOTE_LOG") 2>&1
fi

assert_safe_layout() {
  local path
  for path in \
    "$APP_ROOT" \
    "$CONTENT_REPO" \
    "$STATE_ROOT" \
    /etc/dictation \
    "$BACKUP_BASE" \
    /etc/caddy/Caddyfile \
    /etc/systemd/system/dictation-v2.service \
    /etc/systemd/system/dictation-v2.service.d; do
    [[ ! -L "$path" ]] || die "拒绝操作软链接路径: $path"
  done
  if command -v mountpoint >/dev/null 2>&1; then
    for path in "$APP_ROOT" "$CONTENT_REPO" "$STATE_ROOT" /etc/dictation; do
      if [[ -e "$path" ]] && mountpoint -q "$path"; then
        die "拒绝删除独立挂载点: $path"
      fi
    done
  fi
  if getent passwd dictation >/dev/null 2>&1; then
    local app_home
    app_home="$(getent passwd dictation | cut -d: -f6)"
    [[ "$app_home" == "$STATE_ROOT" ]] \
      || die "dictation 系统用户的 home 不是预期状态目录，拒绝继续"
  fi
}

caddy_looks_managed() {
  local file=/etc/caddy/Caddyfile
  [[ ! -s "$file" ]] && return 0
  grep -Fq 'handle /api/*' "$file" \
    && grep -Fq 'reverse_proxy 127.0.0.1:8889' "$file" \
    && grep -Fq '@audioState' "$file" \
    && grep -Fq 'root * /var/lib/dictation/web' "$file"
}

database_integrity() {
  local database="$1"
  python3 - "$database" <<'PY'
import sqlite3
import sys

uri = f"file:{sys.argv[1]}?mode=ro"
connection = sqlite3.connect(uri, uri=True)
try:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    connection.close()
if result != "ok":
    raise SystemExit(f"SQLite integrity_check failed: {result}")
PY
}

phase_preflight() {
  step "远端只读预检"
  [[ -r /etc/os-release ]] || die "无法识别远端操作系统"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID-}" == "ubuntu" ]] || die "仅支持 Ubuntu，当前为 ${ID-unknown}"
  command -v python3 >/dev/null 2>&1 || die "远端缺少 python3，无法安全备份 SQLite"
  command -v tar >/dev/null 2>&1 || die "远端缺少 tar"
  command -v sha256sum >/dev/null 2>&1 || die "远端缺少 sha256sum"
  assert_safe_layout

  if [[ -s /etc/caddy/Caddyfile ]] && ! caddy_looks_managed; then
    if [[ "$ALLOW_CADDY_REPLACE" == "1" ]]; then
      printf '[!] 现有 Caddyfile 无法识别为 Dictation 专用配置；已显式允许备份后替换\n'
    else
      die "现有 Caddyfile 可能包含其他站点；确认服务器专用后加 --allow-caddy-replace"
    fi
  fi

  if [[ -f "$STATE_ROOT/v2/dictation.db" ]]; then
    database_integrity "$STATE_ROOT/v2/dictation.db"
    ok "现有 SQLite 数据库一致"
  else
    ok "未发现现有 SQLite 数据库"
  fi

  local existing_bytes=0 path bytes available required
  for path in "$APP_ROOT" "$CONTENT_REPO" "$STATE_ROOT" /etc/dictation; do
    if [[ -e "$path" ]]; then
      bytes="$(du -sb -- "$path" | awk '{print $1}')"
      existing_bytes=$((existing_bytes + bytes))
    fi
  done
  available="$(df -PB1 /var/backups | awk 'NR==2 {print $4}')"
  required=$((existing_bytes + 1073741824))
  ((available >= required)) \
    || die "可用空间不足：必须容纳现有部署快照并额外保留 1 GiB"

  printf '  Ubuntu: %s\n' "${PRETTY_NAME:-$VERSION_ID}"
  printf '  Architecture: %s\n' "$(uname -m)"
  printf '  Existing deployment bytes: %s\n' "$existing_bytes"
  printf '  Available backup bytes: %s\n' "$available"
  ok "远端布局、Caddy 边界、数据库与空间通过预检"
}

phase_backup() {
  phase_preflight
  step "停止写入并创建可恢复快照"
  [[ ! -e "$BACKUP_ROOT" ]] || die "本次快照目录已存在: $BACKUP_ROOT"
  mkdir -p "$BACKUP_ROOT"
  chmod 700 "$BACKUP_ROOT"

  local dictation_active=no caddy_active=no
  systemctl is-active --quiet dictation-v2.service 2>/dev/null && dictation_active=yes
  systemctl is-active --quiet caddy.service 2>/dev/null && caddy_active=yes

  recover_services() {
    if [[ "$dictation_active" == "yes" ]]; then
      systemctl start dictation-v2.service >/dev/null 2>&1 || true
    fi
    if [[ "$caddy_active" == "yes" ]]; then
      systemctl start caddy.service >/dev/null 2>&1 || true
    fi
  }
  trap recover_services ERR INT TERM
  [[ "$dictation_active" == "no" ]] || systemctl stop dictation-v2.service

  if [[ -f "$STATE_ROOT/v2/dictation.db" ]]; then
    python3 - "$STATE_ROOT/v2/dictation.db" "$BACKUP_ROOT/database.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    target.close()
    source.close()
if result != "ok":
    raise SystemExit(f"backup integrity_check failed: {result}")
PY
    chmod 600 "$BACKUP_ROOT/database.sqlite3"
  fi

  local -a items=()
  local path relative
  for path in \
    "$APP_ROOT" \
    "$CONTENT_REPO" \
    "$STATE_ROOT" \
    /etc/dictation \
    /etc/caddy/Caddyfile \
    /etc/systemd/system/dictation-v2.service \
    /etc/systemd/system/dictation-v2.service.d \
    /usr/local/bin/backup-dictation.sh; do
    if [[ -e "$path" ]]; then
      relative="${path#/}"
      items+=("$relative")
    fi
  done
  if ((${#items[@]})); then
    tar --numeric-owner -czf "$BACKUP_ROOT/filesystem.tar.gz" -C / -- "${items[@]}"
  else
    tar -C / -czf "$BACKUP_ROOT/filesystem.tar.gz" --files-from /dev/null
  fi
  tar -tzf "$BACKUP_ROOT/filesystem.tar.gz" >/dev/null
  (crontab -l 2>/dev/null || true) > "$BACKUP_ROOT/root.crontab"
  {
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'dictation_service_active=%s\n' "$dictation_active"
    printf 'caddy_service_active=%s\n' "$caddy_active"
    if [[ -d "$APP_ROOT/.git" ]]; then
      printf 'program_ref=%s\n' "$(git -C "$APP_ROOT" rev-parse HEAD 2>/dev/null || true)"
    fi
    if [[ -d "$CONTENT_REPO/.git" ]]; then
      printf 'content_ref=%s\n' "$(git -C "$CONTENT_REPO" rev-parse HEAD 2>/dev/null || true)"
    fi
  } > "$BACKUP_ROOT/inventory.env"
  chmod 600 "$BACKUP_ROOT/root.crontab" "$BACKUP_ROOT/inventory.env"

  (
    cd "$BACKUP_ROOT"
    local -a checksum_files=(filesystem.tar.gz root.crontab inventory.env)
    [[ ! -f database.sqlite3 ]] || checksum_files+=(database.sqlite3)
    sha256sum "${checksum_files[@]}" > SHA256SUMS
    sha256sum -c SHA256SUMS
  )
  printf 'ready\n' > "$BACKUP_ROOT/BACKUP_READY"
  chmod 600 "$BACKUP_ROOT/BACKUP_READY" "$BACKUP_ROOT/SHA256SUMS"
  sync

  if [[ "$dictation_active" == "yes" ]]; then
    systemctl start dictation-v2.service
  fi
  trap - ERR INT TERM
  ok "旧部署快照已验证；删除尚未开始"
}

archive_entry_allowed() {
  case "$1" in
    opt/dictation|opt/dictation/*|opt/dictation-content|opt/dictation-content/*|var/lib/dictation|var/lib/dictation/*|etc/dictation|etc/dictation/*|etc/caddy/Caddyfile|etc/systemd/system/dictation-v2.service|etc/systemd/system/dictation-v2.service.d|etc/systemd/system/dictation-v2.service.d/*|usr/local/bin/backup-dictation.sh) return 0 ;;
    *) return 1 ;;
  esac
}

verify_backup() {
  [[ -f "$BACKUP_ROOT/BACKUP_READY" ]] || die "缺少已验证快照标记"
  (
    cd "$BACKUP_ROOT"
    sha256sum -c SHA256SUMS
  )
  local entry
  while IFS= read -r entry; do
    [[ -z "$entry" ]] || archive_entry_allowed "${entry%/}" \
      || die "快照含意外路径，拒绝恢复或删除: $entry"
  done < <(tar -tzf "$BACKUP_ROOT/filesystem.tar.gz")
  if [[ -f "$BACKUP_ROOT/database.sqlite3" ]]; then
    database_integrity "$BACKUP_ROOT/database.sqlite3"
  fi
}

remove_tree() {
  local path="$1"
  case "$path" in
    /opt/dictation|/opt/dictation-content|/var/lib/dictation|/etc/dictation|/etc/systemd/system/dictation-v2.service.d) ;;
    *) die "拒绝删除未列入白名单的目录: $path" ;;
  esac
  [[ ! -L "$path" ]] || die "拒绝删除软链接目录: $path"
  if [[ -d "$path" ]]; then
    find "$path" -xdev -depth -mindepth 1 -delete
    rmdir -- "$path"
  elif [[ -e "$path" ]]; then
    die "预期目录却发现其他文件类型: $path"
  fi
}

remove_runtime() {
  systemctl disable --now dictation-v2.service >/dev/null 2>&1 || true
  systemctl stop caddy.service >/dev/null 2>&1 || true
  rm -f -- /etc/systemd/system/dictation-v2.service /usr/local/bin/backup-dictation.sh
  remove_tree /etc/systemd/system/dictation-v2.service.d
  remove_tree /etc/dictation
  remove_tree /opt/dictation
  remove_tree /opt/dictation-content
  remove_tree /var/lib/dictation
  if [[ -d /etc/caddy ]]; then
    install -m 0644 /dev/null /etc/caddy/Caddyfile
  fi

  local cron_tmp
  cron_tmp="$(mktemp)"
  (crontab -l 2>/dev/null || true) \
    | awk '!/^[[:space:]]*0[[:space:]]+3[[:space:]]+\*[[:space:]]+\*[[:space:]]+\*[[:space:]]+\/usr\/local\/bin\/backup-dictation\.sh[[:space:]]*$/' \
    > "$cron_tmp"
  if [[ -s "$cron_tmp" ]]; then
    crontab "$cron_tmp"
  else
    crontab -r >/dev/null 2>&1 || true
  fi
  rm -f -- "$cron_tmp"
  systemctl daemon-reload
}

phase_destroy() {
  step "验证快照并删除固定的旧部署路径"
  assert_safe_layout
  verify_backup
  remove_runtime
  [[ ! -e "$APP_ROOT" && ! -e "$CONTENT_REPO" && ! -e "$STATE_ROOT" ]] \
    || die "旧部署目录未完全移除"
  ok "旧程序、内容副本、运行状态与服务配置已移除；备份仍保留"
}

phase_install() {
  step "从不可变标签执行全新安装"
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "远端临时配置不存在"
  [[ ! -e "$APP_ROOT" && ! -e "$CONTENT_REPO" && ! -e "$STATE_ROOT" ]] \
    || die "安装目标不为空，拒绝覆盖"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git ca-certificates file cron ufw
  ufw allow "$REMOTE_SSH_PORT/tcp" >/dev/null
  git clone --quiet --depth 1 --branch "$PROGRAM_TAG" --single-branch \
    "$PROGRAM_URL" "$APP_ROOT"
  git clone --quiet --depth 1 --branch "$CONTENT_TAG" --single-branch \
    "$CONTENT_URL" "$CONTENT_REPO"
  [[ "$(git -C "$APP_ROOT" rev-parse HEAD)" == "$PROGRAM_COMMIT" ]] \
    || die "程序标签未解析到预期提交"
  [[ "$(git -C "$CONTENT_REPO" rev-parse HEAD)" == "$CONTENT_COMMIT" ]] \
    || die "内容标签未解析到预期提交"
  [[ "$(git -C "$APP_ROOT" describe --tags --exact-match)" == "$PROGRAM_TAG" ]] \
    || die "程序检出不是不可变候选标签"
  [[ "$(git -C "$CONTENT_REPO" describe --tags --exact-match)" == "$CONTENT_TAG" ]] \
    || die "内容检出不是不可变内容标签"

  install -m 0600 -o root -g root "$ENV_FILE" "$APP_ROOT/deploy/vps.env"
  bash "$APP_ROOT/deploy/vps-install.sh"
  rm -f -- "$ENV_FILE"
  ok "候选程序与内容标签已完成全新安装"
}

phase_accept() {
  step "执行服务、内容、鉴权、重启与备份恢复验收"
  systemctl is-active --quiet dictation-v2.service || die "dictation-v2 未运行"
  systemctl is-active --quiet caddy.service || die "caddy 未运行"
  caddy validate --config /etc/caddy/Caddyfile >/dev/null

  local health lessons_json generation_json audio_tmp
  local health_lessons health_kps public_lessons lesson_seq word_count polyphonic_count
  health="$(curl -fsS --max-time 15 http://127.0.0.1:8889/api/health)"
  read -r health_lessons health_kps < <(
    printf '%s' "$health" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d.get("status") == "ok"
print(d["database"]["lessons"], d["database"]["knowledge_points"])
'
  )
  [[ "$health_lessons" == "$EXPECTED_LESSONS" && "$health_kps" == "$EXPECTED_KNOWLEDGE_POINTS" ]] \
    || die "健康检查计数不符"

  lessons_json="$(curl -fsS --max-time 15 http://127.0.0.1:8889/api/lessons)"
  read -r public_lessons lesson_seq < <(
    printf '%s' "$lessons_json" | python3 -c '
import json,sys
items=json.load(sys.stdin)
daily=[item for item in items if not item.get("is_review")]
assert daily
print(len(items), daily[0]["lesson_seq"])
'
  )
  [[ "$public_lessons" == "$EXPECTED_PUBLIC_LESSONS" ]] || die "课程 API 计数不符"
  generation_json="$(curl -fsS --max-time 20 \
    "http://127.0.0.1:8889/api/generate_daily/$lesson_seq?mode=daily")"
  read -r word_count polyphonic_count < <(
    printf '%s' "$generation_json" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(len(d["data"]), len(d["polyphonic_section"]))
'
  )
  [[ "$word_count" == "$EXPECTED_WORDS" && "$polyphonic_count" == "$EXPECTED_POLYPHONIC" ]] \
    || die "词表生成计数不符"

  DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
    python3 "$APP_ROOT/shared/tools/audio_bundle.py" \
      inventory --audio-dir "$STATE_ROOT/web/audio" >/dev/null
  local audio_count
  audio_count="$(find "$STATE_ROOT/web/audio" -type f -name '*.mp3' | wc -l | tr -d ' ')"
  [[ "$audio_count" == "$EXPECTED_AUDIO_FILES" ]] || die "运行音频数量不符"

  audio_tmp="$(mktemp)"
  local audio_code audio_type
  audio_code="$(curl -sS --max-time 20 -o "$audio_tmp" -w '%{http_code}' \
    http://127.0.0.1:8889/audio/sys/intro.mp3)"
  audio_type="$(file --brief --mime-type "$audio_tmp")"
  [[ "$audio_code" == "200" && -s "$audio_tmp" && "$audio_type" == audio/* ]] \
    || die "静态音频验收失败"
  ffprobe -v error "$audio_tmp" >/dev/null 2>&1 || die "静态音频无法解码"
  rm -f -- "$audio_tmp"

  python3 - "$STATE_ROOT/deployment.json" <<PY
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {
    "program_ref": "$PROGRAM_COMMIT",
    "content_ref": "$CONTENT_COMMIT",
    "content_version": "$CONTENT_VERSION",
    "pack_id": "$PACK_ID",
    "dataset_sha256": "$DATASET_SHA256",
}
for key, value in expected.items():
    if d.get(key) != value:
        raise SystemExit(f"deployment.json {key} mismatch")
PY

  # Test the Caddy Basic Auth boundary without printing addresses or credentials.
  # shellcheck disable=SC1090
  . "$APP_ROOT/deploy/vps.env"
  local first_site auth_scheme auth_host auth_port auth_url unauthorized authorized
  first_site="${V2_SITE_ADDRESSES%%,*}"
  read -r auth_scheme auth_host auth_port < <(
    python3 - "$first_site" <<'PY'
import sys
from urllib.parse import urlsplit

raw = sys.argv[1]
if "://" not in raw:
    raw = "https://" + raw
parsed = urlsplit(raw)
if not parsed.hostname or parsed.scheme not in {"http", "https"}:
    raise SystemExit("unsupported Caddy site address")
port = parsed.port or (443 if parsed.scheme == "https" else 80)
print(parsed.scheme, parsed.hostname, port)
PY
  )
  auth_url="$auth_scheme://$auth_host:$auth_port/api/health"
  local attempt
  unauthorized=000
  authorized=000
  for attempt in {1..12}; do
    unauthorized="$(curl -k -sS --connect-timeout 3 --max-time 5 \
      -o /dev/null -w '%{http_code}' \
      --resolve "$auth_host:$auth_port:127.0.0.1" "$auth_url" || true)"
    if [[ "$unauthorized" == "401" ]]; then
      authorized="$(curl -k -sS --connect-timeout 3 --max-time 5 \
        -o /dev/null -w '%{http_code}' \
        --resolve "$auth_host:$auth_port:127.0.0.1" \
        -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASSWORD" "$auth_url" || true)"
    fi
    [[ "$unauthorized" == "401" && "$authorized" == "200" ]] && break
    sleep 5
  done
  [[ "$unauthorized" == "401" && "$authorized" == "200" ]] \
    || die "Caddy Basic Auth 验收失败（匿名 $unauthorized，认证 $authorized）"

  systemctl restart dictation-v2.service
  sleep 3
  systemctl is-active --quiet dictation-v2.service || die "服务重启后未运行"
  curl -fsS --max-time 15 http://127.0.0.1:8889/api/health >/dev/null

  /usr/local/bin/backup-dictation.sh
  local db_backup audio_backup restore_dir restored_audio
  db_backup="$BACKUP_BASE/v2/dictation_$(date +%F).db.gz"
  audio_backup="$BACKUP_BASE/audio/audio_$(date +%F).tar.gz"
  [[ -f "$db_backup" && -f "$audio_backup" ]] || die "每日备份产物缺失"
  gzip -t "$db_backup"
  tar -tzf "$audio_backup" >/dev/null
  restore_dir="$(mktemp -d)"
  gzip -cd "$db_backup" > "$restore_dir/restored.db"
  database_integrity "$restore_dir/restored.db"
  python3 - "$restore_dir/restored.db" <<PY
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
try:
    lessons = connection.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    points = connection.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
finally:
    connection.close()
assert lessons == $EXPECTED_LESSONS
assert points == $EXPECTED_KNOWLEDGE_POINTS
PY
  mkdir "$restore_dir/audio"
  tar -C "$restore_dir/audio" -xzf "$audio_backup"
  restored_audio="$(find "$restore_dir/audio/audio" -type f -name '*.mp3' | wc -l | tr -d ' ')"
  [[ "$restored_audio" == "$EXPECTED_AUDIO_FILES" ]] || die "音频备份恢复计数不符"
  find "$restore_dir" -xdev -depth -delete

  local os_pretty architecture python_version caddy_version
  # shellcheck disable=SC1091
  . /etc/os-release
  os_pretty="${PRETTY_NAME:-Ubuntu}"
  architecture="$(uname -m)"
  python_version="$(python3 --version 2>&1)"
  caddy_version="$(caddy version | awk '{print $1}')"
  python3 - "$RESULT_JSON" \
    "$os_pretty" "$architecture" "$python_version" "$caddy_version" \
    "$health_lessons" "$health_kps" "$public_lessons" \
    "$word_count" "$polyphonic_count" "$audio_count" "$audio_code" "$audio_type" \
    "$unauthorized" "$authorized" <<PY
import json
import sys
from datetime import datetime, timezone

(
    output, os_pretty, architecture, python_version, caddy_version,
    lessons, points, public_lessons, words, polyphonic, audio_files,
    audio_status, audio_type, unauthorized, authorized,
) = sys.argv[1:]
data = {
    "date_utc": datetime.now(timezone.utc).date().isoformat(),
    "target": "Ubuntu VPS / BCE",
    "program_tag": "$PROGRAM_TAG",
    "program_commit": "$PROGRAM_COMMIT",
    "content_tag": "$CONTENT_TAG",
    "content_commit": "$CONTENT_COMMIT",
    "pack_id": "$PACK_ID",
    "content_version": "$CONTENT_VERSION",
    "dataset_sha256": "$DATASET_SHA256",
    "platform": f"{os_pretty}; {architecture}; {python_version}; Caddy {caddy_version}",
    "lessons": int(lessons),
    "knowledge_points": int(points),
    "public_lessons": int(public_lessons),
    "generated_words": int(words),
    "polyphonic_items": int(polyphonic),
    "audio_files": int(audio_files),
    "audio_status": int(audio_status),
    "audio_type": audio_type,
    "unauthorized_status": int(unauthorized),
    "authorized_status": int(authorized),
    "restart": "pass",
    "backup_restore": "pass",
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
  chmod 600 "$RESULT_JSON"
  install -m 0644 -o root -g root "$RESULT_JSON" "$SAFE_RESULT_COPY"
  ok "BCE 全新部署验收全部通过"
}

phase_rollback() {
  step "从删除前快照自动恢复旧部署"
  verify_backup
  remove_runtime
  tar -C / -xzf "$BACKUP_ROOT/filesystem.tar.gz"
  if [[ -s "$BACKUP_ROOT/root.crontab" ]]; then
    crontab "$BACKUP_ROOT/root.crontab"
  else
    crontab -r >/dev/null 2>&1 || true
  fi
  systemctl daemon-reload
  if grep -Fxq 'dictation_service_active=yes' "$BACKUP_ROOT/inventory.env" \
      && [[ -f /etc/systemd/system/dictation-v2.service ]]; then
    systemctl enable --now dictation-v2.service
  fi
  if grep -Fxq 'caddy_service_active=yes' "$BACKUP_ROOT/inventory.env" \
      && [[ -s /etc/caddy/Caddyfile ]]; then
    caddy validate --config /etc/caddy/Caddyfile >/dev/null
    systemctl enable --now caddy.service
  fi
  ok "旧部署已从校验快照恢复"
}

case "$PHASE" in
  preflight) phase_preflight ;;
  backup) phase_backup ;;
  destroy) phase_destroy ;;
  install) phase_install ;;
  accept) phase_accept ;;
  rollback) phase_rollback ;;
esac
REMOTE
  chmod 600 "$LOCAL_HELPER"
}

remote_phase() {
  local phase="$1" command_line
  printf -v command_line \
    'if [ "$(id -u)" -eq 0 ]; then exec bash %q %q %q %q %q %q; else exec sudo -n bash %q %q %q %q %q %q; fi' \
    "$REMOTE_HELPER" "$phase" "$RUN_ID" "$REMOTE_ENV" "$ALLOW_CADDY_REPLACE" "$REMOTE_PORT" \
    "$REMOTE_HELPER" "$phase" "$RUN_ID" "$REMOTE_ENV" "$ALLOW_CADDY_REPLACE" "$REMOTE_PORT"
  ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" "$command_line"
}

cleanup_local() {
  local status=$?
  trap - EXIT
  set +e
  if ((status != 0 && DESTRUCTIVE_STARTED == 1 && DEPLOY_COMPLETE == 0)); then
    printf '\n[!] 部署未完成，正在自动恢复删除前快照……\n'
    if remote_phase rollback; then
      printf '[OK] 自动恢复成功；BCE 已回到删除前状态\n'
    else
      printf '[X] 自动恢复失败；请保留日志并使用远端私有快照人工恢复\n' >&2
    fi
  fi
  if ((REMOTE_UPLOADED)); then
    ssh "${SSH_OPTIONS[@]}" "$REMOTE_TARGET" \
      "if [ \"\$(id -u)\" -eq 0 ]; then rm -f -- '$REMOTE_HELPER' '$REMOTE_ENV' '$REMOTE_RESULT'; else sudo -n rm -f -- '$REMOTE_HELPER' '$REMOTE_ENV' '$REMOTE_RESULT'; fi" \
      >/dev/null 2>&1 || true
    ssh "${SSH_OPTIONS[@]}" -O exit "$REMOTE_TARGET" >/dev/null 2>&1 || true
  fi
  if [[ -d "$LOCAL_TMP" && "$LOCAL_TMP" == /tmp/* ]]; then
    find "$LOCAL_TMP" -xdev -depth -delete >/dev/null 2>&1 || true
  fi
  if ((status != 0)); then
    printf '[X] 执行失败；私有原始日志: %s\n' "$RAW_LOG" >&2
  fi
  exit "$status"
}
trap cleanup_local EXIT

write_remote_helper
step "上传一次性远端执行助手（不含任何凭据）"
scp "${SCP_OPTIONS[@]}" "$LOCAL_HELPER" "$REMOTE_TARGET:$REMOTE_HELPER"
REMOTE_UPLOADED=1
remote_phase preflight

if ((PREFLIGHT_ONLY)); then
  ok "只读预检结束；远端部署未修改"
  exit 0
fi

printf '\n将备份后删除并重建以下固定目标：\n'
printf '  /opt/dictation\n  /opt/dictation-content\n  /var/lib/dictation\n'
printf '  dictation-v2.service、Dictation Caddyfile 与备份 cron 项\n'
printf '远端私有备份目录 /var/backups/dictation 不会删除。\n'
[[ -t 0 ]] || die "完整重部署需要交互式终端确认"
printf '请输入 REDEPLOY %s 继续: ' "$REMOTE_HOST"
read -r confirmation
[[ "$confirmation" == "REDEPLOY $REMOTE_HOST" ]] || die "确认文本不匹配，未修改远端"

step "上传私有部署配置"
scp "${SCP_OPTIONS[@]}" "$ENV_FILE" "$REMOTE_TARGET:$REMOTE_ENV"
remote_phase backup

DESTRUCTIVE_STARTED=1
remote_phase destroy
remote_phase install
remote_phase accept

step "下载不含基础设施标识或凭据的安全结果"
scp "${SCP_OPTIONS[@]}" \
  "$REMOTE_TARGET:$REMOTE_RESULT" \
  "$RESULT_JSON"
chmod 600 "$RESULT_JSON"

python3 - "$RESULT_JSON" "$SANITIZED_REPORT" <<'PY'
import json
from pathlib import Path
import sys

source = Path(sys.argv[1])
output = Path(sys.argv[2])
d = json.loads(source.read_text(encoding="utf-8"))

report = f"""# Deployment verification: Ubuntu VPS / BCE

## Identity

| Field | Value |
|---|---|
| Date (UTC) | `{d['date_utc']}` |
| Target | `Ubuntu VPS / BCE` |
| Program ref / commit | `{d['program_tag']} / {d['program_commit']}` |
| Content ref / commit | `{d['content_tag']} / {d['content_commit']}` |
| Pack / version | `{d['pack_id']} / {d['content_version']}` |
| Dataset digest | `{d['dataset_sha256']}` |
| Platform | `{d['platform']}` |

## Fresh-install procedure

The operator ran the public, cleanup-gated workstation entry point with a private
`vps.env` file:

```bash
bash deploy/vps-fresh-redeploy.sh \\
  --host <redacted-host> \\
  --user <redacted-user> \\
  --env-file <private-vps-env>
```

The script performed a read-only preflight, created and verified a private
snapshot, removed only the fixed Dictation paths, cloned the two immutable tags,
ran `deploy/vps-install.sh`, and executed acceptance checks. The snapshot was
retained outside the application and state trees.

## Acceptance results

| Check | Result | Evidence |
|---|---|---|
| Content and audio validation | pass | {d['lessons']} lessons; {d['knowledge_points']} knowledge points; {d['audio_files']} MP3 files; pinned dataset digest |
| Application health | pass | status `ok`; database counts matched the released content |
| Lesson and word selection | pass | {d['public_lessons']} public lessons; {d['generated_words']} words + {d['polyphonic_items']} polyphonic items |
| Static audio | pass | HTTP {d['audio_status']}; `{d['audio_type']}`; decoded with ffprobe |
| Persistence after restart | pass | service active and health endpoint succeeded after restart |
| Access control | pass | anonymous HTTP {d['unauthorized_status']}; authenticated HTTP {d['authorized_status']} |
| Backup and restore | pass | SQLite integrity/counts and {d['audio_files']} restored MP3 files verified in temporary paths |
| Update and rollback | not applicable | automatic rollback was armed but was not deliberately triggered after a successful install |

## Deviations and limitations

The full rollback branch was not deliberately triggered because the fresh
installation and all acceptance checks succeeded. A verified pre-redeployment
snapshot remains available for manual recovery. No private host, domain, account,
database, learning-history, recording-ledger, or credential value is included in
this report.

## Raw-log custody

Raw logs remain private on the operator workstation and target VPS. This
sanitized report was generated only from a fixed allowlist of safe result fields.
"""
output.write_text(report, encoding="utf-8")
PY
chmod 600 "$SANITIZED_REPORT"

DEPLOY_COMPLETE=1
ok "BCE 已完成全新部署和验收"
printf '  私有原始日志: %s\n' "$RAW_LOG"
printf '  可公开脱敏报告: %s\n' "$SANITIZED_REPORT"
printf '  程序/内容: %s + %s\n' "$PROGRAM_TAG" "$CONTENT_TAG"
