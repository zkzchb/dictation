# Cloudflare 部署指南（V3）

从 GitHub 拉取代码开始，到 HTTPS 可访问为止。全程在本地执行，**不需要服务器** —— API 是 Python Worker，前端与音频是 Workers 静态资源，全部跑在 Cloudflare 边缘。

```
浏览器
  └─ HTTPS ──> Cloudflare Edge
                ├─ /api/*    → Python Worker（FastAPI + D1）
                ├─ /audio/*  → 静态资源（预录切片，免费无限量）
                └─ /         → 静态资源（index.html）
```

与 VPS 版（V1/V2）相比：无服务器可维护、自动 CDN、正常用量在免费额度内；代价是音频切片需随部署上传，数据库换成 D1。

---

## 总览：四步

| 步骤 | 做什么 |
|---|---|
| 1 | 装工具链（Node.js、uv、wrangler 登录） |
| 2 | 拉取代码 |
| 3 | 填写 `deploy/cloudflare.env` 密钥 |
| 4 | 跑 `deploy/cloudflare-deploy.sh` |

脚本自动完成：生成切片 → 铺装静态资源 → 建 D1 → 写回 `database_id` → 导种子 SQL → 应用迁移 → 部署 → 验收。

---

## 0. 前置条件

- Cloudflare 账号（免费版即可）
- Node.js ≥ 18
- 有道智云 APP_KEY / APP_SECRET（[控制台](https://ai.youdao.com/)）—— 仅用于本地生成音频切片
- 若要用自定义域名（如 `v3.dictation.de5.net`）：该域名的 DNS 须已由 Cloudflare 托管（橙云开启）

---

## 1. 装工具链

**Node.js ≥ 18**

```bash
node -v
```

若低于 18 或未安装：

```bash
# Ubuntu / Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# macOS
brew install node
```

**uv**（Python 包管理，pywrangler 依赖）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后重开终端，或手动加入 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

> 部署脚本检测到缺 uv 时会自动装，此步可跳过。

**登录 Cloudflare**

```bash
npx wrangler login
```

浏览器会打开授权页，点 Allow。验证：

```bash
npx wrangler whoami
```

> 无浏览器环境（如远程 SSH）可改用 API Token：
> 在 Dashboard → My Profile → API Tokens 建一个含 `Workers Scripts:Edit` + `D1:Edit` 权限的 Token，然后
> `export CLOUDFLARE_API_TOKEN=你的Token`

---

## 2. 拉取代码

```bash
git clone https://github.com/zkzchb/dictation.git
cd dictation
```

---

## 3. 填写密钥

```bash
cp deploy/cloudflare.env.example deploy/cloudflare.env
nano deploy/cloudflare.env
```

**必填项**：

| 变量 | 说明 |
|---|---|
| `YOUDAO_APP_KEY` / `YOUDAO_APP_SECRET` | 生成音频切片用。若切片已生成过，可保留占位符并加 `--skip-slices` |
| `D1_DATABASE_ID` | **保留占位符即可** —— 脚本会自动建库并把真实 ID 写回 `v3/wrangler.jsonc` |

**可选项**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `V3_DOMAIN` | `v3.dictation.de5.net` | 自定义域名。留空则只用 `*.workers.dev` |
| `V3_ZONE` | `de5.net` | 域名所在 Cloudflare Zone |
| `D1_DATABASE_NAME` | `dictation-v3` | D1 库名 |
| `TTS_INTERVAL` | `1.0` | 切片生成间隔（秒）。报 411 限流时调大 |

保存后收紧权限：

```bash
chmod 600 deploy/cloudflare.env
```

---

## 4. 一键部署

```bash
bash deploy/cloudflare-deploy.sh
```

脚本按顺序执行：

1. 校验配置、Node/uv/Python 版本、Cloudflare 登录状态
2. **生成音频切片**（首次约数分钟；已有 500+ 个则自动跳过）
3. `tools/stage.py v3` 铺装 `v3/public/`
4. 创建 D1 数据库，把 `database_id` 写回 `v3/wrangler.jsonc`
5. `shared/tools/export_d1.py` 生成 `migrations/0002_seed.sql`
6. `wrangler d1 migrations apply --remote` 应用迁移，并核对行数
7. `pywrangler deploy` 上传 Worker + 静态资源
8. 验收 `/api/lessons` 与 `/audio/sys/intro.mp3`

幂等，可重复运行。常用参数：

```bash
bash deploy/cloudflare-deploy.sh --skip-slices   # 跳过切片生成（日常更新用）
bash deploy/cloudflare-deploy.sh --dev           # 不上线，改为启动本地预览
```

### 首次运行预期输出

```
==> 读取配置
  [OK] D1 数据库名: dictation-v3
==> 音频切片
  [OK] 切片共 516 个
==> D1 数据库
  [OK] database_id: xxxxxxxx-xxxx-...
==> 应用数据库迁移（远端）
  课程 43 门，知识点 815 条
==> 部署到 Cloudflare
  [OK] 部署成功：https://dictation-v3.xxx.workers.dev
```

---

## 5. 绑定自定义域名

脚本若提示需手动绑定，两种做法任选。

**做法 A：Dashboard（推荐，最快）**

1. 打开 [Workers & Pages](https://dash.cloudflare.com/) → `dictation-v3`
2. Settings → Domains & Routes → Add → Custom Domain
3. 填 `v3.dictation.de5.net` → Add Domain

Cloudflare 自动配置 DNS 与证书，通常 1 分钟内生效。

**做法 B：写进 wrangler.jsonc**

在 `v3/wrangler.jsonc` 顶层加入：

```jsonc
"routes": [
  { "pattern": "v3.dictation.de5.net/*", "zone_name": "de5.net" }
]
```

再重新部署：

```bash
bash deploy/cloudflare-deploy.sh --skip-slices
```

---

## 6. 验收

浏览器打开 `https://v3.dictation.de5.net`，应看到听写界面并能播放音频。

命令行：

```bash
# 课程目录
curl -s https://v3.dictation.de5.net/api/lessons | head -c 200

# 出题：应返回 30 词 + 2 个多音字，每词带 audio_url
curl -s "https://v3.dictation.de5.net/api/generate_daily/3111" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['data']),'词, 多音字',len(d['polyphonic_section']),'个');print(d['data'][0])"

# 切片可访问
curl -sI https://v3.dictation.de5.net/audio/sys/intro.mp3 | head -3

# 提交批改（kp_id 换成上一步返回的真实 id）
curl -s -X POST https://v3.dictation.de5.net/api/submit_dictation \
  -H 'Content-Type: application/json' \
  -d '{"dictation_type":"daily","scope_id":3111,"results":[{"kp_id":1,"is_correct":true},{"kp_id":2,"is_correct":false}],"poly_ids":[]}'
# 应返回 {"status":"success","score":50.0,...}
```

---

## 7. 安全提醒

**V3 默认没有任何访问鉴权** —— 任何人拿到地址都能打开并写入数据。VPS 版靠 Caddy basic_auth 保护，Workers 上没有等价的内置口令。

建议启用 Cloudflare Zero Trust Access（免费版含 50 用户）：

1. Dashboard → Zero Trust → Access → Applications → Add an application
2. 选 Self-hosted，Application domain 填 `v3.dictation.de5.net`
3. 加一条 Policy：Action = Allow，Include = Emails → 填你的邮箱
4. 保存后访问该域名会先要求邮箱验证码

不启用的话，至少别把地址公开分享。

---

## 8. 日常运维

**实时日志**

```bash
cd v3 && npx wrangler tail
```

**查库**

```bash
cd v3
npx wrangler d1 execute dictation-v3 --remote \
  --command "SELECT COUNT(*) FROM dictation_history;"
```

**备份 D1**

```bash
cd v3
npx wrangler d1 export dictation-v3 --remote --output ../d1-backup-$(date +%F).sql
```

**代码或题库更新后重新部署**

```bash
git pull
bash deploy/cloudflare-deploy.sh --skip-slices
```

**题库新增词条后**（需要新切片）

```bash
git pull
bash deploy/cloudflare-deploy.sh        # 增量生成新切片 + 重新部署
```

**回滚**

```bash
cd v3
npx wrangler rollback
```

---

## 9. 成本

| 资源 | 免费配额 | 本应用用量 |
|---|---|---|
| Worker 请求 | 100,000/天 | 每次听写约 5 次 API 调用 |
| D1 读取 | 500 万行/天 | 每次出题约 100 行 |
| D1 写入 | 10 万行/天 | 每次提交约 60 行 |
| D1 存储 | 5 GB | < 5 MB |
| 静态资源请求 | 免费无限量 | 全部音频文件 |

单用户每天几次听写，远在免费额度内。

---

## 10. 故障排查

**`Missing D1 binding` / `no such table`**
`wrangler.jsonc` 的 `database_id` 仍是占位符，或迁移未应用：

```bash
cd v3 && npx wrangler d1 migrations apply dictation-v3 --remote
```

**音频 404**
`v3/public/audio/` 是空的（`public/` 不入 Git，每次部署前需 stage）：

```bash
python tools/stage.py v3
find v3/public/audio -name '*.mp3' | wc -l    # 应为数百
bash deploy/cloudflare-deploy.sh --skip-slices
```

**切片生成报 `errorCode 411`**
有道限流。调大间隔重跑，已生成的会自动跳过：

```bash
# 在 deploy/cloudflare.env 里把 TTS_INTERVAL 改成 2.0，然后
bash deploy/cloudflare-deploy.sh
```

**`import workers` 报错**
该模块只存在于 Workers 运行时。本地不能直接 `python v3/src/worker.py`，必须用：

```bash
cd v3 && uv run pywrangler dev
```

**`uv: command not found`**
uv 装了但没在 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**自定义域名 522 / 无响应**
刚绑定需等 1–2 分钟。仍不行则检查该域名是否橙云代理开启、Zone 是否与 `V3_ZONE` 一致。

**多音字每次都是同两个**
D1 库缺 `poly_ids` 列（老库）。重新应用迁移：

```bash
cd v3 && npx wrangler d1 migrations apply dictation-v3 --remote
```

`0001_initial.sql` 已含该列，全新库无此问题。

---

## 11. 删除部署

```bash
cd v3
npx wrangler delete                              # 删 Worker
npx wrangler d1 delete dictation-v3              # 删数据库（数据不可恢复）
```

删库前先导出备份（见第 8 节）。

---

## 附录：把主域名指向 V3

在 Dashboard 为 `dictation-v3` Worker 添加 `dictation.de5.net` 作为第二个 Custom Domain，或在 DNS 把 `dictation.de5.net` CNAME 到 `v3.dictation.de5.net`。
