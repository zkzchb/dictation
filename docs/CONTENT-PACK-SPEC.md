# Dictation content-pack v1

content pack 是 v2.1 的可移植内容边界。程序只读取包内的结构化课程、知识点、录音清单
和可选音频，不假设出版社、年级、学期或仓库目录。每个包必须独立说明内容来源与许可。

## 目录

```text
content-pack/
├── dataset.json
├── lessons.json
├── knowledge_points.json
├── studio_manifest.json
├── tts/                    可选的 MP3 基线
└── tts.sha256              包含 tts/ 时必需
```

`dataset.json` 是唯一入口。`paths` 中的值只能是包内相对路径，不能使用绝对路径或 `..`。

## dataset.json

```json
{
  "schema_version": 1,
  "id": "demo-zh-cn",
  "display_name": "中文听写演示包",
  "language": "zh-CN",
  "subject": "chinese",
  "paths": {
    "lessons": "lessons.json",
    "knowledge_points": "knowledge_points.json",
    "studio_manifest": "studio_manifest.json",
    "tts": "tts",
    "tts_checksums": "tts.sha256"
  },
  "runtime": {
    "cold_start_lesson": 9000,
    "initial_lesson": 9101,
    "review_lessons": [9100],
    "daily_target": 30,
    "review_target": 50,
    "polyphonic_per_lesson": 2
  },
  "counts": {},
  "sha256": {}
}
```

`id` 使用 2–64 位小写字母、数字、点、短横线或下划线。`runtime` 的课程号必须出现在
`lessons.json`；`initial_lesson` 必须是正式课，不能同时属于冷启动池或复习课。

为兼容 v2.0，缺少 `runtime` 时按以下规则推断：若存在课程 `3000`，将其视为冷启动池；
除冷启动池外，末位为 `0` 的课程视为复习课；最小的正式课作为首课；目标词数使用
30/50，多音字使用 2 个。公共 v2.1 内容包应显式写出全部 runtime 字段。

## lessons.json

顶层为数组。每项至少包含唯一的正整数 `lesson_seq`、正整数 `unit_id`、非空
`unit_name` 和 `lesson_name`；`lesson_title` 可选。`lesson_seq` 只要求稳定且唯一，不需要
使用“末位 0 表示复习课”之类的编码；课程类型由 `runtime.review_lessons` 明确给出。
同一单元的正式课与复习课必须使用相同 `unit_id`，选词引擎据此确定复习范围。

## knowledge_points.json

顶层为数组。每项包含 `lesson_seq`、非空 `target`、`category` 和 `options_json`。
`lesson_seq` 必须引用已有课程；v1 支持 `生字`、`词语`、`易错字`、`多音字` 四类。
`options_json` 是数组，选项可包含 `text`、`pinyin`、`pron`、`pair_id` 等字段。

## studio_manifest.json

顶层为去重后的录音词条数组。每项包含 `text`、`pinyin` 和
`md5(text.encode("utf-8"))[:12]` 形式的 `hash`。12 位哈希只作为稳定文件名，不用于
安全校验；发行完整性由 `sha256` 和 `tts.sha256` 保证。

## 数量与哈希

`counts` 必须准确记录课程、知识点、录音词条、TTS 词条、系统提示音与分类数量。
`sha256` 至少记录三个 JSON 文件和逻辑 dataset digest；digest 按
`文件名 + NUL + 文件原始字节 + NUL` 的顺序连接三个文件后计算。包含音频时还要记录
`tts.sha256` 自身的 SHA-256，并逐文件校验 MP3。

运行 `python shared/content_pack.py <内容包目录>` 可独立校验一个内容包；运行
`python -m unittest tests/test_content_pack.py -v` 可验证规范实现和 v2.0 兼容性。

Cloudflare/D1 部署使用同一个入口。`shared/tools/export_d1.py` 从通过校验的内容包生成
静态种子迁移和单例 `content_runtime` 配置；Worker 每次请求从 D1 读取配置，不在源码中
写死首课、复习课或目标词数。切换到不同内容包时应使用新的 D1 数据库，避免把两个包的
静态知识点混入同一学习记录。
