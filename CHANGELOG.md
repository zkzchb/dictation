# Changelog

本文件记录正式发行的用户可见变化。开发中的完整提交记录见 GitHub Pull Request。

## Unreleased

### Changed

- 移除新前端与录音工作台均已弃用的 `poly_intro` 系统提示音。
- 程序仓库收敛为 V2/V3，正式内容迁移到独立 `dictation-content` 仓库。
- V2 本地/VPS 运行状态移出程序检出目录。
- V2/V3 统一使用外部 content-pack v1、稳定知识点 ID 与组合版本记录。
- CI 使用合成 fixture，不依赖正式教材或录音。

### Added

- 兼容的 SQLite 内容追加同步与 D1 stable-ID upsert。
- 程序/内容 Git ref、内容版本、pack id 与 dataset digest 的部署记录。
- 通用 CI、依赖审计、CycloneDX SBOM、校验和与 GitHub artifact attestation。

历史 `v2.0.0` 冻结发行说明见 [docs/V2-FREEZE.md](docs/V2-FREEZE.md)。
