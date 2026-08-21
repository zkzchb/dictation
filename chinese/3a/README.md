# 人教版小学语文三年级上册教材包

这是 Dictation V2 的自包含只读发行资源。克隆仓库后，安装器直接从这里建立空白
SQLite，并把标准 TTS 复制到可写运行目录；不需要原始 XLSX、有道账号或旧服务器数据。

## 内容

- `lessons.json`：43 门课程（42 门可选课程和 1 个冷启动池）；
- `knowledge_points.json`：814 条知识点；
- `studio_manifest.json`：869 个唯一录音词条；
- `tts/w/`：869 个标准词条 MP3；
- `tts/sys/`：25 个系统提示音 MP3；
- `tts.sha256`：全部标准音频的逐文件校验值；
- `dataset.json`：教材身份、数量、路径、TTS 来源和总体校验值。

## 不可变边界

这里不能存放真人录音、学习历史或录音/质检台账。运行时文件位于
`shared/web/audio/`，真人录音只覆盖该运行副本。要更新教材包，必须重新生成
`dataset.json` 与 `tts.sha256`，并通过：

```bash
python3 shared/tools/audio_bundle.py verify-dataset --content-root chinese/3a
```

未来教材使用同级目录，例如 `chinese/3b`、`chinese/4a` 或 `english/...`，不通过
长期 Git 分支区分语言。
