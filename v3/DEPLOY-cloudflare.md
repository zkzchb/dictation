# 听写小助手 V3 —— Cloudflare Workers + D1 部署指南

V3 域名：`v3.dictation.de5.net`

整个应用跑在 Cloudflare 边缘上：API 是 Python Worker（FastAPI + D1），前端和音频切片是 Workers 静态资源（免费、不限量请求、自动 CDN）。部署后没有服务器需要维护。

架构：

```
浏览器
  └─ HTTPS ──> Cloudflare Edge
                ├─ /api/*     → Python Worker（FastAPI + D1）
                ├─ /audio/*   → 静态资源（预录切片，免费无限量）
                └─ /          → 静态资源（index.html）
```

---

## 前置条件

- Node.js ≥ 18（用于 wrangler CLI）
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（Python 包管理，pywrangler 要求）
- Cloudflare 账号，已登录 `npx wrangler login`
- `v3.dictation.de5.net` 的 DNS 由 Cloudflare 托管（橙云开启）

---

## 1. 安装工具

```bash
# wrangler（Cloudflare CLI）
npm install -g wrangler

# uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 验证
wrangler --version && uv --version
```

---

## 2. 生成音频切片（如尚未生成）

切片只需在本地生成一次，后续可增量更新。填入有道密钥后执行：

```bash
YOUDAO_APP_KEY=你的AppKey YOUDAO_APP_SECRET=你的AppSecret \
  python shared/gen_slices.py
```

约 502 个词条 + 14 个系统提示音，首次约需数分钟。
切片写入 `shared/web/audio/`，按内容 MD5 命名，增量更新。

---

## 3. 铺装 V3 静态资源（stage）

```bash
python tools/stage.py v3
```

这会把 `shared/web/index.html` 和 `shared/web/audio/` 复制到 `v3/public/`。
`v3/public/` 已列入 `.gitignore`——它是部署前的中间产物，不提交。每次部署前运行一次。

验证：

```bash
ls v3/public/index.html v3/public/audio/sys/intro.mp3
```

---

## 4. 创建 D1 数据库

```bash
npx wrangler d1 create dictation-v3
```

命令会输出类似：

```
✅ Successfully created DB 'dictation-v3'
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

把 `database_id` 填入 `v3/wrangler.jsonc`，替换 `REPLACE_WITH_YOUR_D1_DATABASE_ID`：

```jsonc
"d1_databases": [
  {
    "binding": "DB",
    "database_name": "dictation-v3",
    "database_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  // ← 填这里
  }
]
```

---

## 5. 生成 D1 种子 SQL

```bash
python shared/tools/export_d1.py
```

这会读取 `shared/data/` 中的 JSON 题库，生成 `v3/migrations/0002_seed.sql`（622 条知识点 + 38 条课程）。

当前迁移文件顺序：
- `0001_initial.sql` — 建表 schema
- `0002_seed.sql` — 题库数据（上一步生成）

---

## 6. 应用数据库迁移

```bash
cd v3

# 远端数据库（正式部署）
npx wrangler d1 migrations apply dictation-v3

# 或先在本地 dev 环境验证
npx wrangler d1 migrations apply dictation-v3 --local
```

验证：

```bash
npx wrangler d1 execute dictation-v3 \
  --command "SELECT COUNT(*) AS kp FROM knowledge_points; SELECT COUNT(*) AS lessons FROM lessons;"
```

应看到 622 和 38。

---

## 7. 本地开发预览

```bash
cd v3
uv run pywrangler dev
```

开启本地开发服务器，访问 `http://localhost:8787`。所有 API 接口和静态资源均可测试。

> D1 在本地 dev 模式下使用本地 SQLite 副本，不连接远端数据库。

---

## 8. 部署到 Cloudflare

```bash
cd v3
uv run pywrangler deploy
```

wrangler 会自动：
1. 上传 `src/worker.py` 及其依赖
2. 上传 `public/` 中的所有静态资源（index.html + 音频切片）
3. 绑定 D1 数据库
4. 输出部署 URL

---

## 9. 配置自定义域名

部署成功后，在 Cloudflare Dashboard 为 Worker 添加自定义域名：

1. 打开 Workers & Pages → `dictation-v3`
2. Settings → Domains & Routes → Add Custom Domain
3. 输入 `v3.dictation.de5.net`，Cloudflare 自动配置路由和证书

或者用 wrangler.jsonc 声明（routes 字段）：

```jsonc
"routes": [
  { "pattern": "v3.dictation.de5.net/*", "zone_name": "de5.net" }
]
```

---

## 10. 验收

```bash
# 课程接口
curl -s https://v3.dictation.de5.net/api/lessons | head -c 150

# 出题接口（应含 audio_url）
curl -s "https://v3.dictation.de5.net/api/generate_daily/1?mode=daily" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); w=d['data'][0]; print(len(d['data']),'词:', w['target'], w['audio_url'])"

# 切片可访问
curl -sI https://v3.dictation.de5.net/audio/sys/intro.mp3 | head -3

# 提交批改（把 kp_id 换成上一步返回的真实 id）
curl -s -X POST https://v3.dictation.de5.net/api/submit_dictation \
  -H 'Content-Type: application/json' \
  -d '{"dictation_type":"daily","scope_id":1,"results":[{"kp_id":1,"is_correct":true},{"kp_id":2,"is_correct":false}]}'
# 应返回 {"status":"success","score":50.0,...}
```

---

## 11. 成本参考

| 资源 | 免费配额 | V3 用量 |
|---|---|---|
| Worker 请求 | 100,000/天 | 每次听写 ~5 次 API 调用 |
| D1 读取 | 25M 行/天 | 每次出题 ~100 行 |
| D1 写入 | 100K 行/天 | 每次提交 ~60 行 |
| 静态资源请求 | **免费无限量** | 所有音频文件 |
| 静态资源存储 | 100K 文件，每文件 25MB | ~516 个文件，<5MB |

正常使用完全在免费配额内。

---

## 12. 更新切片（增量）

题库 JSON 新增词条后：

```bash
# 1. 增量生成新切片（已有文件不重新生成）
python shared/gen_slices.py

# 2. 重新 stage
python tools/stage.py v3

# 3. 重新导出种子 SQL
python shared/tools/export_d1.py

# 4. 应用新迁移
cd v3 && npx wrangler d1 migrations apply dictation-v3

# 5. 重新部署（更新静态资源）
cd v3 && uv run pywrangler deploy
```

---

## 13. 常见问题

**`error: Missing D1 binding`**：`wrangler.jsonc` 里的 `database_id` 还是占位符，或未运行 `wrangler d1 create`。

**切片 404**：`tools/stage.py v3` 未运行，`v3/public/audio/` 是空的。

**D1 迁移失败 `table already exists`**：初始 schema 已应用，但再次 apply 时冲突。用 `--local` 先测；或在 migration SQL 里确认用 `CREATE TABLE IF NOT EXISTS`（0001_initial.sql 已正确使用）。

**`import workers` 报错（本地 Python 环境）**：这个模块只在 Workers 运行时中存在，本地 `python v3/src/worker.py` 会出错——这是正常的，必须用 `uv run pywrangler dev` 在 Workers 沙箱里运行。

**`response_header_timeout` 相关的报错**：V3 不需要这个配置（Cloudflare 对 Worker 执行时间的限制来自 CPU 时间，而不是超时）。等待网络的时间（D1 查询、fetch）不计入 CPU 时间。

---

## 附录：将 dictation.de5.net 切换到 V3

在 Cloudflare Dashboard 为 `dictation-v3` Worker 添加 `dictation.de5.net` 作为自定义域名，或在 DNS 将 `dictation.de5.net` CNAME 到 `v3.dictation.de5.net`。
