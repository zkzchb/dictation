# CLAUDE.md — 听写小助手

## 项目简介

为 Mia（小学生，人教版三年级）定制的中文听写练习应用。
三个版本并行，共用题库和音频，部署目标不同。

---

## 目录结构

```
dictation/
├── shared/               ← 永不删除；删任意版本目录不影响其他版本
│   ├── data/             题库 JSON（由 xlsx 转换而来，唯一副本）
│   ├── web/              前端母本 index.html + 预录音频切片 audio/
│   └── tools/            gen_slices.py, convert_wordlist.py,
│                         import_wordlist_xlsx.py, export_d1.py
├── tools/stage.py        铺装 shared/web → v2/audio + v3/public
├── v1/                   Ubuntu VPS, port 8888, 运行时 TTS（有道）
├── v2/                   Ubuntu VPS, port 8889, 预录切片 + 录音工作台
├── v3/                   Cloudflare Workers + D1
├── wordlist_template.xlsx 词表（6 张关系型表格，格式已冻结）
├── DEPLOY-vps.md         V1+V2 部署指南
└── DEPLOY-cloudflare.md  V3 部署指南
```

---

## 三版本对比

| | V1 | V2 | V3 |
|---|---|---|---|
| 部署平台 | Ubuntu VPS | Ubuntu VPS | Cloudflare Workers |
| 端口 | 8888 | 8889 | — |
| 音频方案 | 运行时调有道 TTS | 预录切片（static） | 预录切片（CDN） |
| 数据库 | SQLite | SQLite | D1 |
| 录音工作台 | ❌ | ✅ `/studio` | ❌ |
| 需要 ffmpeg | ✅（TTS 合成） | 可选（studio 切割用） | ❌ |

---

## 词表（xlsx）格式 — **已冻结，不要重新讨论**

6 张工作表，关系型设计：

| 表名 | 主键 | 说明 |
|---|---|---|
| unit | uid (311~318) | 单元 |
| lesson | lid (3111~) | 课程；lid=3111 = 三年级1学期1单元1课 |
| word2write | wid (后两位 01-49) | 生字，每字最多2个组词 |
| vocab | vid (后两位 51-79) | 词语/成语/四字积累 |
| polyphonic | ppid (后两位 81-99) | 多音字，一行一读音 |
| typo | tid (lesson_seq=3000) | 冷启动易错字 |

**lid // 100 = lesson_seq**，**lid // 10 = unit_id**。
拼音由 Claude 用 pypinyin 自动补全，用户审核后确认。

---

## 出题算法（daily 模式）— **已定，不要调整**

总上限 30 词，五梯队漏斗：

1. 昨天的错字（`user_memory.last_tested_date = yesterday`）— **全部保证**
2. 当前课生字（**随机取** word1 或 word2）
3. 当前课词语/成语/易错字
4. 最近其他到期错词（`next_review_date ≤ today`，非昨天）
5. lesson3000 冷启动兜底（前3课词库薄时）

**多音字**：末尾独立段落（`polyphonic_section`），**不占 30 词槽位**。
听写时报字名，学生自行写出各读音的组词并标注拼音。

冷启动注入：`next_review_date = today + 3`（不与真实错词同日竞争）。

---

## Git 规范

```
update@YYYYMMDD

- 具体改动1
- 具体改动2
```

标题固定为 `update@日期`，具体改动写在 description。

---

## 本地环境

- **工作目录**：`D:\claude\dictation`（GitHub: NigthRain/dictation）
- **Python**：`/c/Users/zhang/.local/bin/python3.12`
- **构建 venv**：`D:\claude\dictation\_build_venv`（含 openpyxl, pypinyin）
- **真实数据库**：`v1/dictation.db`（gitignore，含 Mia 的真实学习记录，操作前备份）

---

## 待完成工作（当前状态）

1. **用户**：等教材到手，填完 `wordlist_template.xlsx`（汉字部分，拼音留空）
2. **Claude**：读取 xlsx，用 pypinyin 补全拼音，输出对照表供审核
3. **用户**：审核拼音确认
4. **部署**：按 `DEPLOY-vps.md` 部署 V1 → V2（含录音）→ V3（按 `DEPLOY-cloudflare.md`）

V3 数据来源：
- 音频：`tools/stage.py v3` → `wrangler deploy`
- 题库：`shared/tools/export_d1.py` → `wrangler d1 migrations apply`
- 学习历史：**不迁移**，V3 从空库独立开始

---

## 用户偏好（给 Claude 看）

- **先讨论对齐，再动手**：重要决定（架构、格式、算法）必须先确认，不要直接实现
- **中文回复**
- **简洁直接**：不要冗长的致谢、铺垫或免责声明
- **明确分工**：用户填内容，Claude 填技术细节（拼音、代码）
- **已定的事不再讨论**：词表格式、出题算法、多音字处理方式等均已冻结
- **不修改真实数据库**：任何测试都用 `_smoke.db` 副本，不动 `v1/dictation.db`
