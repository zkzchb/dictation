#!/usr/bin/env bash
# ============================================================================
# 听写小助手 V3 —— Cloudflare Workers + D1 一键部署
#
# 用法：
#   bash deploy/cloudflare-deploy.sh --fresh
#
# 可选：更新已有部署前，可复制 deploy/cloudflare.env.example 保存固定配置。
#
# 全程在本地执行，不需要服务器。默认更新模式幂等；--fresh 仅用于新建。
#
# 可选参数：
#   --dev           不上线，改为启动本地预览（pywrangler dev）
#   --fresh         交互创建全新的 Worker 与同名 D1；拒绝覆盖已有资源
#   --require-voice 缺少或无法校验真人录音包时停止，不回退到纯 TTS
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/cloudflare.env"
V3_DIR="$REPO_ROOT/v3"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
C_HEAD=$'\033[1;36m'; C_OFF=$'\033[0m'
step() { echo; echo "${C_HEAD}==> $*${C_OFF}"; }
ok()   { echo "${C_OK}  [OK]${C_OFF} $*"; }
warn() { echo "${C_WARN}  [!] ${C_OFF} $*"; }
die()  { echo "${C_ERR}  [X] $*${C_OFF}" >&2; exit 1; }

DEV_MODE=no
FRESH_MODE=no
REQUIRE_VOICE=no
for arg in "$@"; do
  case "$arg" in
    --dev)         DEV_MODE=yes ;;
    --fresh)       FRESH_MODE=yes ;;
    --require-voice) REQUIRE_VOICE=yes ;;
    -h|--help)     sed -n '2,24p' "$0"; exit 0 ;;
    *)             die "未知参数: $arg" ;;
  esac
done
[[ "$DEV_MODE" != "yes" || "$FRESH_MODE" != "yes" ]] \
  || die "--dev 与 --fresh 不能同时使用"

# ── 选择语音来源 ─────────────────────────────────────────────────────────
DICTATION_VOICE_REPO_URL="${DICTATION_VOICE_REPO_URL:-}"
if [[ -t 0 ]]; then
  echo
  echo "${C_HEAD}语音来源${C_OFF}"
  echo "  直接回车：使用公开课程包自带的 TTS（默认）"
  echo "  真人录音：输入 https://github.com/zkzchb/dictation_voice"
  IFS= read -r -p "真人录音仓库 URL（默认留空）: " DICTATION_VOICE_REPO_URL
fi

case "$DICTATION_VOICE_REPO_URL" in
  "") ;;
  https://github.com/zkzchb/dictation_voice|https://github.com/zkzchb/dictation_voice.git) ;;
  *) die "真人录音仓库只接受: https://github.com/zkzchb/dictation_voice" ;;
esac
[[ "$REQUIRE_VOICE" != "yes" || -n "$DICTATION_VOICE_REPO_URL" ]] \
  || die "--require-voice 要求输入真人录音仓库 URL"

# ── 读取配置 ─────────────────────────────────────────────────────────────
step "读取配置"
if [[ -f "$ENV_FILE" ]]; then
  set -a; . "$ENV_FILE"; set +a
  ok "已读取 $ENV_FILE"
else
  warn "未找到 cloudflare.env，使用相邻仓库与交互式默认配置"
fi

: "${V3_WORKER_NAME:=}"
: "${D1_DATABASE_NAME:=}"
: "${CONTENT_ROOT:=../dictation-content/packs/zh-cn/primary-3a}"
: "${V3_DOMAIN:=}"

DEFAULT_CONTENT_ROOT="$(realpath -m "$REPO_ROOT/../dictation-content/packs/zh-cn/primary-3a")"
REQUESTED_CONTENT_ROOT="$CONTENT_ROOT"
[[ "$REQUESTED_CONTENT_ROOT" == /* ]] \
  || REQUESTED_CONTENT_ROOT="$REPO_ROOT/$REQUESTED_CONTENT_ROOT"
REQUESTED_CONTENT_ROOT="$(realpath -m "$REQUESTED_CONTENT_ROOT")"
if [[ "$REQUESTED_CONTENT_ROOT" == "$DEFAULT_CONTENT_ROOT" ]]; then
  step "抓取公开课程仓库"
  command -v git >/dev/null 2>&1 || die "缺少 git，无法抓取课程仓库"
  CONTENT_REPO_DIR="$REPO_ROOT/../dictation-content"
  if [[ -e "$CONTENT_REPO_DIR" && ! -d "$CONTENT_REPO_DIR/.git" ]]; then
    die "目标路径已存在但不是 Git 仓库: $CONTENT_REPO_DIR"
  fi
  if [[ -d "$CONTENT_REPO_DIR/.git" ]]; then
    CONTENT_ORIGIN="$(git -C "$CONTENT_REPO_DIR" remote get-url origin 2>/dev/null || true)"
    case "$CONTENT_ORIGIN" in
      https://github.com/zkzchb/dictation-content|https://github.com/zkzchb/dictation-content.git|git@github.com:zkzchb/dictation-content|git@github.com:zkzchb/dictation-content.git) ;;
      *) die "现有 dictation-content 的 origin 不匹配: $CONTENT_ORIGIN" ;;
    esac
    [[ -z "$(git -C "$CONTENT_REPO_DIR" status --porcelain)" ]] \
      || die "dictation-content 工作树有未提交修改，拒绝自动拉取"
    [[ "$(git -C "$CONTENT_REPO_DIR" branch --show-current)" == "main" ]] \
      || die "dictation-content 当前不在 main 分支，拒绝自动切换"
    git -C "$CONTENT_REPO_DIR" pull --ff-only origin main \
      || die "无法更新公开课程仓库"
    ok "公开课程仓库已更新"
  else
    git clone --depth 1 https://github.com/zkzchb/dictation-content \
      "$CONTENT_REPO_DIR" \
      || die "无法抓取公开课程仓库"
    ok "公开课程仓库已抓取"
  fi
  CONTENT_ROOT="$DEFAULT_CONTENT_ROOT"
fi

VOICE_PACK_ROOT=""
if [[ -n "$DICTATION_VOICE_REPO_URL" ]]; then
  step "抓取真人录音仓库"
  command -v git >/dev/null 2>&1 || die "缺少 git，无法抓取真人录音仓库"
  VOICE_REPO_DIR="$REPO_ROOT/../dictation_voice"
  if [[ -e "$VOICE_REPO_DIR" && ! -d "$VOICE_REPO_DIR/.git" ]]; then
    die "目标路径已存在但不是 Git 仓库: $VOICE_REPO_DIR"
  fi
  if [[ -d "$VOICE_REPO_DIR/.git" ]]; then
    VOICE_ORIGIN="$(git -C "$VOICE_REPO_DIR" remote get-url origin 2>/dev/null || true)"
    case "$VOICE_ORIGIN" in
      https://github.com/zkzchb/dictation_voice|https://github.com/zkzchb/dictation_voice.git|git@github.com:zkzchb/dictation_voice|git@github.com:zkzchb/dictation_voice.git) ;;
      *) die "现有 dictation_voice 的 origin 不匹配: $VOICE_ORIGIN" ;;
    esac
    [[ -z "$(git -C "$VOICE_REPO_DIR" status --porcelain)" ]] \
      || die "dictation_voice 工作树有未提交修改，拒绝自动拉取"
    [[ "$(git -C "$VOICE_REPO_DIR" branch --show-current)" == "main" ]] \
      || die "dictation_voice 当前不在 main 分支，拒绝自动切换"
    GIT_TERMINAL_PROMPT=1 git -C "$VOICE_REPO_DIR" pull --ff-only origin main \
      || die "无法更新真人录音仓库；请确认当前 GitHub 账户有私有仓库权限"
    ok "真人录音仓库已更新"
  else
    GIT_TERMINAL_PROMPT=1 git clone --depth 1 \
      "$DICTATION_VOICE_REPO_URL" "$VOICE_REPO_DIR" \
      || die "无法抓取真人录音仓库；请先配置 GitHub 登录并确认私有仓库权限"
    ok "真人录音仓库已抓取"
  fi
  VOICE_PACK_ROOT="$VOICE_REPO_DIR/packs/zh-cn/primary-3a"
fi

valid_worker_name() {
  local value="$1"
  ((${#value} >= 1 && ${#value} <= 63)) \
    && [[ "$value" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]
}

valid_domain() {
  local value="$1"
  [[ -z "$value" ]] && return 0
  ((${#value} <= 253)) \
    && [[ "$value" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

prompt_deployment_identity() {
  [[ -t 0 ]] || die "需要交互输入 Worker 名称和域名，但当前不是交互终端"
  local entered
  while :; do
    IFS= read -r -p "请输入 Worker 名称（小写字母、数字、连字符，最多 63 位）: " entered
    if valid_worker_name "$entered"; then
      V3_WORKER_NAME="$entered"
      break
    fi
    warn "Worker 名称格式不合法，请重新输入"
  done

  while :; do
    IFS= read -r -p "请输入 Custom Domain（留空则只用 workers.dev）: " entered
    entered="${entered,,}"
    if valid_domain "$entered"; then
      V3_DOMAIN="$entered"
      break
    fi
    warn "域名必须是纯主机名，不能包含 https://、路径或通配符"
  done
}

if [[ "$FRESH_MODE" == "yes" || -z "$V3_WORKER_NAME" ]]; then
  prompt_deployment_identity
fi

valid_worker_name "$V3_WORKER_NAME" \
  || die "V3_WORKER_NAME 格式不合法: $V3_WORKER_NAME"
V3_DOMAIN="${V3_DOMAIN,,}"
valid_domain "$V3_DOMAIN" \
  || die "V3_DOMAIN 必须是纯主机名，不能包含协议、路径或通配符"

if [[ -z "$D1_DATABASE_NAME" ]]; then
  D1_DATABASE_NAME="$V3_WORKER_NAME"
fi

is_placeholder() {
  case "${1-}" in ""|REPLACE_WITH*) return 0 ;; *) return 1 ;; esac
}

if [[ "$FRESH_MODE" == "yes" ]]; then
  is_placeholder "${D1_DATABASE_ID-}" \
    || die "--fresh 不接受已有 D1_DATABASE_ID；请保留占位符"
  D1_DATABASE_NAME="$V3_WORKER_NAME"
  printf '  将创建 Worker: %s\n' "$V3_WORKER_NAME"
  printf '  将创建 D1:     %s\n' "$D1_DATABASE_NAME"
  [[ -n "$V3_DOMAIN" ]] && printf '  将绑定域名:    %s\n' "$V3_DOMAIN"
  IFS= read -r -p "输入 CREATE $V3_WORKER_NAME 确认: " FRESH_CONFIRM
  [[ "$FRESH_CONFIRM" == "CREATE $V3_WORKER_NAME" ]] \
    || die "确认文字不匹配；未创建任何 Cloudflare 资源"
  unset FRESH_CONFIRM
fi
ok "Worker 名称: $V3_WORKER_NAME"
ok "D1 数据库名: $D1_DATABASE_NAME"
[[ -n "$V3_DOMAIN" ]] && ok "自定义域名: $V3_DOMAIN" || warn "未设自定义域名，将只用 *.workers.dev"

# ── 检查工具链 ───────────────────────────────────────────────────────────
step "检查工具链"
command -v node >/dev/null 2>&1 || die "缺少 Node.js（需 >= 22）
  请通过 Node.js 官方发行包或版本管理器安装当前维护版本"
NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
[[ "$NODE_MAJOR" -ge 22 ]] || die "Node.js 版本过低（当前 $(node -v)，锁定的 Wrangler 需要 >= 22）"
ok "Node.js $(node -v)"

command -v npx >/dev/null 2>&1 || die "缺少 npx（随 Node.js 安装）"
command -v npm >/dev/null 2>&1 || die "缺少 npm（随 Node.js 安装）"

if ! command -v uv >/dev/null 2>&1; then
  warn "缺少 uv（pywrangler 依赖），正在安装…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv 安装失败，请手动安装：https://docs.astral.sh/uv/"
fi
UV_VERSION="$(uv --version 2>/dev/null | awk '{print $2}')"
UV_MIN_VERSION="0.12.3"
if ! printf '%s\n%s\n' "$UV_MIN_VERSION" "$UV_VERSION" | sort -V -C; then
  die "uv 版本过低（当前 $UV_VERSION，workers-py 需要 >= $UV_MIN_VERSION）
  独立安装版可执行：uv self update
  系统包安装版请按发行版方式升级后重试"
fi
ok "uv $UV_VERSION"

command -v python3 >/dev/null 2>&1 || die "缺少 python3"
ok "Python $(python3 -V 2>&1 | awk '{print $2}')"

[[ -f "$V3_DIR/package-lock.json" ]] || die "缺少 Wrangler 锁文件: $V3_DIR/package-lock.json"
(cd "$V3_DIR" && npm ci --no-audit --no-fund >/dev/null) \
  || die "无法安装锁定的 Wrangler 工具链（尚未写入 Cloudflare 资源）"
wrangler() {
  (cd "$V3_DIR" && npx --no-install wrangler "$@")
}
WRANGLER_VERSION="$(wrangler --version 2>/dev/null | tail -1)" \
  || die "无法启动锁定的 Wrangler"
ok "Wrangler $WRANGLER_VERSION"

[[ -f "$V3_DIR/uv.lock" ]] || die "缺少 V3 依赖锁文件: $V3_DIR/uv.lock"
if ! (cd "$V3_DIR" && uv run --frozen --quiet pywrangler --version >/dev/null 2>&1); then
  die "无法从锁文件启动 pywrangler；请检查网络与 uv，然后重试（尚未写入 Cloudflare 资源）"
fi
PYWRANGLER=(uv run --frozen pywrangler)
ok "pywrangler 锁定工具链可用"

# ── Cloudflare 登录 ──────────────────────────────────────────────────────
step "确认 Cloudflare 登录状态"
if wrangler whoami >/dev/null 2>&1; then
  ok "Cloudflare 已登录（账户详情未显示）"
else
  warn "尚未登录，将打开浏览器授权…"
  wrangler login || die "登录失败。也可改用 API Token：export CLOUDFLARE_API_TOKEN=..."
  ok "登录成功"
fi

if [[ "$FRESH_MODE" == "yes" ]]; then
  step "确认目标资源尚不存在"
  D1_LIST="$(wrangler d1 list --json 2>/dev/null)" \
    || die "无法读取 D1 列表；为避免误覆盖，拒绝继续"
  set +e
  printf '%s' "$D1_LIST" | python3 -c '
import json, sys
name = sys.argv[1]
items = json.load(sys.stdin)
raise SystemExit(0 if any(item.get("name") == name for item in items) else 10)
' "$D1_DATABASE_NAME"
  D1_CHECK_STATUS=$?
  set -e
  case "$D1_CHECK_STATUS" in
    0)  die "D1 数据库已存在: $D1_DATABASE_NAME；请换一个 Worker 名称" ;;
    10) ;;
    *)  die "无法解析 D1 列表；为避免误覆盖，拒绝继续" ;;
  esac

  set +e
  WORKER_CHECK="$(wrangler deployments list --name "$V3_WORKER_NAME" --json 2>&1)"
  WORKER_CHECK_STATUS=$?
  set -e
  if ((WORKER_CHECK_STATUS == 0)); then
    die "Worker 已存在: $V3_WORKER_NAME；请换一个名称"
  fi
  if ! grep -Eqi 'not found|does not exist|no worker|10090' <<< "$WORKER_CHECK"; then
    die "无法可靠确认 Worker 名称未被占用；wrangler 返回：
$WORKER_CHECK"
  fi
  unset D1_LIST D1_CHECK_STATUS WORKER_CHECK WORKER_CHECK_STATUS
  ok "Worker 与 D1 名称均未占用"
fi

# ── 校验并铺装外部内容包 ─────────────────────────────────────────────────
step "校验并铺装外部内容包"
[[ "$CONTENT_ROOT" == /* ]] || CONTENT_ROOT="$REPO_ROOT/$CONTENT_ROOT"
[[ -f "$CONTENT_ROOT/dataset.json" ]] || die "内容包缺少 dataset.json: $CONTENT_ROOT"
AUDIO_TOOL="$REPO_ROOT/shared/tools/audio_bundle.py"
export DICTATION_CONTENT_ROOT="$CONTENT_ROOT"
python3 "$REPO_ROOT/shared/content_pack.py" "$CONTENT_ROOT" \
  || die "内容包未通过结构和哈希校验"
python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT" \
  || die "内容包音频未通过校验"
python3 "$REPO_ROOT/tools/stage.py" v3 --content-root "$CONTENT_ROOT"

if [[ -n "$VOICE_PACK_ROOT" ]]; then
  [[ "$VOICE_PACK_ROOT" == /* ]] || VOICE_PACK_ROOT="$REPO_ROOT/$VOICE_PACK_ROOT"
  VOICE_PACK_ROOT="$(cd "$VOICE_PACK_ROOT" 2>/dev/null && pwd -P)" \
    || die "找不到真人录音包目录: $VOICE_PACK_ROOT"
  VOICE_META="$VOICE_PACK_ROOT/voice-pack.json"
  VOICE_BUNDLE="$VOICE_PACK_ROOT/human-recordings.tar.gz"
  [[ -f "$VOICE_META" ]] || die "真人录音包缺少 voice-pack.json: $VOICE_META"
  [[ -f "$VOICE_BUNDLE" ]] || die "真人录音包缺少 human-recordings.tar.gz: $VOICE_BUNDLE"

  python3 - "$CONTENT_ROOT" "$VOICE_META" "$VOICE_BUNDLE" <<'PYEOF' \
    || die "真人录音包与当前内容包不兼容"
import hashlib
import json
import sys
from pathlib import Path

content_root, meta_path, bundle_path = map(Path, sys.argv[1:])
dataset = json.loads((content_root / "dataset.json").read_text(encoding="utf-8"))
studio_path = content_root / dataset["paths"]["studio_manifest"]
studio = json.loads(studio_path.read_text(encoding="utf-8"))
voice = json.loads(meta_path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


checks = {
    "content pack id": voice.get("content_pack_id") == dataset.get("id"),
    "content pack version": voice.get("content_pack_version") == dataset.get("version"),
    "dataset digest": voice.get("compatibility", {}).get("source_dataset_sha256")
        == dataset.get("sha256", {}).get("dataset"),
    "Studio manifest": voice.get("compatibility", {}).get("studio_manifest_sha256")
        == sha256(studio_path),
    "bundle filename": voice.get("bundle", {}).get("file") == bundle_path.name,
    "bundle kind": voice.get("bundle", {}).get("kind") == "human-recordings",
    "bundle checksum": voice.get("bundle", {}).get("sha256") == sha256(bundle_path),
    "Studio word count": voice.get("recordings", {}).get("studio_words") == len(studio),
    "human word count": voice.get("recordings", {}).get("human_words") == len(studio),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("不匹配项目: " + ", ".join(failed))

print(
    "[OK] 真人录音元数据: "
    f"{voice.get('id')} / {len(studio)} 词条 / "
    f"{voice.get('recordings', {}).get('human_system')} 系统提示"
)
PYEOF

  DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
    python3 "$AUDIO_TOOL" verify \
      --bundle "$VOICE_BUNDLE" --kind human-recordings \
    || die "真人录音 bundle 未通过严格校验"
  DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
    python3 "$AUDIO_TOOL" install \
      --bundle "$VOICE_BUNDLE" \
      --audio-dir "$V3_DIR/public/audio" \
      --kind human-recordings \
    || die "无法把真人录音覆盖到 Workers 静态资源"

  VOICE_REF=""
  if VOICE_REPO_ROOT="$(git -C "$VOICE_PACK_ROOT" rev-parse --show-toplevel 2>/dev/null)"; then
    VOICE_REF="$(git -C "$VOICE_REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  fi
  python3 - "$V3_DIR/public/deployment.json" "$VOICE_META" "$VOICE_REF" <<'PYEOF'
import json
import sys
from pathlib import Path

record_path, meta_path = map(Path, sys.argv[1:3])
voice_ref = sys.argv[3] or None
record = json.loads(record_path.read_text(encoding="utf-8"))
voice = json.loads(meta_path.read_text(encoding="utf-8"))
record.update({
    "voice_ref": voice_ref,
    "voice_pack_id": voice.get("id"),
    "voice_bundle_sha256": voice.get("bundle", {}).get("sha256"),
    "human_words": voice.get("recordings", {}).get("human_words"),
    "human_system": voice.get("recordings", {}).get("human_system"),
})
record_path.write_text(
    json.dumps(record, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PYEOF
  ok "真人录音已覆盖 TTS，三仓版本已写入 deployment.json"
elif [[ "$REQUIRE_VOICE" == "yes" ]]; then
  die "未找到真人录音包；请重新运行并输入 dictation_voice 仓库 URL"
else
  warn "未配置真人录音包，本次将部署纯 TTS 版本"
fi

[[ -f "$V3_DIR/public/index.html" ]] || die "stage 失败：缺少 v3/public/index.html"
PUB_SLICES="$(find "$V3_DIR/public/audio" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
ok "V3 静态资源与 $PUB_SLICES 个音频已就位"
python3 "$AUDIO_TOOL" inventory --audio-dir "$V3_DIR/public/audio" >/dev/null \
  || die "v3/public 音频未通过严格校验"

# ── D1 数据库 ────────────────────────────────────────────────────────────
step "D1 数据库"
WRANGLER_CFG="$V3_DIR/wrangler.jsonc"
[[ -f "$WRANGLER_CFG" ]] || die "缺少 $WRANGLER_CFG"
WRANGLER_BACKUP="$(mktemp)"
cp "$WRANGLER_CFG" "$WRANGLER_BACKUP"
restore_wrangler_config() {
  if [[ -f "$WRANGLER_BACKUP" ]]; then
    cp "$WRANGLER_BACKUP" "$WRANGLER_CFG"
    rm -f "$WRANGLER_BACKUP"
  fi
}
trap restore_wrangler_config EXIT

cd "$V3_DIR"

# 每次部署只临时写入目标名称、D1 绑定和 Custom Domain；EXIT trap 会恢复
# 仓库原始配置，避免把账户资源 UUID 或私有域名留在工作树中。
python3 - "$WRANGLER_CFG" "$V3_WORKER_NAME" "$D1_DATABASE_NAME" "$V3_DOMAIN" <<'PYEOF'
import re
import sys

path, worker_name, database_name, domain = sys.argv[1:]
with open(path, encoding="utf-8") as f:
    src = f.read()

src, count = re.subn(
    r'("name"\s*:\s*")[^"]*(")',
    lambda m: m.group(1) + worker_name + m.group(2),
    src,
    count=1,
)
if count != 1:
    raise SystemExit("无法更新 wrangler.jsonc 中的 Worker name")

src, count = re.subn(
    r'("database_name"\s*:\s*")[^"]*(")',
    lambda m: m.group(1) + database_name + m.group(2),
    src,
    count=1,
)
if count != 1:
    raise SystemExit("无法更新 wrangler.jsonc 中的 D1 database_name")

if domain:
    if re.search(r'"routes"\s*:', src):
        raise SystemExit(
            "wrangler.jsonc 已含 routes；为避免覆盖现有路由，请先人工核对"
        )
    close = src.rfind("}")
    if close < 0:
        raise SystemExit("wrangler.jsonc 缺少顶层结束括号")
    prefix = src[:close].rstrip()
    comma = "" if prefix.endswith("{") else ","
    route = (
        f'{comma}\n  "routes": [\n'
        '    {\n'
        f'      "pattern": "{domain}",\n'
        '      "custom_domain": true\n'
        '    }\n'
        '  ]\n'
    )
    src = prefix + route + src[close:]

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
PYEOF
ok "临时 Wrangler 配置已写入"

# 优先用配置文件里已填的 ID；否则看 wrangler.jsonc 是否已有真实 ID；都没有则新建
CFG_ID="$(grep -oE '"database_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$WRANGLER_CFG" \
  | sed -E 's/.*"database_id"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/' | head -1)"

if ! is_placeholder "${D1_DATABASE_ID-}"; then
  DB_ID="$D1_DATABASE_ID"
  ok "使用 cloudflare.env 中的 database_id"
elif ! is_placeholder "$CFG_ID"; then
  DB_ID="$CFG_ID"
  ok "使用 wrangler.jsonc 中已有的 database_id"
else
  warn "未找到 database_id，创建新数据库 $D1_DATABASE_NAME …"
  CREATE_OUT="$(wrangler d1 create "$D1_DATABASE_NAME" 2>&1 || true)"
  DB_ID="$(printf '%s' "$CREATE_OUT" \
    | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
  if [[ -z "$DB_ID" ]]; then
    # 可能已存在同名库，从列表里取
    DB_ID="$(wrangler d1 list --json 2>/dev/null \
      | python3 -c '
import json, sys
name = sys.argv[1]
try:
    for item in json.load(sys.stdin):
        if item.get("name") == name:
            print(item.get("uuid") or item.get("database_id") or "")
            break
except Exception:
    pass
' "$D1_DATABASE_NAME" 2>/dev/null || echo "")"
  fi
  [[ -n "$DB_ID" ]] || die "无法创建或找到数据库。wrangler 输出：
$CREATE_OUT"
  ok "D1 database_id 已取得（日志不显示）"
fi

# 写回 wrangler.jsonc（幂等）
if [[ "$CFG_ID" != "$DB_ID" ]]; then
  python3 - "$WRANGLER_CFG" "$DB_ID" <<'PYEOF'
import re, sys
path, db_id = sys.argv[1], sys.argv[2]
with open(path, encoding='utf-8') as f:
    src = f.read()
new = re.sub(r'("database_id"\s*:\s*")[^"]*(")', lambda m: m.group(1) + db_id + m.group(2), src, count=1)
if new != src:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new)
    print("  已写入 wrangler.jsonc")
PYEOF
  ok "wrangler.jsonc 已更新"
else
  ok "wrangler.jsonc 已是最新"
fi

# ── 生成种子 SQL ─────────────────────────────────────────────────────────
step "生成 D1 种子 SQL"
python3 "$REPO_ROOT/shared/tools/export_d1.py" --content-root "$CONTENT_ROOT"
SEED="$V3_DIR/migrations/0002_seed.sql"
RUNTIME_SQL="$V3_DIR/.content-runtime.sql"
[[ -f "$SEED" ]] || die "种子 SQL 未生成：$SEED"
[[ -f "$RUNTIME_SQL" ]] || die "运行时 SQL 未生成：$RUNTIME_SQL"
ok "$(basename "$SEED") ($(wc -l < "$SEED" | tr -d ' ') 行)"

# ── 应用迁移 ─────────────────────────────────────────────────────────────
step "应用数据库迁移（远端）"
cd "$V3_DIR"
if wrangler d1 migrations apply "$D1_DATABASE_NAME" --remote 2>&1 | tail -20; then
  ok "迁移已应用"
else
  warn "迁移命令返回非零。若提示 'No migrations to apply' 属正常"
fi

if wrangler d1 execute "$D1_DATABASE_NAME" --remote \
  --file "$SEED" 2>&1 | tail -20; then
  ok "内容包课程与知识点已同步"
else
  die "无法同步内容包课程与知识点"
fi

if wrangler d1 execute "$D1_DATABASE_NAME" --remote \
  --file "$RUNTIME_SQL" 2>&1 | tail -20; then
  ok "内容包运行时配置已刷新"
else
  die "无法写入内容包运行时配置"
fi

# 验证行数
COUNTS="$(wrangler d1 execute "$D1_DATABASE_NAME" --remote --json \
  --command "SELECT (SELECT COUNT(*) FROM lessons) AS lessons, (SELECT COUNT(*) FROM knowledge_points) AS kps;" \
  2>/dev/null || echo "")"
if [[ -n "$COUNTS" ]]; then
  printf '%s' "$COUNTS" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    r = d[0]['results'][0] if isinstance(d, list) else d['result'][0]['results'][0]
    print('  课程 %s 门，知识点 %s 条' % (r['lessons'], r['kps']))
except Exception:
    print('  (无法解析计数结果，可稍后手动核对)')
" || true
else
  warn "无法读取行数，可稍后手动核对"
fi

# ── 本地预览模式 ─────────────────────────────────────────────────────────
if [[ "$DEV_MODE" == "yes" ]]; then
  step "启动本地预览"
  echo "  访问 http://localhost:8787 ，Ctrl+C 结束"
  echo "  注意：--dev 使用本地 D1 副本，需先执行一次："
  echo "    npx --no-install wrangler d1 migrations apply $D1_DATABASE_NAME --local"
  echo
  cd "$V3_DIR"
  "${PYWRANGLER[@]}" dev
  exit $?
fi

# ── 部署 ─────────────────────────────────────────────────────────────────
step "部署到 Cloudflare"
cd "$V3_DIR"
ok "使用 uv run --frozen pywrangler"
DEPLOY_OUT="$("${PYWRANGLER[@]}" deploy 2>&1)" || {
  echo "$DEPLOY_OUT"
  die "部署失败（上方为 wrangler 输出）"
}
echo "$DEPLOY_OUT" | tail -15
WORKER_URL="$(printf '%s' "$DEPLOY_OUT" | grep -oE 'https://[a-zA-Z0-9.-]*workers\.dev' | head -1)"
ok "部署成功${WORKER_URL:+：$WORKER_URL}"

# ── 自定义域名 ───────────────────────────────────────────────────────────
if [[ -n "$V3_DOMAIN" ]]; then
  step "自定义域名"
  grep -q '"custom_domain"[[:space:]]*:[[:space:]]*true' "$WRANGLER_CFG" \
    || die "临时 Wrangler 配置缺少 Custom Domain 声明"
  ok "Custom Domain 已随 Worker 部署: $V3_DOMAIN"
fi

# ── 验收 ─────────────────────────────────────────────────────────────────
step "验收"
BASE="${WORKER_URL:-}"
[[ -n "$V3_DOMAIN" ]] && BASE="https://$V3_DOMAIN"

if [[ -n "$BASE" ]]; then
  sleep 3
  LESSONS="$(curl -fsS --max-time 20 "$BASE/api/lessons" 2>/dev/null || echo "")"
  if [[ -n "$LESSONS" ]]; then
    N="$(printf '%s' "$LESSONS" | grep -o '"lesson_seq"' | wc -l | tr -d ' ')"
    ok "/api/lessons 返回 $N 门课程"
  else
    warn "/api/lessons 暂无响应（自定义域名首次生效可能需要几分钟）"
  fi

  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/audio/sys/intro.mp3" 2>/dev/null || echo 000)"
  [[ "$CODE" == "200" ]] && ok "音频切片可访问 (200)" || warn "音频切片返回 $CODE"
else
  warn "未取得访问地址，请手动验收"
fi

# ── 完成 ─────────────────────────────────────────────────────────────────
echo
echo "${C_HEAD}============================================================${C_OFF}"
echo "${C_OK}  V3 部署完成${C_OFF}"
echo "${C_HEAD}============================================================${C_OFF}"
echo
[[ -n "$WORKER_URL" ]] && echo "  Workers 地址  $WORKER_URL"
[[ -n "$V3_DOMAIN" ]]  && echo "  自定义域名    https://$V3_DOMAIN"
echo
echo "${C_WARN}  提醒：V3 没有访问鉴权，任何人拿到地址都能打开。${C_OFF}"
echo "  如需保护，在 Cloudflare Dashboard 启用 Zero Trust Access"
echo "  （Zero Trust → Access → Applications → Add self-hosted app）"
echo
echo "  更新内容后重新部署："
echo "    bash deploy/cloudflare-deploy.sh"
echo
echo "  查看实时日志："
echo "    cd v3 && npx --no-install wrangler tail --name $V3_WORKER_NAME"
echo
