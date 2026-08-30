# Ubuntu 本地部署 V2

本地安装使用两个相邻 Git 仓库：`dictation` 提供稳定程序，`dictation-content` 提供所选
教材包和录音。SQLite、可写录音与生成网页保存在 `.runtime/local`，不会写回任一源码目录。

## 1. 准备两个仓库

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git
git clone --branch v2.1.0-rc.1 https://github.com/zkzchb/dictation.git
cd dictation
```

默认目录应为：

```text
dictation-workspace/
├── dictation/
└── dictation-content/
    └── packs/zh-cn/primary-3a/
```

## 2. 配置

```bash
cp deploy/local.env.example deploy/local.env
nano deploy/local.env
```

主要变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CONTENT_ROOT` | `../dictation-content/packs/zh-cn/primary-3a` | 外部内容包 |
| `STATE_ROOT` | `.runtime/local` | 数据库、运行音频、录音台账和部署记录 |
| `V2_PORT` | `8889` | 本地端口 |
| `BIND_HOST` | `127.0.0.1` | 只允许本机访问 |
| `STUDIO_ENABLED` | `1` | 是否启用录音工作台 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 打卡和记录所用时区 |

使用 `BIND_HOST=0.0.0.0` 会让同一局域网的设备访问应用，但 V2 自身没有登录鉴权。
在未配置反向代理鉴权时，建议同时设 `STUDIO_ENABLED=0`。

## 3. 校验或安装

只验证程序、内容包、哈希和音频清单：

```bash
bash deploy/local-install.sh --check-only
```

安装依赖、初始化/同步数据库并直接启动：

```bash
bash deploy/local-install.sh --serve
```

安装器会：

1. 验证 `dataset.json`、JSON 哈希、`tts.sha256` 和全部 MP3；
2. 建立 `v2/venv` 并安装固定依赖；
3. 在 `.runtime/local/v2` 建立 SQLite 数据库；
4. 对同一 pack id 执行稳定 ID 的追加同步；
5. 把公共前端与内容音频铺装到 `.runtime/local/web`；
6. 写入 `.runtime/local/web/deployment.json`。

浏览器打开 `http://localhost:8889`。命令行验收：

```bash
curl -fsS http://127.0.0.1:8889/api/health
curl -fsS http://127.0.0.1:8889/api/lessons | head -c 300
curl -fsSI http://127.0.0.1:8889/audio/sys/intro.mp3 | head
```

## 4. 常驻服务

```bash
bash deploy/local-install.sh --install-service
systemctl status dictation-local-v2 --no-pager
journalctl -u dictation-local-v2 -f
```

移除服务但保留全部数据：

```bash
bash deploy/local-install.sh --uninstall-service
```

## 5. 更新

程序和内容分别检出已审核的新标签，然后重跑安装器：

```bash
git fetch --tags
git switch --detach v2.1.0-rc.1
git -C ../dictation-content fetch --tags
git -C ../dictation-content switch --detach content-v1.0.0
bash deploy/local-install.sh --install-service
```

更新内容时，安装器会保留已登记的真人录音，并只用内容包基线覆盖未登记的音频。若新内容
移除了已有知识点 ID，或会让已有录音失去对应词条，更新会停止并给出错误，不会静默删数据。

## 6. 录音工作台

在本机浏览器访问 `http://localhost:8889/studio`。`localhost` 属于浏览器允许麦克风的
安全上下文；通过普通 HTTP 局域网 IP 访问时，浏览器通常会拒绝麦克风权限。

录音写入 `.runtime/local/web/audio`，不自动提交到内容仓库。确认录音后，应把对应 MP3
导入 `dictation-content`，更新 `tts.sha256` 和 `dataset.json`，再发布新的内容版本。

## 7. 数据位置

| 路径 | 内容 |
|---|---|
| `v2/venv` | Python 依赖，可重建 |
| `.runtime/local/v2/dictation.db` | 学习记录与内容索引 |
| `.runtime/local/web/audio` | 当前运行音频和录音台账 |
| `.runtime/local/web/deployment.json` | 程序/内容组合记录 |
| `deploy/local.env` | 本机部署设置，不提交 |

备份至少应包含 `.runtime/local/v2/dictation.db` 与 `.runtime/local/web/audio`。
