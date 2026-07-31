# 听写小助手 V1 —— Ubuntu VPS 部署指南（Caddy 版）

面向一台全新安装的 Ubuntu 22.04 / 24.04 LTS，从零到可用。

本文部署的是 **V1**，对外域名 `v1.dictation.de5.net`。

后续 V2、V3 计划部署在**同一台机器**上，因此本文所有路径、服务名、端口都带
版本号做隔离，v2/v3 直接照抄改数字即可，不会与 v1 冲突。等某个版本确定为正式
版后，把 `dictation.de5.net` CNAME 过去即可切换——具体见「附录 D」。

最终架构：

```
浏览器 ──HTTPS──> Caddy ┬─ v1.dictation.de5.net ┬─ /        → 前端 dictation_www/
                        │                       ├─ /api/*   → 127.0.0.1:8888 (uvicorn)
                        │                       └─ /audio/* → mp3 静态文件
                        ├─ v2.dictation.de5.net → …（将来，端口 8889）
                        └─ v3.dictation.de5.net → …（将来，端口 8890）
```

关键设计：uvicorn **只监听 127.0.0.1**，不对外暴露 8888 端口；对外只开 80/443，
全部流量经 Caddy。前端 `API_BASE` 为空字符串，靠同源访问 `/api`，因此无需配置 CORS。

V1 的约定路径：

| 用途 | 路径 |
|---|---|
| 代码与虚拟环境 | `/opt/dictation/v1` |
| 数据库 | `/opt/dictation/v1/dictation.db` |
| 成品音频（对外可访问） | `/opt/dictation/v1/audio` |
| TTS 分词缓存（不对外） | `/opt/dictation/v1/tts_cache` |
| 密钥环境文件 | `/etc/dictation/v1.env` |
| systemd 服务名 | `dictation-v1` |
| 本地监听端口 | `8888` |
| Caddy 配置 | `/etc/caddy/Caddyfile` |
| 数据库备份 | `/var/backups/dictation/v1` |

---

## 0. 前置条件

- 一台 Ubuntu 22.04 或 24.04 的 VPS，有 root 或 sudo 权限
- `v1.dictation.de5.net` 已解析（A 记录）到该 VPS 公网 IP。Caddy 的自动 HTTPS
  依赖 ACME 校验，域名必须先解析生效，否则申请证书会失败
- 有道智云的 `APP_KEY` / `APP_SECRET`（TTS 语音合成用）

先确认域名已生效：

```bash
dig +short v1.dictation.de5.net
```

返回的 IP 必须与你 VPS 的公网 IP 一致，再继续往下。顺手核对一下本机公网 IP：

```bash
curl -s https://api.ipify.org && echo
```

两者一致才继续。

> 依赖组合 fastapi 0.104.1 + pydantic 2.5.2 + requests 2.31.0 已在 Python 3.12 上验证可正常启动。
> Ubuntu 24.04 自带 Python 3.12，22.04 自带 3.10，两者都满足要求（代码未使用 3.11+ 语法）。

## 1. 系统初始化与依赖安装

以 root 登录后先更新系统：

```bash
apt update && apt upgrade -y
```

安装运行所需的系统包：

```bash
apt install -y python3 python3-venv python3-pip ffmpeg sqlite3 ufw curl debian-keyring debian-archive-keyring apt-transport-https
```

其中 **ffmpeg 是必须的**：`pydub` 靠它解码和拼接 MP3，缺了它音频接口会在运行时报错
（出题和算分接口不受影响，因为代码里 pydub 是延迟 import 的）。

确认版本符合要求：

```bash
python3 --version && ffmpeg -version | head -1 && sqlite3 --version
```

Python 应 ≥ 3.10，SQLite 应 ≥ 3.30（代码中的 `NULLS LAST` 语法需要）。

> 这一步和下一步（装 Caddy）是**全机共用**的，将来部署 v2/v3 时不必重做。

---

## 2. 安装 Caddy

用官方 apt 源装，这样能跟随系统更新，也自带 systemd 单元：

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
```

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
```

```bash
apt update && apt install -y caddy
```

确认版本（后面的 `basic_auth` 指令名跟版本有关）：

```bash
caddy version
```

装好后 Caddy 已自动启动并托管在 systemd 下，服务名就是 `caddy`，
以 `caddy` 用户运行，配置文件在 `/etc/caddy/Caddyfile`。

---

## 3. 创建专用系统用户

不要用 root 跑 Web 应用。创建一个无登录权限的专用账户：

```bash
adduser --system --group --home /opt/dictation --shell /usr/sbin/nologin dictation
```

**三个版本共用这一个 `dictation` 用户**——它们是同一个应用的不同版本，
没必要各自开账户；隔离靠目录和端口，不靠用户。

（Caddy 自己的 `caddy` 用户由 apt 包创建，无需处理。）

---

## 4. 上传代码

在服务器上先建好目录骨架：

```bash
mkdir -p /opt/dictation/v1
```

在**本地机器**上执行，把项目传到 v1 目录（把 `YOUR_VPS_IP` 换成你的 IP）：

```bash
rsync -avz --exclude 'venv/' --exclude '__pycache__/' --exclude '_testenv/' --exclude '*.db' --exclude '*.mp3' --exclude '.env' /d/claude/dictation_app/ root@YOUR_VPS_IP:/opt/dictation/v1/
```

> 注意排除了 `venv/`——原项目里的 venv 是在 Linux 上创建后被复制过的，
> 里面记录的是 `/root/dictation_app/venv` 的绝对路径，不可复用，必须在服务器上重建。
>
> 也排除了 `*.db`。如果你想把本地已有的学习记录一起带上去，见文末「附录 A」。

回到**服务器**，确认关键文件都到齐了：

```bash
ls /opt/dictation/v1/{main.py,init_db.py,requirements.txt,lessons_2b.json,kp_part0.json} /opt/dictation/v1/dictation_www/index.html
```

题库 JSON（`lessons_2b.json` 和 `kp_part0~3.json`）必须存在，下一步初始化数据库要读它们。

---

## 5. 建立虚拟环境并安装 Python 依赖

```bash
cd /opt/dictation/v1 && python3 -m venv venv && ./venv/bin/pip install --upgrade pip && ./venv/bin/pip install -r requirements.txt
```

验证四个核心包都能导入：

```bash
cd /opt/dictation/v1 && ./venv/bin/python -c "import fastapi, pydantic, requests, pydub; print('依赖 OK')"
```

> 每个版本用**各自独立的 venv**。这正是同机多版本的意义所在——将来 v2 想升级
> fastapi 或换 TTS 库，不会牵动正在服役的 v1。

---

## 6. 初始化数据库

`init_db.py` 会建表并灌入 622 条知识点、38 条课程目录，同时把第 0 课
（上学期核心错词）注入错题本做冷启动。它默认在**当前工作目录**下创建
`dictation.db`，所以必须 `cd` 到项目目录再执行：

```bash
cd /opt/dictation/v1 && ./venv/bin/python init_db.py
```

预期输出里应看到灌入条数和 `🎉 生产级数据库完整初始化完毕！`。

> ⚠️ **这一步只在全新部署时做一次。** 脚本会 DROP 所有表，包含用户的听写记录和
> 错题本。为防误操作，脚本检测到 `dictation.db` 已存在时会直接拒绝退出；
> 确实要重建才需要加 `--force`，且加之前务必先备份。

检查数据是否正常落库：

```bash
sqlite3 /opt/dictation/v1/dictation.db "SELECT COUNT(*) AS 知识点 FROM knowledge_points; SELECT COUNT(*) AS 课程 FROM lessons; SELECT COUNT(*) AS 冷启动错词 FROM user_memory;"
```

> **各版本数据库相互独立。** 这是有意的：版本间 schema 可能演进，共用一个库会让
> 旧版本读到不认识的结构。代价是学习记录不通用——真要让 v2 继承 v1 的历史，
> 就按「附录 A」的办法把 v1 的库复制过去，而不是让两者指向同一个文件。

---

## 7. 配置密钥

密钥通过环境文件注入，不写在代码里。创建目录与文件：

```bash
mkdir -p /etc/dictation && touch /etc/dictation/v1.env && chmod 600 /etc/dictation/v1.env && chown root:root /etc/dictation/v1.env
```

编辑：

```bash
nano /etc/dictation/v1.env
```

填入以下内容（把有道的两个值换成你自己的）：

```
YOUDAO_APP_KEY=你的AppKey
YOUDAO_APP_SECRET=你的AppSecret
AUDIO_OUTPUT_DIR=/opt/dictation/v1/audio
AUDIO_CACHE_DIR=/opt/dictation/v1/tts_cache
```

> 这里特意把 TTS 分词缓存目录（`tts_cache`）放在成品音频目录（`audio`）**之外**。
> 代码默认会把缓存放在 `audio/cache` 子目录下，而 Caddy 对外暴露整个 `/audio/*`，
> 那样缓存片段也会变成公开可访问。分开放更干净。
>
> 密钥格式无需引号，systemd 的 `EnvironmentFile` 会把整行值原样读入。

将来 v2/v3 各自建 `/etc/dictation/v2.env`、`v3.env`。有道密钥可以填一样的
（同一个账号的额度），但音频路径必须各指向自己的目录。

---

## 8. 创建音频目录并设置权限

```bash
mkdir -p /opt/dictation/v1/audio /opt/dictation/v1/tts_cache
chown -R dictation:dictation /opt/dictation
chmod 755 /opt/dictation /opt/dictation/v1 /opt/dictation/v1/audio /opt/dictation/v1/dictation_www
```

这里有个**两个用户共用目录**的细节：后端以 `dictation` 用户写入音频，
而 Caddy 以 `caddy` 用户读取并对外发送。`755` 让 `caddy` 能读能进入目录，
但只有属主 `dictation` 能写，权限刚好。注意 `/opt/dictation` 这一层也必须
可进入（`x` 位），否则 Caddy 到不了下面的 `v1/`。

数据库文件要能被 `dictation` 用户写入：

```bash
chown dictation:dictation /opt/dictation/v1/dictation.db && chmod 644 /opt/dictation/v1/dictation.db
```

验证 `caddy` 用户确实能读到前端和音频目录：

```bash
sudo -u caddy test -r /opt/dictation/v1/dictation_www/index.html && sudo -u caddy test -x /opt/dictation/v1/audio && echo "caddy 读取权限 OK"
```

<!-- CHUNK4 -->
