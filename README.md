# Dictation

[English](README.en.md) | 简体中文

当前稳定程序版本为 [`v2.1.0`](https://github.com/zkzchb/dictation/releases/tag/v2.1.0)，兼容内容版本为 [`content-v1.0.0`](https://github.com/zkzchb/dictation-content/releases/tag/content-v1.0.0)。

面向中文听写练习的自托管程序。它提供每日听写、单元复习、错词回流、多音字轮换、
学习记录，以及本地/VPS 录音工作台。程序不绑定出版社、年级或固定课程号，所有课程、
词条和音频都从外部 content pack 加载。

## 项目理念

Dictation 不以建设固定教材、固定语音的中心化 SaaS 为目标，而是一套运行可版本化学习材料的自托管学习运行时。核心原则是：**程序无状态，材料有版本，记录有归属，反馈可解释。**

详见 [项目愿景（中文）](docs/PROJECT-VISION.md) / [Project vision (English)](docs/PROJECT-VISION.en.md)。

## 两个仓库

| 仓库 | 职责 | 版本节奏 |
|---|---|---|
| [`zkzchb/dictation`](https://github.com/zkzchb/dictation) | V2/V3 程序、内容包规范、部署与测试 | 稳定、低频 |
| [`zkzchb/dictation-content`](https://github.com/zkzchb/dictation-content) | 教材 JSON、录音和内容说明 | 独立发布、持续增长 |

程序仓库只带一个 CC0 的小型合成测试 fixture，不发布正式教材或产品录音。部署记录会保存
程序提交、内容提交、内容版本、pack id 和 dataset SHA-256，便于复现一次具体安装。

## 运行时与产品形态

V2/V3 与三种 Edition 描述的是两个不同维度。**V2/V3 是技术运行时和部署目标；Edition 是面向使用者的产品形态，不是新的代码版本或分支。** 一个 Edition 可以由不同运行时承载，同一个 V2 部署也可以同时提供个人练习和材料工作台。

### 技术运行时

| 运行时 | 目标 | 数据库 | 静态音频 | 录音工作台 |
|---|---|---|---|---|
| V2 | Ubuntu 本地或 VPS | SQLite | 本地磁盘 | 支持 |
| V3 | Cloudflare Workers | D1 | Workers 静态资源 | 不支持 |

V2 和 V3 使用同一套 content-pack v1 契约与选词规则。V2 的 SQLite 选择器和 V3 的 D1 选择器有固定随机种子一致性测试，因此同一个学习包可以铺装到两种运行时。

### 产品形态

| 产品形态 | 当前状态 | 与运行时、部署的关系 | 当前范围与边界 |
|---|---|---|---|
| 个人练习版（Personal Edition） | 可用 | V3 + Workers/D1 是低运维参考方案；V2 本地/VPS 也可承载，并额外提供 Studio | 面向个人或家庭的听写、复习与学习记录。V3 不含 Studio；当前不是具备完整认证体系的多用户服务，访问保护由部署者负责 |
| 教师工作台版（Teacher Studio Edition） | 核心工作流可用 | 当前使用 V2 本地/VPS；它是 V2 学习运行时与 Studio 工作流的组合 | 支持录音、重录和人工质检，生成或维护的学习包可供 V2/V3 使用；更完整的导入、编辑和发布体验仍在路线图中 |
| 班级共学版（Classroom Edition） | 规划中 | 预期面向自托管 VPS 或校内服务器，具体运行时尚未确定 | 班级、学生、任务、多教材、进度与教学反馈均属后续规划；v2.1.0 不包含完整多用户认证或班级协作 |

简言之：**V2/V3 回答“程序怎样运行”，三种 Edition 回答“谁来使用、解决什么问题”。二者不是一一对应关系。**

## 五分钟本地启动

正式版部署固定使用程序与内容标签，避免两个 `main` 在安装期间发生漂移。把两个仓库检出为
相邻目录：

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git
git clone --branch v2.1.0 https://github.com/zkzchb/dictation.git
cd dictation
cp deploy/local.env.example deploy/local.env
bash deploy/local-install.sh --serve
```

默认内容包路径是
`../dictation-content/packs/zh-cn/primary-3a`，运行状态写入
`.runtime/local`。浏览器打开 `http://localhost:8889`。

若使用另一个 pack，只需修改 `deploy/local.env` 中的 `CONTENT_ROOT`。安装器会先验证结构、
哈希和音频清单，再建立或同步数据库；不兼容的删除、重新编号或 pack 切换会被拒绝。

## 部署

- [Ubuntu 本地](DEPLOY-local.md)
- [Ubuntu VPS](DEPLOY-vps.md)
- [Cloudflare Workers + D1](DEPLOY-cloudflare.md)

VPS 推荐布局：

```text
/opt/
├── dictation/                  程序，只读检出
└── dictation-content/          内容，只读检出

/var/lib/dictation/             SQLite、运行音频、录音台账和部署记录
```

这样程序更新、内容更新和用户状态互不覆盖。旧部署升级时，安装器会把仓库内的旧数据库与
运行音频复制到独立状态目录，原文件保留作为回退副本。

## 内容包兼容性

`dataset.json` 是内容包唯一入口。每个知识点都有发布后不可复用的稳定整数 ID；兼容更新
可以追加课程/知识点，或在保留语义身份时修订原 ID。删除、重新编号以及切换 pack id
需要独立数据库。

```bash
python3 shared/content_pack.py ../dictation-content/packs/zh-cn/primary-3a
python3 shared/sync_content.py \
  --db .runtime/local/v2/dictation.db \
  --content-root ../dictation-content/packs/zh-cn/primary-3a
```

完整契约见 [content-pack v1 规范](docs/CONTENT-PACK-SPEC.md) 和
[双仓库架构](docs/REPOSITORY-ARCHITECTURE.md)。

## 开发与验证

```bash
python3 -m venv .testenv
.testenv/bin/pip install -r v2/requirements.txt
DICTATION_CONTENT_ROOT=tests/fixtures/demo-content-pack \
  .testenv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall -q v2 v3 shared tools tests
for script in deploy/*.sh; do bash -n "$script"; done
python3 tests/check_inline_js.py
```

测试 fixture 没有音频；音频打包、校验和铺装测试会在临时目录中生成最小的合成 MP3
文件，不依赖正式内容仓库。

## 项目结构

```text
dictation/
├── v2/                 FastAPI / SQLite API
├── v3/                 Cloudflare Python Worker / D1
├── shared/             选词、建库、内容同步、内容包与音频工具
├── shared/web/         V2/V3 公共前端母本
├── deploy/             本地、VPS 与 Cloudflare 部署入口
├── tools/              V2/V3 静态资源铺装
├── tests/              回归测试与合成 content-pack fixture
└── docs/               规范、架构、许可与发布文档
```

历史冻结版仍可通过 `v2.0.0` 标签和 `v2.0-stable` 分支检出；当前产品线只维护 V2/V3。

v2.1 验收记录保存在 [docs/verification](docs/verification/) 与对应发布 PR 中；原始机器日志留在对应部署环境，
公开仓库只保存去除账号、地址、凭据和用户数据后的结论。

## 许可

程序采用 [GNU AGPL-3.0](LICENSE)。内容包是独立作品，必须在自己的仓库声明来源、作者、
许可和音频权利；程序许可证不会自动改变内容包的许可。详见 [NOTICE](NOTICE.md) 与
[内容许可边界](docs/CONTENT-LICENSING.md)。
