#!/usr/bin/env bash
# ============================================================================
# 听写小助手 V3 —— Cloudflare Workers + D1 一键部署
#
# 用法：
#   cp deploy/cloudflare.env.example deploy/cloudflare.env
#   nano deploy/cloudflare.env        # 填 Cloudflare 配置；维护 TTS 时才需有道密钥
#   chmod 600 deploy/cloudflare.env
#   bash deploy/cloudflare-deploy.sh
#
# 全程在本地执行，不需要服务器。幂等：可重复运行。
#
# 可选参数：
#   --skip-slices   跳过音频切片生成（已生成过时用）
#   --dev           部署完不上线，改为启动本地预览（pywrangler dev）
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

SKIP_SLICES=no
DEV_MODE=no
for arg in "$@"; do
  case "$arg" in
    --skip-slices) SKIP_SLICES=yes ;;
    --dev)         DEV_MODE=yes ;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
    *)             die "未知参数: $arg" ;;
  esac
done

# ── 读取配置 ─────────────────────────────────────────────────────────────
step "读取配置"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE
  请先执行：cp deploy/cloudflare.env.example deploy/cloudflare.env 并填写"

set -a; . "$ENV_FILE"; set +a

: "${D1_DATABASE_NAME:=dictation-v3}"
: "${YOUDAO_VOICE:=youxiaoxun}"
: "${YOUDAO_SPEED:=0.6}"
: "${TTS_INTERVAL:=1.0}"
: "${V3_DOMAIN:=}"
: "${V3_ZONE:=}"

is_placeholder() {
  case "${1-}" in ""|REPLACE_WITH*) return 0 ;; *) return 1 ;; esac
}
ok "D1 数据库名: $D1_DATABASE_NAME"
[[ -n "$V3_DOMAIN" ]] && ok "自定义域名: $V3_DOMAIN" || warn "未设自定义域名，将只用 *.workers.dev"

# ── 检查工具链 ───────────────────────────────────────────────────────────
step "检查工具链"
command -v node >/dev/null 2>&1 || die "缺少 Node.js（需 >= 18）
  安装：https://nodejs.org/  或  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
[[ "$NODE_MAJOR" -ge 18 ]] || die "Node.js 版本过低（当前 $(node -v)，需 >= 18）"
ok "Node.js $(node -v)"

command -v npx >/dev/null 2>&1 || die "缺少 npx（随 Node.js 安装）"

if ! command -v uv >/dev/null 2>&1; then
  warn "缺少 uv（pywrangler 依赖），正在安装…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv 安装失败，请手动安装：https://docs.astral.sh/uv/"
fi
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

command -v python3 >/dev/null 2>&1 || die "缺少 python3"
ok "Python $(python3 -V 2>&1 | awk '{print $2}')"

# ── Cloudflare 登录 ──────────────────────────────────────────────────────
step "确认 Cloudflare 登录状态"
if npx --yes wrangler whoami >/dev/null 2>&1; then
  WHO="$(npx --yes wrangler whoami 2>/dev/null | grep -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+' | head -1 || echo '已登录')"
  ok "Cloudflare: $WHO"
else
  warn "尚未登录，将打开浏览器授权…"
  npx --yes wrangler login || die "登录失败。也可改用 API Token：export CLOUDFLARE_API_TOKEN=..."
  ok "登录成功"
fi

# ── 生成音频切片 ─────────────────────────────────────────────────────────
step "音频切片"
AUDIO_DIR="$REPO_ROOT/shared/web/audio"
CONTENT_ROOT="$REPO_ROOT/chinese/3a"
CONTENT_AUDIO_DIR="$CONTENT_ROOT/tts"
AUDIO_TOOL="$REPO_ROOT/shared/tools/audio_bundle.py"
export DICTATION_CONTENT_ROOT="$CONTENT_ROOT"
EXISTING="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"

if ! python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null 2>&1 \
   && python3 "$AUDIO_TOOL" verify-dataset --content-root "$CONTENT_ROOT" >/dev/null 2>&1; then
  mkdir -p "$AUDIO_DIR"
  cp -an "$CONTENT_AUDIO_DIR/." "$AUDIO_DIR/"
  EXISTING="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  ok "已从 chinese/3a 教材包补齐标准音频"
fi

if python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null 2>&1; then
  ok "音频严格清单通过（$EXISTING 个 MP3）"
elif [[ "$SKIP_SLICES" == "yes" ]]; then
  die "--skip-slices 不能跳过失败的音频完整性校验"
else
  if is_placeholder "${YOUDAO_APP_KEY-}" || is_placeholder "${YOUDAO_APP_SECRET-}"; then
    die "仓库教材包或运行音频未通过严格校验，且有道密钥仍是占位符。"
  fi

  # gen_slices.py 需要 requests；用临时 venv 装，避免污染系统 Python
  GEN_VENV="$REPO_ROOT/.venv-gen"
  if [[ ! -x "$GEN_VENV/bin/python" ]]; then
    python3 -m venv "$GEN_VENV"
    "$GEN_VENV/bin/pip" install --quiet --upgrade pip
    "$GEN_VENV/bin/pip" install --quiet requests
  fi
  ok "生成环境就绪，开始合成（约需数分钟，可中断后重跑）"

  YOUDAO_APP_KEY="$YOUDAO_APP_KEY" \
  YOUDAO_APP_SECRET="$YOUDAO_APP_SECRET" \
  YOUDAO_VOICE="$YOUDAO_VOICE" \
  YOUDAO_SPEED="$YOUDAO_SPEED" \
  TTS_INTERVAL="$TTS_INTERVAL" \
    "$GEN_VENV/bin/python" "$REPO_ROOT/shared/gen_slices.py"

  NOW="$(find "$AUDIO_DIR" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
  python3 "$AUDIO_TOOL" inventory --audio-dir "$AUDIO_DIR" >/dev/null \
    || die "生成后音频仍未通过 869 个词条 + 25 个系统提示音的严格校验"
  ok "切片共 $NOW 个"
fi

# ── 铺装静态资源 ─────────────────────────────────────────────────────────
step "铺装静态资源到 v3/public/"
python3 "$REPO_ROOT/tools/stage.py" v3
[[ -f "$V3_DIR/public/index.html" ]] || die "stage 失败：缺少 v3/public/index.html"
PUB_SLICES="$(find "$V3_DIR/public/audio" -name '*.mp3' 2>/dev/null | wc -l | tr -d ' ')"
ok "index.html + $PUB_SLICES 个音频已就位"
python3 "$AUDIO_TOOL" inventory --audio-dir "$V3_DIR/public/audio" >/dev/null \
  || die "v3/public 音频未通过严格校验"

# ── D1 数据库 ────────────────────────────────────────────────────────────
step "D1 数据库"
WRANGLER_CFG="$V3_DIR/wrangler.jsonc"
[[ -f "$WRANGLER_CFG" ]] || die "缺少 $WRANGLER_CFG"

cd "$V3_DIR"

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
  CREATE_OUT="$(npx --yes wrangler d1 create "$D1_DATABASE_NAME" 2>&1 || true)"
  DB_ID="$(printf '%s' "$CREATE_OUT" \
    | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
  if [[ -z "$DB_ID" ]]; then
    # 可能已存在同名库，从列表里取
    DB_ID="$(npx --yes wrangler d1 list --json 2>/dev/null \
      | python3 -c "
import json,sys
try:
    for d in json.load(sys.stdin):
        if d.get('name')=='$D1_DATABASE_NAME':
            print(d.get('uuid') or d.get('database_id') or ''); break
except Exception:
    pass" 2>/dev/null || echo "")"
  fi
  [[ -n "$DB_ID" ]] || die "无法创建或找到数据库。wrangler 输出：
$CREATE_OUT"
  ok "database_id: $DB_ID"
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
python3 "$REPO_ROOT/shared/tools/export_d1.py"
SEED="$V3_DIR/migrations/0002_seed.sql"
[[ -f "$SEED" ]] || die "种子 SQL 未生成：$SEED"
ok "$(basename "$SEED") ($(wc -l < "$SEED" | tr -d ' ') 行)"

# ── 应用迁移 ─────────────────────────────────────────────────────────────
step "应用数据库迁移（远端）"
cd "$V3_DIR"
if npx --yes wrangler d1 migrations apply "$D1_DATABASE_NAME" --remote 2>&1 | tail -20; then
  ok "迁移已应用"
else
  warn "迁移命令返回非零。若提示 'No migrations to apply' 属正常"
fi

# 验证行数
COUNTS="$(npx --yes wrangler d1 execute "$D1_DATABASE_NAME" --remote --json \
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

# pywrangler 由 workers-py 包提供。项目里声明了它就能 `uv run pywrangler`；
# 没声明时 uv 只装 pyproject 的运行依赖，命令不存在（报 Failed to spawn）。
# 用 uvx --from 直接取包来跑，无需先把它装进项目 —— 这也是官方文档给出的
# 初始化方式（uvx --from workers-py pywrangler init）。
pywrangler_cmd() {
  if uv run --quiet pywrangler --version >/dev/null 2>&1; then
    echo "uv run pywrangler"
  else
    echo "uvx --from workers-py pywrangler"
  fi
}

# ── 本地预览模式 ─────────────────────────────────────────────────────────
if [[ "$DEV_MODE" == "yes" ]]; then
  step "启动本地预览"
  echo "  访问 http://localhost:8787 ，Ctrl+C 结束"
  echo "  注意：--dev 使用本地 D1 副本，需先执行一次："
  echo "    npx wrangler d1 migrations apply $D1_DATABASE_NAME --local"
  echo
  cd "$V3_DIR"
  exec $(pywrangler_cmd) dev
fi

# ── 部署 ─────────────────────────────────────────────────────────────────
step "部署到 Cloudflare"
cd "$V3_DIR"
PW="$(pywrangler_cmd)"
ok "使用 $PW"
DEPLOY_OUT="$($PW deploy 2>&1)" || {
  echo "$DEPLOY_OUT"
  die "部署失败（上方为 wrangler 输出）"
}
echo "$DEPLOY_OUT" | tail -15
WORKER_URL="$(printf '%s' "$DEPLOY_OUT" | grep -oE 'https://[a-zA-Z0-9.-]*workers\.dev' | head -1)"
ok "部署成功${WORKER_URL:+：$WORKER_URL}"

# ── 自定义域名 ───────────────────────────────────────────────────────────
if [[ -n "$V3_DOMAIN" ]]; then
  step "自定义域名"
  if grep -q '"routes"' "$WRANGLER_CFG"; then
    ok "wrangler.jsonc 已含 routes 配置"
  else
    warn "wrangler.jsonc 未声明 routes。绑定 $V3_DOMAIN 需在 Dashboard 手动添加："
    echo "    Workers & Pages → $D1_DATABASE_NAME → Settings → Domains & Routes"
    echo "    → Add Custom Domain → $V3_DOMAIN"
    echo
    echo "  或在 v3/wrangler.jsonc 顶层加入后重跑本脚本："
    echo "    \"routes\": [{ \"pattern\": \"$V3_DOMAIN/*\", \"zone_name\": \"${V3_ZONE:-你的主域名}\" }]"
  fi
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
echo "    bash deploy/cloudflare-deploy.sh --skip-slices"
echo
echo "  查看实时日志："
echo "    cd v3 && npx wrangler tail"
echo
