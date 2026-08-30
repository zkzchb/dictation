# V2 可复现安装规范

一次 V2 安装由四个独立输入决定：程序 Git 提交、内容 Git 提交、内容包 dataset digest
以及固定 Python 依赖。安装结果写入运行目录的 `deployment.json`。

## 输入边界

| 输入 | 来源 | 标识 |
|---|---|---|
| 程序 | `dictation` | Git commit/tag |
| 课程与录音 | `dictation-content` | Git commit/tag + `dataset.json version` |
| 逻辑数据 | content pack | `pack_id` + `dataset_sha256` |
| Python 依赖 | `v2/requirements.txt` | 固定版本与 `pip check` |

程序仓库不包含正式内容，内容仓库不包含运行时学习记录。VPS 的可写状态固定保存在
`/var/lib/dictation`；本地默认保存在 `.runtime/local`。

## 安装后不变量

- `content_pack.py` 通过结构和哈希校验；
- `audio_bundle.py verify-dataset` 通过全部 MP3 SHA-256 校验；
- SQLite 课程数和知识点数等于所选内容包，而不是某个程序常量；
- 新数据库的学习历史为空；
- 已有数据库只接受同一 pack id 的稳定 ID 追加/修订；
- 运行音频只允许内容包登记的文件，真人录音由台账保护；
- `deployment.json` 同时记录程序与内容来源。

## 验收命令

```bash
CONTENT_ROOT=../dictation-content/packs/zh-cn/primary-3a
STATE_ROOT=.runtime/local

python3 shared/content_pack.py "$CONTENT_ROOT"
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  python3 shared/tools/audio_bundle.py verify-dataset --content-root "$CONTENT_ROOT"
DICTATION_CONTENT_ROOT="$CONTENT_ROOT" \
  python3 shared/tools/audio_bundle.py inventory \
  --audio-dir "$STATE_ROOT/web/audio"
curl -fsS http://127.0.0.1:8889/api/health
python3 -m json.tool "$STATE_ROOT/web/deployment.json"
```

恢复时必须把 SQLite、运行音频和组合版本记录恢复到同一时间点，再检出记录中的两个 Git
提交。只回滚程序或只回滚数据库不能称为可复现恢复。
