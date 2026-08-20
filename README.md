# 听写小助手

> 小学语文听写练习应用（现词库支持人教版三年级上学期）。支持生字词听写、间隔复习与打卡记录。

---

## 功能概览

- **每日听写** — 当课新字词优先，错题本自动补足，每次最多 30 词
- **单元复习** — 按单元聚合，错词优先，用于单元测前冲刺
- **间隔重复引擎** — 答对连续 3 次晋级"已掌握"，答错次日必听，复习间隔按 2ⁿ 天指数后延（上限 30 天）
- **田字格实时高亮** — 播放时当前词的拼音与汉字同步显示
- **35 天打卡日历** — 以颜色区分优秀 / 良好 / 需加强
- **题库** — `42 门可选课程 + 1 个冷启动池，814 条知识点（生字 250 / 词语 418 / 易错字 108 / 多音字 38）`

---

## 三个版本

| | V1 | V2 | V3 |
|---|---|---|---|
| **域名** | v1.* | v2.* | v3.* |
| **部署目标** | Ubuntu VPS | Ubuntu VPS | Cloudflare Workers |
| **音频方案** | 运行时调用有道 TTS，ffmpeg 拼接 | 预录切片，浏览器播放列表 | 预录切片，静态资源 CDN |
| **数据库** | SQLite | SQLite | Cloudflare D1 |
| **需要 ffmpeg** | ✅ | 播放不需要；录音台需要 | ❌ |
| **服务器** | uvicorn + Caddy | uvicorn + Caddy | 无（全边缘） |

V1 是最初的完整版本（适合对 TTS 效果有要求时），V2/V3 使用预先录好的切片（启动更快，无等待），三版 API 合约完全一致，可随时切换。

---

## 项目结构

```
dictation-app/
├── chinese/3a/       自包含教材包（JSON + 纯净标准 TTS）
├── shared/
│   ├── data/          V1/V3 兼容数据副本（CI 强制与教材包一致）
│   ├── web/           前端母本（audio/ 为部署时安装的发行资源）
│   ├── gen_slices.py  预录切片生成脚本（调用有道 TTS）
│   └── tools/
│       ├── audio_bundle.py  V2 音频发行包校验/导入工具
│       └── export_d1.py     D1 种子 SQL 生成脚本
├── tools/
│   └── stage.py       把 shared/web/ 铺装到 v2/v3
├── v1/                V1 代码（FastAPI + ffmpeg TTS）
├── v2/                V2 代码（FastAPI + 预录切片 + 离线 wheelhouse）
└── v3/                V3 代码（Python Workers + D1）
```

删掉任意两个版本目录，剩下的版本不受影响；`shared/` 永远不删。

---

## 快速开始

### 前置条件

- Python ≥ 3.10；冻结版 V2 离线安装基线固定为 Ubuntu 24.04 / Python 3.12
- 仅 V1 运行时合成或重新生成 TTS 时需要有道智云账号；标准 V2/V3 不需要
- V3 额外需要：Node.js ≥ 18，[uv](https://docs.astral.sh/uv/)，Cloudflare 账号

### 本地部署 / 生成音频切片

```bash
cp deploy/local.env.example deploy/local.env
nano deploy/local.env          # 标准 V2 可直接保留有道占位符
bash deploy/local-install.sh
```

完整基线为 869 个词条音频和 25 个系统提示音。正式 V2 安装直接使用
`chinese/3a/tts` 中随仓库版本化的标准音频，不需要再次调用 TTS。V2 的 Python
运行依赖也随仓库冻结在 `v2/wheelhouse`，安装时不访问 PyPI 或地区镜像。

详见 [DEPLOY-local.md](DEPLOY-local.md)。

### 部署 V1 / V2（Ubuntu VPS + Caddy）

服务器上拉取代码后填密钥、跑一键脚本：

```bash
git clone https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
cp deploy/vps.env.example deploy/vps.env
nano deploy/vps.env          # 填域名、访问口令和标准音频来源
sudo bash deploy/vps-install.sh
```

全新 V2 的数据库、TTS 基线和真人录音分层流程见
[V2 可复现安装规范](docs/V2-REPRODUCIBLE-INSTALL.md)，完整运维见
[DEPLOY-vps.md](DEPLOY-vps.md)。

### 部署 V3（Cloudflare Workers）

本地执行，无需服务器：

```bash
cp deploy/cloudflare.env.example deploy/cloudflare.env
nano deploy/cloudflare.env   # 填有道密钥
bash deploy/cloudflare-deploy.sh
```

详见 [DEPLOY-cloudflare.md](DEPLOY-cloudflare.md)。

---

## 环境变量

复制 `.env.example` 为 `.env` 并填写：

| 变量 | 用途 | 版本 |
|---|---|---|
| `YOUDAO_APP_KEY` | 有道 TTS App Key | V1 运行时 / 切片生成 |
| `YOUDAO_APP_SECRET` | 有道 TTS App Secret | 同上 |
| `YOUDAO_VOICE` | TTS 音色（默认 `youxiaoxun`） | V1 |
| `YOUDAO_SPEED` | 语速（默认 `0.6`） | V1 |
| `AUDIO_OUTPUT_DIR` | 成品音频目录 | V1 |
| `AUDIO_CACHE_DIR` | TTS 分词缓存目录 | V1 |
| `DICTATION_DB` | 数据库路径（默认脚本同目录） | V1 / V2 |
| `APP_TIMEZONE` | V2 打卡与录音台账业务时区（默认 `Asia/Shanghai`） | V2 |

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/lessons` | 课程目录 |
| `GET` | `/api/generate_daily/{lesson_seq}?mode=daily\|unit` | 生成词表 |
| `POST` | `/api/submit_dictation` | 提交批改，服务端算分并更新记忆库 |
| `GET` | `/api/dictation_history?start_date=&end_date=` | 打卡日历数据 |
| `GET` | `/api/health`（仅 V2） | 服务与数据库健康检查 |
| `POST` | `/api/generate_audio`（仅 V1）| 合成听写音频，返回 URL + 时间轴 |

---

## 开发本地运行

```bash
cd v1   # 或 v2
python3 -m venv venv && source venv/bin/activate
# V1：pip install -r requirements.txt
# V2（Python 3.12 / Linux）：
python ../shared/tools/verify_wheelhouse.py wheelhouse
pip install --no-index --find-links wheelhouse -r requirements.txt
python ../shared/init_db.py --db dictation.db   # 首次初始化数据库
uvicorn main:app --reload --port 8888
```

访问 `http://localhost:8888`（需同时用 Caddy 或直接打开 `v1/dictation_www/index.html`）。

---

## 技术栈

**后端** FastAPI · Pydantic · SQLite / Cloudflare D1 · uvicorn  

**前端** Alpine.js · Tailwind CSS · FontAwesome  

**TTS** 有道智云 `youxiaoxun`（0.6× 语速）  

**服务** Caddy（自动 HTTPS）· Cloudflare Workers（V3） 

**算法** 自研间隔重复（基于 Leitner Box 变体）

---

## 许可

GNU AGPL v3.0 — 允许商业使用；通过网络向用户提供修改版程序时，需要按许可证要求向这些用户提供对应源码。闭源发行或不同授权方式需要版权所有者另行许可。

V2 冻结、部署验收和回滚步骤见 [V2-FREEZE.md](docs/V2-FREEZE.md)。
