# V2 可复现安装规范

V2 的可复现性由固定程序版本、教材数据、标准音频、依赖版本和验收规则共同保证。

| 发行物 | 标准版 `main` | 离线版 `main-offline` |
|---|---|---|
| 程序、教材 JSON | 包含 | 包含 |
| 894 个标准音频 | 包含 | 包含 |
| `requirements.txt` 固定版本 | 包含 | 包含 |
| Python wheelhouse 与哈希 | 不包含 | 包含 |
| 在线安装入口 | 包含 | 包含 |
| 离线安装入口 | 不包含 | 包含 |

标准音频只读基线位于 `chinese/3a/tts`；部署时复制到可写的
`shared/web/audio`。真人录音只能覆盖运行副本，不能回写教材包。

标准版通过 `deploy/install-v2-online.sh` 安装；离线版通过
`deploy/install-v2-offline.sh` 安装。两个入口都调用同一个公共部署核心，因此数据库、
systemd、Caddy、防火墙、备份和健康检查不会产生两套实现。

## 冷启动结果

全新部署不得复制旧 SQLite。安装后必须满足：

```text
lessons=43
knowledge_points=814
dictation_history=0
word_audio=869
system_audio=25
```

真人录音覆盖包只允许包含录音台账登记过的 MP3 和必要台账，不得包含成绩、Check
结果或待重录词表。导入覆盖包必须固定 SHA-256。

## 验收命令

```bash
curl -fsS http://127.0.0.1:8889/api/health
python3 shared/tools/audio_bundle.py verify-dataset --content-root chinese/3a
python3 shared/tools/audio_bundle.py inventory --audio-dir shared/web/audio
sqlite3 v2/dictation.db \
  'SELECT (SELECT count(*) FROM lessons), (SELECT count(*) FROM knowledge_points), (SELECT count(*) FROM dictation_history);'
```

