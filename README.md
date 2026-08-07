# 听写小助手

> 人教版小学三年级语文听写练习应用。支持生字词听写、间隔复习与打卡记录。

---

## 功能概览

- **每日听写** — 当课新字词优先，错题本自动补足，每次最多 30 词
- **单元复习** — 按单元聚合，错词优先，用于单元测前冲刺
- **间隔重复引擎** — 答对连续 3 次晋级"已掌握"，答错次日必听，复习间隔按 2ⁿ 天指数后延（上限 30 天）
- **田字格实时高亮** — 播放时当前词的拼音与汉字同步显示
- **35 天打卡日历** — 以颜色区分优秀 / 良好 / 需加强
- **题库** — `43 课，815 条知识点（生字 250 / 词语 418 / 易错字 108 / 多音字 39）`

---

## 三个版本

| | V1 | V2 | V3 |
|---|---|---|---|
| **域名** | v1.* | v2.* | v3.* |
| **部署目标** | Ubuntu VPS | Ubuntu VPS | Cloudflare Workers |
| **音频方案** | 运行时调用有道 TTS，ffmpeg 拼接 | 预录切片，浏览器播放列表 | 预录切片，静态资源 CDN |
| **数据库** | SQLite | SQLite | Cloudflare D1 |
| **需要 ffmpeg** | ✅ | ❌ | ❌ |
| **服务器** | uvicorn + Caddy | uvicorn + Caddy | 无（全边缘） |

V1 是最初的完整版本（适合对 TTS 效果有要求时），V2/V3 使用预先录好的切片（启动更快，无等待），三版 API 合约完全一致，可随时切换。

---

## 项目结构

```
dictation-app/
├── shared/
│   ├── data/          题库 JSON（唯一副本）
│   ├── web/           前端母本 index.html + audio/ 切片
│   ├── gen_slices.py  预录切片生成脚本（调用有道 TTS）
│   └── tools/
│       └── export_d1.py  D1 种子 SQL 生成脚本
├── tools/
│   └── stage.py       把 shared/web/ 铺装到 v2/v3
├── v1/                V1 代码（FastAPI + ffmpeg TTS）
├── v2/                V2 代码（FastAPI + 预录切片）
└── v3/                V3 代码（Python Workers + D1）
```

删掉任意两个版本目录，剩下的版本不受影响——`shared/` 永远不删。

---

## 快速开始

### 前置条件

- Python ≥ 3.10
- 有道智云账号（APP_KEY / APP_SECRET）→ [控制台](https://ai.youdao.com/)
- V3 额外需要：Node.js ≥ 18，[uv](https://docs.astral.sh/uv/)，Cloudflare 账号

### 生成音频切片（V2 / V3 必须）

```bash
YOUDAO_APP_KEY=你的Key YOUDAO_APP_SECRET=你的Secret \
  python shared/gen_slices.py
```

约 516 个文件，首次约需数分钟，后续增量更新不重复消耗额度。

### 部署 V1 / V2（Ubuntu VPS + Caddy）

服务器上拉取代码后填密钥、跑一键脚本：

```bash
git clone https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
cp deploy/vps.env.example deploy/vps.env
nano deploy/vps.env          # 填有道密钥、域名、访问口令
sudo bash deploy/vps-install.sh
```

详见 [DEPLOY-vps.md](DEPLOY-vps.md)。

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

---

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/lessons` | 课程目录 |
| `GET` | `/api/generate_daily/{lesson_seq}?mode=daily\|unit` | 生成词表 |
| `POST` | `/api/submit_dictation` | 提交批改，服务端算分并更新记忆库 |
| `GET` | `/api/dictation_history?start_date=&end_date=` | 打卡日历数据 |
| `POST` | `/api/generate_audio`（仅 V1）| 合成听写音频，返回 URL + 时间轴 |

---

## 开发本地运行

```bash
cd v1   # 或 v2
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
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

GNU AGPL v3.0 — 使用本项目的代码（包括部署为网络服务）须开放全部源码，且不得用于闭源商业产品。
