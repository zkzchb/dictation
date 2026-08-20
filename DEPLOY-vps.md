# VPS 部署指南（V1 / V2）

从 GitHub 拉取代码开始，到 HTTPS 可访问为止。正式 V2 已把教材 JSON 与标准 TTS
封装在 `chinese/3a`，全新安装不再要求先在本地生成、上传音频，也不需要有道密钥。
详细的冷启动与真人录音覆盖规范见
[V2 可复现安装规范](docs/V2-REPRODUCIBLE-INSTALL.md)。

| 版本 | 端口 | 音频方案 | 需要 ffmpeg | 需要有道密钥 |
|---|---|---|---|---|
| V1 | 8888 | 运行时调有道 TTS，pydub 拼接 | 是 | 是 |
| V2 | 8889 | 仓库教材包中的预录切片 | 仅录音工作台 | 否 |

两版共用一台 Ubuntu 24.04、一个 `caddy` 进程、一个 `dictation` 系统用户，数据库各自独立。

---

## 总览：V2 三步

| 步骤 | 在哪执行 | 做什么 |
|---|---|---|
| 1 | 服务器 | 拉取包含 `chinese/3a` 教材包的代码 |
| 2 | 服务器 | 填写 `deploy/vps.env` 域名和访问口令 |
| 3 | 服务器 | 跑 `deploy/vps-install.sh` 并验收 |

> V1 或自定义教材仍可使用后文的 TTS 生成工具；正式 V2 冷启动无需执行。

---

## 0. 前置条件

- Ubuntu 24.04，有 root 或 sudo
- 域名 A 记录已指向服务器公网 IP：
  - `v1.dictation.de5.net`（部署 V1 时）
  - `v2.dictation.de5.net`（部署 V2 时）
- 仅部署 V1 或重新生成 TTS 时，需要有道智云 APP_KEY / APP_SECRET
  （[控制台](https://ai.youdao.com/)）；标准 V2 冷启动不需要。

验证 DNS 与本机 IP 一致：

```bash
dig +short v1.dictation.de5.net; dig +short v2.dictation.de5.net; curl -s https://api.ipify.org; echo
```

前两行应与最后一行相同。若不同，先改 DNS 再继续 —— Caddy 申请证书依赖它。

---

## 1. 本地：生成音频切片（仅维护/自定义教材）

正式 `chinese/3a` 已随 Git 包含完整切片。本节只用于维护者重新制作标准 TTS，或
开发尚未进入仓库的新教材；普通 V2 安装请直接从第 2 节开始。

完整步骤见 **[DEPLOY-local.md](DEPLOY-local.md)**。简版：

```bash
git clone https://github.com/zkzchb/dictation.git
cd dictation
cp deploy/local.env.example deploy/local.env
nano deploy/local.env          # 填有道密钥
bash deploy/local-install.sh
```

首次约需数分钟，生成完整的 894 个 MP3。增量执行，中断可重跑。

验证：

```bash
find shared/web/audio -name '*.mp3' | wc -l    # 应为 894
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

## 3. 服务器：填写配置

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

## 5. 本地：同步运行音频（维护/迁移用）

标准 V2 安装已经从仓库复制完整音频，不需要执行本节。只有迁移运行目录中的真人
录音、维护自定义教材，或兼容旧部署时才使用：

```bash
bash deploy/sync-slices.sh root@你的服务器IP
```

脚本会 rsync 增量传输、修正远端属主、重启 `dictation-v2`，并严格核对 869 个词条
和 25 个系统提示音。若目标是新站只导入真人录音、不带 Check 状态，请使用
[V2 可复现安装规范](docs/V2-REPRODUCIBLE-INSTALL.md)中的真人覆盖包，不要直接同步
整个运行目录。

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

> 忘了密码？唯一的明文副本在服务器上：`grep BASIC_AUTH /opt/dictation/deploy/vps.env`。
> `/etc/caddy/Caddyfile` 里只有 bcrypt 哈希，不可逆推。真丢了就改
> `deploy/vps.env` 里的 `BASIC_AUTH_PASSWORD` 后重跑 `bash deploy/vps-install.sh`
> —— 脚本幂等，数据库与切片都不动。

命令行验收（`用户名:密码` 换成你的）。**在服务器上可直连 uvicorn 端口跳过
basic_auth**，调接口时比走 Caddy 省事：

```bash
curl -s http://127.0.0.1:8889/api/lessons | head -c 200      # V2，无需密码
curl -s http://127.0.0.1:8888/api/lessons | head -c 200      # V1，无需密码
```

从外部走公网则需带上口令：

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

V1 每次听写要连发 40+ 次 TTS 请求，容易被限流。若已安装 V2 切片，可零 API 调用预热：

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

**首次拉取前需加一次例外**。安装脚本把 `/opt/dictation` 属主设为 `dictation`，而你以 root 登录，Git 2.35.2+ 会拒绝操作他人拥有的仓库：

```
fatal: detected dubious ownership in repository at '/opt/dictation'
```

```bash
# 只需执行一次
git config --global --add safe.directory /opt/dictation
```

之后每次更新（**`chown` 不能省**——root 拉下来的新文件属主是 root，
而服务以 `dictation` 身份运行且带 `ProtectSystem=strict`，会读不到新代码）：

```bash
cd /opt/dictation
git pull
chown -R dictation:dictation /opt/dictation
systemctl restart dictation-v1 dictation-v2
```

依赖或配置也变了时（新增 Python 包、改了 Caddy 规则、动过 `deploy/vps.env`），
改跑一键脚本——它幂等，并且自己会处理属主：

```bash
cd /opt/dictation && git pull && sudo bash deploy/vps-install.sh
```

数据库不受影响（脚本检测到已存在即跳过初始化）。

### 新增词条后更新切片

```bash
# 本地：增量生成新词的切片，然后同步
bash deploy/local-install.sh --slices-only
bash deploy/sync-slices.sh root@你的服务器IP
```

### 录音工作台：邀请老师录真人音频

浏览器只在**安全上下文**（HTTPS / `localhost`）才暴露 `navigator.mediaDevices`，
所以局域网 HTTP 地址（`http://192.168.x.x:8889/studio`）**录不了音**——页面能打开，
一点录音就报非安全来源。VPS 有 Caddy 自动签发的证书，加上 basic_auth 挡住陌生人，
是录音最合适的环境，也便于邀请校外的老师参与。

```bash
# 1. 把地址和口令给老师（标准 TTS 已随一键安装完成）
#    https://v2.你的域名/studio

# 2. 老师录完，拉回本地（会先备份本地切片，可回退）
bash deploy/sync-slices.sh root@你的服务器IP --pull

# 3. 同步到 Cloudflare（若已部署 V3）
bash deploy/cloudflare-deploy.sh --skip-slices
```

`--pull` 是反向同步。真人录音与 TTS 占位切片**同名同路径**（文件名是
`md5(词面)[:12]`），拉回会直接覆盖，所以脚本默认先把本地 `shared/web/audio/`
备份成 `audio.bak_<时间戳>`。加 `--no-backup` 跳过，加 `--dry-run` 先看会拉什么。

哪些词已由真人录过记在 `shared/web/audio/.recorded.json`。之所以需要这份台账：
切片文件名是内容 MD5，真人录音和 TTS 占位在磁盘上无从区分，只看文件是否存在
的话「待录」永远是空集。它跟切片放在一起，`sync-slices.sh` 会一并同步，
本地与 VPS 对录音进度的认知保持一致。

`/studio` 是唯一会往磁盘写文件的接口，由 `STUDIO_ENABLED` 控制（VPS 上默认开启）。
不录音时建议关掉：

```bash
# 改 deploy/vps.env 设 STUDIO_ENABLED=0，然后
cd /opt/dictation && sudo bash deploy/vps-install.sh
```

> V1 的 TTS 缓存不会自动跟着更新。老师重录后若要 V1 也用新音频，
> 需重跑一次预热（见第 6 节「预热 V1 缓存」）。V2/V3 直接读切片，不受影响。

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

**V2 音频 404** — 运行音频没有从教材包正确安装：

```bash
python3 /opt/dictation/shared/tools/audio_bundle.py inventory \
  --audio-dir /opt/dictation/shared/web/audio
```

若 `complete` 不是 `true`，重新运行 `sudo bash deploy/vps-install.sh`；仍失败则检查
`chinese/3a/tts.sha256` 与 Git 工作区是否完整。

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
