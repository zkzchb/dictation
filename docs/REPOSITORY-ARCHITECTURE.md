# 双仓库架构

Dictation 把相对稳定的软件能力与持续增长的教学内容分开维护。

## 程序仓库：dictation

程序仓库发布 V2 和 V3。V2 使用 FastAPI 与 SQLite，适合 Ubuntu 本地或 VPS；V3 使用
Cloudflare Workers 与 D1。两者共享前端、选词逻辑、内容包校验和数据语义。

程序只依赖 content-pack v1 契约，不依赖具体年级、教材目录、课程编号形状或固定词数。
程序版本采用 `v2.1.x`；schema 兼容范围写入发布说明。程序冻结表示功能与契约进入低频
维护，不表示内容停止增长。

## 内容仓库：dictation-content

内容仓库可以容纳多个教材包，建议结构：

```text
dictation-content/
├── README.md
├── LICENSE
├── NOTICE.md
└── packs/
    └── zh-cn/
        └── primary-3a/
            ├── dataset.json
            ├── lessons.json
            ├── knowledge_points.json
            ├── studio_manifest.json
            ├── tts.sha256
            └── tts/
```

内容仓库独立使用标签发布，例如 `content-v1.0.0`。同一 pack 的 `id` 保持稳定，每个
知识点使用发布后不可复用的稳定整数 ID；修改任何结构化数据或音频后必须更新 counts、
SHA-256 与 dataset digest。兼容更新可以追加 ID 或修订同一语义身份，删除/重新编号需要
新的 pack id 与独立学习数据库。新教材包不要求程序发版。

## 组合与锁定

三个部署目标都通过 `CONTENT_ROOT` 选择 pack。部署记录至少保存：

- 程序 Git commit 或 release tag；
- 内容仓库 Git commit 或 release tag；
- pack id、schema version 和 dataset SHA-256；
- 部署时间与目标类型，不保存凭据。

本地开发推荐把两个仓库并列检出：

```text
Projects/
├── dictation/
└── dictation-content/
```

默认内容路径为 `../dictation-content/packs/zh-cn/primary-3a`，也可以通过绝对路径覆盖。
Cloudflare 部署从同一个 pack 生成 D1 upsert 并上传静态音频；V2 从该 pack 初始化或同步
SQLite，再把音频复制到可写运行目录。VPS 状态默认在 `/var/lib/dictation`，本地状态默认
在 `.runtime/local`。用户历史和录音工作台的未发布录音不回写程序仓库。

## 兼容性规则

程序声明支持的 content-pack schema 主版本。内容包的 schema 主版本不变时，程序不得依赖
未声明字段；需要破坏性格式变化时，新建 schema 主版本并在程序中提供迁移或明确拒绝。
切换 pack 应使用新的 D1 数据库；V2 切换 pack 前必须备份 SQLite、真人录音与学习历史。
