# Cloudflare Workers + D1 部署 V3

V3 在 Cloudflare Python Workers 上运行 API，在 D1 保存学习记录，并把前端和所选内容包
的音频作为静态资源发布。部署在本地执行，不需要 VPS。

## 1. 前置条件

- Node.js 22 或更新版本；
- `npx wrangler login` 已登录 Cloudflare，或提供合适权限的 API Token；
- `uv >= 0.12.3`（脚本缺少时会安装；版本过低时先升级）；
- 相邻检出的 `dictation` 与 `dictation-content` 仓库。

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git
git clone https://github.com/zkzchb/dictation.git
git -C dictation switch --detach 586ffecf23efb514fdf7094d603081814963dc33
cd dictation
```

以上固定提交是 Draft PR #30 当前通过 CI 的 Workers 全新部署入口；冻结的
`v2.1.0-rc.1` 标签保持不变，不包含本页新增的 `--fresh` 防误覆盖流程。

部署脚本会先安装仓库锁定的 Wrangler，再检查登录状态；未登录时才打开浏览器授权。

## 2. 配置

```bash
cp deploy/cloudflare.env.example deploy/cloudflare.env
chmod 600 deploy/cloudflare.env
nano deploy/cloudflare.env
```

| 变量 | 说明 |
|---|---|
| `CONTENT_ROOT` | 外部内容包路径，默认相邻仓库的 `primary-3a` |
| `V3_WORKER_NAME` | 已有/更新部署的 Worker 名；全新部署时由脚本交互询问 |
| `D1_DATABASE_NAME` | 已有/更新部署的 D1 名；全新部署时与 Worker 同名 |
| `D1_DATABASE_ID` | 已有数据库 UUID；保留占位符时自动创建/查找 |
| `V3_DOMAIN` | 已有/更新部署的 Custom Domain；全新部署时由脚本交互询问 |

不同 pack id 应使用不同 D1 数据库。同一 pack id 的内容更新通过稳定知识点 ID upsert，
不会要求修改 Worker 源码。

## 3. 部署

创建全新的 Worker 与同名 D1：

```bash
bash deploy/cloudflare-deploy.sh --fresh
```

脚本会依次询问 Worker 名称和可选的 Custom Domain，并要求输入
`CREATE <Worker 名称>`。`--fresh` 会在任何远端写入前确认同名 Worker 和 D1
均不存在；若名称已被占用则停止，不会更新已有项目。

如全新部署在 D1 创建后、Worker 发布前中断，保留相同名称并改用不带 `--fresh`
的命令即可续跑；不要删除或另建数据库。

更新已在 `cloudflare.env` 中配置的现有部署：

```bash
bash deploy/cloudflare-deploy.sh
```

脚本会：

1. 验证 content pack、JSON 哈希、`tts.sha256` 与所有 MP3；
2. 把前端、音频和 `deployment.json` 铺装到忽略的 `v3/public`；
3. 临时写入 Worker 名称、D1 绑定和可选 Custom Domain；
4. 创建或选择 D1；
5. 生成课程/知识点 upsert SQL 和 content runtime SQL；
6. 应用 schema 迁移，再同步当前内容；
7. 部署 Worker 和静态资源；
8. 验证课程 API 与开场音频。

脚本退出时会恢复仓库中的 `v3/wrangler.jsonc`，不会把 D1 UUID、Worker 名称
或私有域名留在工作树中。`v3/package-lock.json` 固定 Wrangler，`v3/uv.lock`
固定本地 `pywrangler` 工具链，`v3/pylock.toml` 固定 Worker 内的 Pyodide 依赖；
生成的 `node_modules/` 和 `python_modules/` 不进入 Git。工具链会在登录 Cloudflare
和创建 D1 之前完成校验。

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

全新部署时输入域名（例如 `worker.example.net`）后，脚本会临时加入精确主机名的
Custom Domain 配置并随 Worker 一起部署。Custom Domain 不是普通 route：配置使用
`"pattern": "worker.example.net"` 和 `"custom_domain": true`，不带 `/*`；Cloudflare
会自动创建 DNS 记录并签发证书。目标主机名必须属于当前账户中的活动 Cloudflare Zone，
且不能已有冲突的 CNAME 或其他 Custom Domain 绑定。

V3 程序本身没有登录鉴权。公开部署需要访问控制时，可在 Cloudflare Zero Trust Access
中建立 Self-hosted application，并用邮箱或身份提供商策略限制访问。

## 6. 更新与回滚

```bash
git fetch --tags
git switch --detach 586ffecf23efb514fdf7094d603081814963dc33
git -C ../dictation-content fetch --tags
git -C ../dictation-content switch --detach content-v1.0.0
bash deploy/cloudflare-deploy.sh
```

查看日志和数据库：

```bash
cd v3
npx --no-install wrangler tail --name <worker-name>
npx --no-install wrangler d1 execute <d1-name> --remote \
  --command "SELECT COUNT(*) FROM dictation_history;"
```

Worker 代码回滚使用 Cloudflare 的部署版本；D1 更新前应另行导出备份：

```bash
cd v3
npx --no-install wrangler d1 export <d1-name> --remote --output ../d1-backup.sql
```

D1 和内容包必须成对恢复：代码回滚不会自动回滚数据库或静态内容。
