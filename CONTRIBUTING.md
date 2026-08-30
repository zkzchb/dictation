# Contributing

感谢你改进 Dictation。这个仓库只接收 V2/V3 程序、content-pack 契约、部署、测试和程序
文档；教材、词表和录音请提交到 `dictation-content`。

## 开始前

小型 bug 可以直接提交 Pull Request。较大的功能、数据模型或 content-pack schema 变更，
请先开 Issue 说明用户场景、兼容影响以及 V2/V3 是否都需要实现，避免双方在不同方向上投入。

不要提交凭据、个人域名/IP、学习记录、录音台账、未授权教材或他人的录音。测试数据应当
是原创、合成或具有清晰再分发许可的最小 fixture。

## 本地验证

```bash
python3 -m venv .testenv
.testenv/bin/pip install -r v2/requirements.txt
DICTATION_CONTENT_ROOT=tests/fixtures/demo-content-pack \
  .testenv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.testenv/bin/python -m compileall -q v2 v3 shared tools tests
python3 tests/check_inline_js.py
for script in deploy/*.sh; do bash -n "$script"; done
```

改变选词规则时必须保持 SQLite 与 D1 的一致性测试；改变内容契约时必须增加校验失败测试和
升级路径；改变安装器时必须覆盖本地、VPS 和 Workers 中受影响的目标。

## Pull Request

- 一个 PR 解决一个清晰问题；
- 说明行为变化、风险、回滚方式和验证结果；
- 用户可见变化更新 README/部署文档；
- 不把生成目录、运行数据库或音频复制进程序仓库；
- 保持提交消息简洁、使用祈使语气。

提交贡献表示你有权按本仓库的 AGPL-3.0 许可提供这些改动。
