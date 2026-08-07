# 本地部署指南（Ubuntu 笔记本）

三阶段部署的**第一阶段**。本地做两件事：生成音频切片（V2/V3 都要用，且不入 Git），以及在本机试跑确认功能正常。

```
① 本地 Ubuntu  ← 本文档
   生成切片 + 试跑
        │
        ├──② VPS 部署（V1/V2）→ 同步切片过去
        │     见 DEPLOY-vps.md
        │
        └──③ Cloudflare 部署（V3）→ 直接复用本地切片
              见 DEPLOY-cloudflare.md
```

为什么切片要在本地生成：约 500 个 MP3 共几 MB，不适合入 Git（`.gitignore` 已排除）；生成一次即可，VPS 和 Cloudflare 都复用同一批文件。

---

## 总览：四步

| 步骤 | 做什么 |
|---|---|
| 1 | 拉取代码 |
| 2 | 填写 `deploy/local.env` 密钥 |
| 3 | 跑 `deploy/local-install.sh` |
| 4 | 本机试跑验收 |

---

## 0. 前置条件

- Ubuntu（20.04 及以上都可以）
- 有道智云 APP_KEY / APP_SECRET（[控制台](https://ai.youdao.com/)）
- 磁盘约 100 MB 空闲（切片 + venv）

系统包不用手动装 —— 脚本检测到缺 `python3-venv`、`ffmpeg`、`sqlite3`、`rsync` 会用 sudo 自动安装。

---

## 1. 拉取代码

```bash
git clone https://github.com/zkzchb/dictation.git
cd dictation
```

---

## 2. 填写密钥

```bash
cp deploy/local.env.example deploy/local.env
nano deploy/local.env
```

**必填**：

| 变量 | 说明 |
|---|---|
| `YOUDAO_APP_KEY` | 有道 App Key |
| `YOUDAO_APP_SECRET` | 有道 App Secret |

**可选**（都有默认值）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SETUP_V1` / `SETUP_V2` | `yes` | 是否装该版本的本地运行环境 |
| `TTS_INTERVAL` | `1.0` | 生成间隔（秒）。报 411 限流时调大到 `2.0` |
| `YOUDAO_VOICE` | `youxiaoxun` | 音色。**改了要删 `shared/web/audio/` 重新生成全部切片** |
| `WARM_V1_CACHE` | `yes` | 是否预热 V1 缓存（复用切片，零 API 调用） |

保存后收紧权限：

```bash
chmod 600 deploy/local.env
```

---

## 3. 一键安装

```bash
bash deploy/local-install.sh
```

脚本按顺序完成：

1. 校验配置（占位符未替换会直接报错并指出哪一项）
2. 检查并安装系统包（`python3-venv` / `ffmpeg` / `sqlite3` / `rsync`）
3. 为 V1、V2 建 venv 装依赖
4. 初始化本地数据库（**已存在则保留数据**，只补 `poly_ids` 列）
5. **生成音频切片**到 `shared/web/audio/`（增量，中断可重跑）
6. 预热 V1 缓存（从切片复制，几乎不消耗有道额度）
7. 生成 `.local-run.env` 供试跑用

首次运行切片生成约需数分钟（500+ 个文件，默认 1 秒一个）。

### 常用参数

```bash
bash deploy/local-install.sh --skip-slices        # 跳过切片生成
bash deploy/local-install.sh --slices-only        # 只生成切片，不建 venv
bash deploy/local-install.sh --serve v2           # 装完直接启动 V2
bash deploy/local-install.sh --install-service    # 装成开机自启服务（仅本机可访问）
bash deploy/local-install.sh --uninstall-service  # 移除服务（保留数据与切片）
```

### 预期输出结尾

```
============================================================
  本地部署完成
============================================================

  音频切片   516 个  →  shared/web/audio/
  V1 环境    v1/venv  +  v1/dictation.db
  V2 环境    v2/venv  +  v2/dictation.db
```

---

## 4. 本机试跑

**V2**（推荐先试这个：切片直接播放，不用等合成）

```bash
bash deploy/local-install.sh --skip-slices --serve v2
```

浏览器打开 `http://localhost:8889`，选一课点开始听写，应能听到读音、看到田字格高亮。

**V1**（运行时调 TTS 合成）

```bash
bash deploy/local-install.sh --skip-slices --serve v1
```

打开 `http://localhost:8888`。若第 3 步预热过缓存，合成会命中缓存、几乎瞬间完成。

手动启动的等价命令：

```bash
# V2
cd v2 && ./venv/bin/uvicorn main:app --reload --port 8889

# V1（需要密钥环境变量）
source .local-run.env
cd v1 && ./venv/bin/uvicorn main:app --reload --port 8888
```

### 命令行验收

```bash
# 课程目录：应返回 lesson_seq >= 3100 的课程
curl -s http://localhost:8889/api/lessons | head -c 200

# 出题：应返回 30 词 + 2 个多音字
curl -s "http://localhost:8889/api/generate_daily/3111" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['data']),'词, 多音字',len(d['polyphonic_section']),'个')"

# 切片可访问
curl -sI http://localhost:8889/audio/sys/intro.mp3 | head -3
```

### 检查切片完整性

```bash
find shared/web/audio -name '*.mp3' | wc -l      # 正常 500+
ls shared/web/audio/sys/                          # 应有 intro/outro/poly_intro/g1..g20
du -sh shared/web/audio                          # 通常几 MB
```

---

## 5. 常驻运行（可选）

上面的试跑是前台进程，关掉终端就停了。如果希望这台笔记本开机自动运行、随时打开浏览器就能用：

```bash
bash deploy/local-install.sh --install-service
```

装两个 systemd 服务：`dictation-local-v1`（8888）、`dictation-local-v2`（8889），开机自启、异常自动重启。

**只监听 `127.0.0.1`** —— 同局域网的其他设备访问不到。这是有意的：应用没有任何鉴权，暴露到局域网意味着谁都能改听写记录。需要给别的设备用，就走 VPS 那套（Caddy + basic_auth + HTTPS）。

日常运维：

```bash
systemctl status dictation-local-v1 dictation-local-v2   # 状态
journalctl -u dictation-local-v2 -f                      # 实时日志
sudo systemctl restart dictation-local-v2                # 重启
```

代码更新后要重启才生效：

```bash
git pull && sudo systemctl restart dictation-local-v1 dictation-local-v2
```

移除服务（数据库与切片不受影响）：

```bash
bash deploy/local-install.sh --uninstall-service
```

> 服务读取的密钥文件是 `.local-svc.env`（systemd 格式，无 `export` 前缀），与试跑用的 `.local-run.env` 分开生成。两者都是 600 权限、都在 `.gitignore` 里。改了 `deploy/local.env` 里的密钥后，重跑一次 `--install-service` 即可刷新。

---

## 6. 下一步

**部署到 VPS**（V1/V2）—— 见 [DEPLOY-vps.md](DEPLOY-vps.md)。
VPS 部署完成后，回到本机把切片同步过去：

```bash
bash deploy/sync-slices.sh root@你的VPS_IP
```

该脚本会 rsync 增量传输、修正远端属主、重启 `dictation-v2`，并核对两边文件数。支持 `--dry-run` 先看会传什么。

**部署到 Cloudflare**（V3）—— 见 [DEPLOY-cloudflare.md](DEPLOY-cloudflare.md)。
切片已在本地，用 `--skip-slices` 直接复用：

```bash
cp deploy/cloudflare.env.example deploy/cloudflare.env
nano deploy/cloudflare.env
bash deploy/cloudflare-deploy.sh --skip-slices
```

---

## 7. 题库更新后重新生成切片

题库 JSON 新增词条后，增量生成只补新词：

```bash
git pull
bash deploy/local-install.sh --slices-only
```

然后分发到两处：

```bash
bash deploy/sync-slices.sh root@你的VPS_IP          # VPS
bash deploy/cloudflare-deploy.sh --skip-slices      # Cloudflare（会重新 stage 上传）
```

---

## 8. 故障排查

**`YOUDAO_APP_KEY 仍是占位符`**
`deploy/local.env` 没填或没保存。注意要改的是 `local.env` 而不是 `local.env.example`。

**有道 `errorCode 411`（访问频率受限）**
把 `deploy/local.env` 里的 `TTS_INTERVAL` 改成 `2.0` 或更大，然后重跑。已生成的文件会自动跳过：

```bash
bash deploy/local-install.sh --slices-only
```

**切片生成中断**
直接重跑即可，脚本增量执行，已生成的不会重复消耗额度。

**`python3 -m venv` 失败**
缺 `python3-venv`：

```bash
sudo apt update && sudo apt install -y python3-venv
```

**V1 报 `ffmpeg not found` 或 pydub 相关错误**

```bash
sudo apt install -y ffmpeg
```

**V2 打开页面但没声音**
切片没生成完，或浏览器缓存了旧的空响应：

```bash
find shared/web/audio -name '*.mp3' | wc -l    # 为 0 或很少则重新生成
```

**V1 合成很慢 / 频繁 411**
缓存没预热。V1 每次听写要连发 40+ 次请求：

```bash
source .local-run.env
v1/venv/bin/python shared/tools/warm_v1_cache.py --cache-dir "$PWD/v1/tts_cache"
```

> 注意 `--cache-dir` 要与 V1 实际读取的目录一致。`local-install.sh` 已通过
> `.local-run.env` 里的 `AUDIO_CACHE_DIR` 统一到 `v1/tts_cache`。

**多音字每次都是同两个**
数据库缺 `poly_ids` 列（旧库）：

```bash
v2/venv/bin/python shared/tools/migrate_poly_ids.py v2/dictation.db
```

---

## 附录：本地产物一览

以下都在 `.gitignore` 中，不会提交：

| 路径 | 内容 |
|---|---|
| `shared/web/audio/` | 音频切片（要同步到 VPS / Cloudflare） |
| `v1/venv/` `v2/venv/` | Python 虚拟环境 |
| `v1/dictation.db` `v2/dictation.db` | 本地数据库 |
| `v1/tts_cache/` | V1 的 TTS 磁盘缓存 |
| `v1/audio/` | V1 合成的成品音频 |
| `deploy/local.env` | 你填的密钥 |
| `.local-run.env` | 试跑用环境变量（含密钥，`source` 格式） |
| `.local-svc.env` | systemd 服务用环境变量（含密钥，无 `export`） |

彻底清理本地环境（**会删掉本地听写记录**）：

```bash
# 若装过常驻服务，先移除
bash deploy/local-install.sh --uninstall-service

rm -rf v1/venv v2/venv v1/dictation.db v2/dictation.db \
       v1/tts_cache v1/audio .local-run.env .local-svc.env .venv-gen
# 切片建议保留，删了要重新消耗有道额度：
# rm -rf shared/web/audio
```
