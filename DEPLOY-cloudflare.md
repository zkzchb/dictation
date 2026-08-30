# Cloudflare Workers + D1 部署 V3

V3 在 Cloudflare Python Workers 上运行 API，在 D1 保存学习记录，并把前端和所选内容包
的音频作为静态资源发布。部署在本地执行，不需要 VPS。

## 1. 前置条件

- Node.js 18 或更新版本；
- `npx wrangler login` 已登录 Cloudflare，或提供合适权限的 API Token；
- `uv`（脚本缺少时会安装）；
- 相邻检出的 `dictation` 与 `dictation-content` 仓库。

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone https://github.com/zkzchb/dictation-content.git
git clone https://github.com/zkzchb/dictation.git
cd dictation
npx wrangler login
```

## 2. 配置

```bash
cp deploy/cloudflare.env.example deploy/cloudflare.env
chmod 600 deploy/cloudflare.env
nano deploy/cloudflare.env
```

| 变量 | 说明 |
|---|---|
| `CONTENT_ROOT` | 外部内容包路径，默认相邻仓库的 `primary-3a` |
| `D1_DATABASE_NAME` | D1 名称，默认 `dictation-v3` |
| `D1_DATABASE_ID` | 已有数据库 UUID；保留占位符时自动创建/查找 |
| `V3_DOMAIN` | 可选自定义域名；留空使用 workers.dev |
| `V3_ZONE` | 使用自定义域名时的 Cloudflare Zone |

不同 pack id 应使用不同 D1 数据库。同一 pack id 的内容更新通过稳定知识点 ID upsert，
不会要求修改 Worker 源码。

## 3. 部署

```bash
bash deploy/cloudflare-deploy.sh
```

脚本会：

1. 验证 content pack、JSON 哈希、`tts.sha256` 与所有 MP3；
2. 把前端、音频和 `deployment.json` 铺装到忽略的 `v3/public`；
3. 创建或选择 D1，并临时配置绑定；
4. 生成课程/知识点 upsert SQL 和 content runtime SQL；
5. 应用 schema 迁移，再同步当前内容；
6. 部署 Worker 和静态资源；
7. 验证课程 API 与开场音频。

启动本地预览而不发布：

```bash
bash deploy/cloudflare-deploy.sh --dev
```

## 4. 验收

用脚本输出的 URL：

```bash
BASE_URL=https://your-worker.example.workers.dev
curl -fsS "$BASE_URL/api/lessons" | head -c 300
curl -fsSI "$BASE_URL/audio/sys/intro.mp3" | head
curl -fsS "$BASE_URL/deployment.json"
```

`deployment.json` 应包含本次程序提交、内容提交、内容版本、pack id 和 dataset SHA-256。

## 5. 自定义域名与访问控制

若 `V3_DOMAIN` 非空但 `wrangler.jsonc` 没有 routes，脚本会提示在 Cloudflare Dashboard 的
Workers & Pages → Settings → Domains & Routes 中添加 Custom Domain。

V3 程序本身没有登录鉴权。公开部署需要访问控制时，可在 Cloudflare Zero Trust Access
中建立 Self-hosted application，并用邮箱或身份提供商策略限制访问。

## 6. 更新与回滚

```bash
git pull --ff-only
git -C ../dictation-content pull --ff-only
bash deploy/cloudflare-deploy.sh
```

查看日志和数据库：

```bash
cd v3
npx wrangler tail
npx wrangler d1 execute dictation-v3 --remote \
  --command "SELECT COUNT(*) FROM dictation_history;"
```

Worker 代码回滚使用 Cloudflare 的部署版本；D1 更新前应另行导出备份：

```bash
cd v3
npx wrangler d1 export dictation-v3 --remote --output ../d1-backup.sql
```

D1 和内容包必须成对恢复：代码回滚不会自动回滚数据库或静态内容。
