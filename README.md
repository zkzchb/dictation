# Dictation

面向中文听写练习的自托管程序。它提供每日听写、单元复习、错词回流、多音字轮换、
学习记录，以及本地/VPS 录音工作台。程序不绑定出版社、年级或固定课程号，所有课程、
词条和音频都从外部 content pack 加载。

## 两个仓库

| 仓库 | 职责 | 版本节奏 |
|---|---|---|
| [`zkzchb/dictation`](https://github.com/zkzchb/dictation) | V2/V3 程序、内容包规范、部署与测试 | 稳定、低频 |
| [`zkzchb/dictation-content`](https://github.com/zkzchb/dictation-content) | 教材 JSON、录音和内容说明 | 独立发布、持续增长 |

程序仓库只带一个 CC0 的小型合成测试 fixture，不发布正式教材或产品录音。部署记录会保存
程序提交、内容提交、内容版本、pack id 和 dataset SHA-256，便于复现一次具体安装。

## 运行版本

| 版本 | 目标 | 数据库 | 静态音频 | 录音工作台 |
|---|---|---|---|---|
| V2 | Ubuntu 本地或 VPS | SQLite | 本地磁盘 | 支持 |
| V3 | Cloudflare Workers | D1 | Workers 静态资源 | 不支持 |

V2 和 V3 使用同一套 content-pack v1 契约与选词规则。V2 的 SQLite 选择器和 V3 的 D1
选择器有固定随机种子一致性测试。

## 五分钟本地启动

把两个仓库检出为相邻目录：

```bash
mkdir dictation-workspace && cd dictation-workspace
git clone https://github.com/zkzchb/dictation-content.git
git clone https://github.com/zkzchb/dictation.git
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

## 许可

程序采用 [GNU AGPL-3.0](LICENSE)。内容包是独立作品，必须在自己的仓库声明来源、作者、
许可和音频权利；程序许可证不会自动改变内容包的许可。详见 [NOTICE](NOTICE.md) 与
[内容许可边界](docs/CONTENT-LICENSING.md)。
