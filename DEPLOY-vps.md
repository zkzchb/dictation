# VPS 部署指南（V1 / V2）

从 GitHub 拉取代码开始，到 HTTPS 可访问为止。全程使用一键脚本，密钥集中在脚本顶部占位符，拉取后手动填写再运行。

| 版本 | 端口 | 音频方案 | 需要 ffmpeg | 需要有道密钥 |
|---|---|---|---|---|
| V1 | 8888 | 运行时调有道 TTS，pydub 拼接 | 是 | 是 |
| V2 | 8889 | 预录切片，浏览器播放列表 | 仅录音工作台 | 仅生成切片时 |

两版共用一台 Ubuntu 24.04、一个 `caddy` 进程、一个 `dictation` 系统用户，数据库各自独立。

---

## 总览：五步

| 步骤 | 在哪执行 | 做什么 |
|---|---|---|
| 1 | 本地 | 生成音频切片（V2 必需） |
| 2 | 服务器 | 拉取代码 |
| 3 | 服务器 | 填写 `deploy/vps.env` 密钥 |
| 4 | 服务器 | 跑 `deploy/vps-install.sh` |
| 5 | 本地 | 上传切片（V2） + 验收 |

> 只部署 V1 可跳过第 1、5 步中的切片部分。

---

## 0. 前置条件

- Ubuntu 24.04，有 root 或 sudo
- 域名 A 记录已指向服务器公网 IP：
  - `v1.dictation.de5.net`（部署 V1 时）
  - `v2.dictation.de5.net`（部署 V2 时）
- 有道智云 APP_KEY / APP_SECRET（[控制台](https://ai.youdao.com/)）

验证 DNS 与本机 IP 一致：

```bash
dig +short v1.dictation.de5.net; dig +short v2.dictation.de5.net; curl -s https://api.ipify.org; echo
```

前两行应与最后一行相同。若不同，先改 DNS 再继续 —— Caddy 申请证书依赖它。

---

## 1. 本地：生成音频切片（V2 必需）

V2 播放预录切片，切片不入 Git（`.gitignore` 已排除），需在本地生成后上传。

完整步骤见 **[DEPLOY-local.md](DEPLOY-local.md)**。简版：

```bash
git clone https://github.com/zkzchb/dictation.git
cd dictation
cp deploy/local.env.example deploy/local.env
nano deploy/local.env          # 填有道密钥
bash deploy/local-install.sh
```

首次约需数分钟，生成 500+ 个 MP3 到 `shared/web/audio/`。增量执行，中断可重跑。

验证：

```bash
find shared/web/audio -name '*.mp3' | wc -l    # 应为 500+
ls shared/web/audio/sys/intro.mp3
```

> 只部署 V1 可跳过本步 —— V1 在运行时实时合成，不依赖预录切片。

---

## 2. 服务器：拉取代码

```bash
ssh root@你的服务器IP
```

```bash
apt update && apt install -y git
git clone https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
```

> 若 `/opt/dictation` 已存在旧部署，先备份数据库：
> `cp /opt/dictation/v1/dictation.db ~/v1-backup.db 2>/dev/null; cp /opt/dictation/v2/dictation.db ~/v2-backup.db 2>/dev/null`

---

## 3. 服务器：填写密钥

复制配置模板：

```bash
cp deploy/vps.env.example deploy/vps.env
nano deploy/vps.env
```

按注释填写。**必填项**：

| 变量 | 说明 |
|---|---|
| `DEPLOY_V1` / `DEPLOY_V2` | 是否部署该版本（`yes` / `no`） |
| `V1_DOMAIN` / `V2_DOMAIN` | 对应域名 |
| `YOUDAO_APP_KEY` / `YOUDAO_APP_SECRET` | V1 运行时合成必需；只部署 V2 可留占位符 |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | 访问口令。应用层无鉴权，靠它保护 |

保存后收紧权限（内含明文密钥）：

```bash
chmod 600 deploy/vps.env
```

---

## 4. 服务器：一键安装

```bash
sudo bash deploy/vps-install.sh
```

脚本按顺序完成：

1. 校验 `vps.env` 配置与域名解析
2. 安装系统包（python3-venv、sqlite3、ffmpeg、ufw、Caddy）
3. 创建 `dictation` 系统用户
4. 为启用的版本建 venv、装依赖
5. 初始化数据库（已存在则跳过并保留数据）
6. 写 `/etc/dictation/v{1,2}.env`（权限 600）
7. 装并启动 systemd 服务
8. 生成 Caddyfile（含 basic_auth）并 reload
9. 配置 ufw（放行 22/80/443，不暴露 8888/8889）
10. 装每日 3:00 数据库备份 cron
11. 打印健康检查结果

脚本幂等 —— 可重复运行，不会覆盖已有数据库。

完成后确认服务状态：

```bash
systemctl status dictation-v1 dictation-v2 --no-pager
```

首次访问域名时 Caddy 自动申请 Let's Encrypt 证书。观察签发：

```bash
journalctl -u caddy -f
```

看到 `certificate obtained successfully` 后按 Ctrl+C。

---

## 5. 本地：上传切片（V2）

回到本机（第 1 步生成切片的那台），一条命令同步：

```bash
bash deploy/sync-slices.sh root@你的服务器IP
```

脚本会 rsync 增量传输、修正远端属主、重启 `dictation-v2`，并核对两边文件数是否一致。

先看会传什么（不改动远端）：

```bash
bash deploy/sync-slices.sh root@你的服务器IP --dry-run
```

部署路径不是默认的 `/opt/dictation` 时用 `--path` 指定：

```bash
bash deploy/sync-slices.sh root@你的服务器IP --path /srv/dictation
```

---

## 6. 验收

浏览器打开 `https://v2.dictation.de5.net`，输入第 3 步设置的用户名密码，应看到听写界面并能播放音频。

命令行验收（`用户名:密码` 换成你的）：

```bash
# 课程目录：应返回 lesson_seq >= 3100 的课程
curl -su '用户名:密码' https://v2.dictation.de5.net/api/lessons | head -c 200

# 出题：应返回 30 词 + 2 个多音字
curl -su '用户名:密码' "https://v2.dictation.de5.net/api/generate_daily/3111" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['data']),'词, 多音字',len(d['polyphonic_section']),'个')"

# 切片可访问：应返回 200 与 audio/mpeg
curl -sI -u '用户名:密码' https://v2.dictation.de5.net/audio/sys/intro.mp3 | head -3
```

V1 音频合成（首次约 30 秒，之后命中缓存）：

```bash
curl -su '用户名:密码' -X POST https://v1.dictation.de5.net/api/generate_audio \
  -H 'Content-Type: application/json' \
  -d '[{"text":"诗人","pinyin":"shī rén"},{"text":"碧绿","pinyin":"bì lǜ"}]'
```

应返回 `audio_url` 与 `timeline`。

### 可选：预热 V1 缓存

V1 每次听写要连发 40+ 次 TTS 请求，容易被限流。若已上传 V2 切片，可零 API 调用预热：

```bash
ssh root@你的服务器IP
cd /opt/dictation
set -a && . /etc/dictation/v1.env && set +a
v1/venv/bin/python shared/tools/warm_v1_cache.py
chown -R dictation:dictation v1/tts_cache
```

---

## 7. 日常运维

```bash
# 状态与重启
systemctl status dictation-v1 dictation-v2 --no-pager
systemctl restart dictation-v2

# 实时日志
journalctl -u dictation-v2 -f

# Caddy
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy

# 磁盘占用
du -sh /opt/dictation/shared/web/audio /opt/dictation/v*/dictation.db
```

### 更新代码

```bash
cd /opt/dictation
systemctl stop dictation-v1 dictation-v2
git pull
sudo bash deploy/vps-install.sh     # 幂等，会重装依赖并重启
```

数据库不受影响（脚本检测到已存在即跳过初始化）。

### 新增词条后更新切片

```bash
# 本地：增量生成新词的切片，然后同步
bash deploy/local-install.sh --slices-only
bash deploy/sync-slices.sh root@你的服务器IP
```

### 数据库备份

脚本已装每日 3:00 自动备份到 `/var/backups/dictation/`，保留 30 天。手动备份：

```bash
/usr/local/bin/backup-dictation.sh
ls -lh /var/backups/dictation/v2/
```

---

## 8. 迁移已有数据

若本地已有含真实学习记录的 `dictation.db`：

```bash
# 本地：安全导出（勿直接 cp 运行中的库）
sqlite3 v2/dictation.db ".backup 'v2-upload.db'"
scp v2-upload.db root@你的服务器IP:/opt/dictation/v2/dictation.db
```

服务器上修正属主，并补 `poly_ids` 列（老库缺这列会导致多音字轮换失效）：

```bash
cd /opt/dictation
chown dictation:dictation v2/dictation.db
v2/venv/bin/python shared/tools/migrate_poly_ids.py v2/dictation.db
systemctl restart dictation-v2
```

`migrate_poly_ids.py` 幂等，只做 `ALTER TABLE ADD COLUMN`，不动现有数据。

---

## 9. 故障排查

**服务起不来** — `journalctl -u dictation-v2 -n 50`
常见：venv 路径错、数据库未初始化、`ProtectSystem=strict` 缺 `ReadWritePaths`。

**Caddy 拿不到证书** — DNS 未生效或 80 端口被挡：

```bash
dig +short v2.dictation.de5.net; ufw status
```

**V2 音频 404** — 切片未上传：

```bash
find /opt/dictation/shared/web/audio -name '*.mp3' | wc -l   # 应为数百
```

为 0 则回到第 5 步。

**V1 报 `ffmpeg not found`**

```bash
apt install -y ffmpeg && systemctl restart dictation-v1
```

**`attempt to write a readonly database`** — 属主不对：

```bash
chown dictation:dictation /opt/dictation/v*/dictation.db
```

**有道 `errorCode 411`** — 限流。V1 调大 `TTS_MIN_INTERVAL`：

```bash
echo 'TTS_MIN_INTERVAL=0.5' >> /etc/dictation/v1.env
systemctl restart dictation-v1
```

**多音字每次都是同两个** — 库缺 `poly_ids` 列，见第 8 节迁移命令。

---

## 10. 卸载

```bash
sudo bash deploy/vps-uninstall.sh
```

移除服务、Caddy 站点配置、系统用户与 cron。**保留** `/opt/dictation` 与 `/var/backups/dictation`，需要时手动删除。

---

## 附录：把主域名指向某个版本

确定正式版后，在 `/etc/caddy/Caddyfile` 末尾追加重定向：

```caddyfile
dictation.de5.net {
    redir https://v2.dictation.de5.net{uri} permanent
}
```

```bash
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```
