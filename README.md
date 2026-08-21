# 听写小助手

小学语文听写练习应用，当前词库为人教版三年级上学期。支持每日听写、单元复习、
间隔重复、打卡记录、批量录音、集中质检和批量重录。

## V2.0.0 冻结版

V2 已冻结为稳定功能基线：43 门课程、814 个知识点、869 个词条音频和 25 个系统
提示音。冻结后只接受数据安全、严重兼容性和安全漏洞修复。

| 分支 | 用途 | Python 依赖 |
|---|---|---|
| `main` | 唯一功能主线、标准联网部署 | 按固定版本从 PyPI/配置的镜像安装 |
| `main-offline` | `main` 的机械离线发行分支 | 仓库 wheelhouse，安装时不访问 PyPI |

教材 JSON 和标准音频是产品内容，两个分支都包含。wheelhouse 只存在于
`main-offline`；成品压缩包只发布到 GitHub Release。

## 快速部署 V2

### 标准联网版

```bash
apt update && apt install -y git
git clone --branch main https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
bash deploy/install-v2-online.sh
```

详细说明：[V2 标准联网部署](docs/V2-DEPLOY-ONLINE.md)。

### 离线版

在可以访问 GitHub 的电脑下载离线 Release 附件，通过 Xftp/SCP 上传到服务器，或
检出 `main-offline`。然后按照该分支中的 `docs/V2-DEPLOY-OFFLINE.md` 操作。

两种入口都会在运行开始时询问公网 IP、主域名、备用域名、是否立即启用 HTTPS、
访问账号、密码和录音工作台设置。仓库不保存任何个人域名、IP 或口令。

备案期间可只填写公网 IP并暂不启用 HTTPS；备案完成后重新运行同一入口即可加入
域名并让 Caddy 自动申请证书。

## 三个运行版本

| | V1 | V2 | V3 |
|---|---|---|---|
| 部署目标 | Ubuntu VPS | Ubuntu VPS | Cloudflare Workers |
| 音频方案 | 运行时调用有道 TTS | 预录切片 | 预录切片/CDN |
| 数据库 | SQLite | SQLite | Cloudflare D1 |
| 状态 | 保留 | 冻结主线 | 实验/边缘版本 |

## 项目结构

```text
dictation/
├── chinese/3a/       教材 JSON、Studio 清单和纯净标准 TTS
├── shared/           公共前端、建库、音频与迁移工具
├── deploy/           本地、VPS、Cloudflare 和同步脚本
├── docs/             冻结、安装、升级与回滚文档
├── v1/               运行时 TTS 版本
├── v2/               冻结的 FastAPI/SQLite 版本
└── v3/               Cloudflare Workers 版本
```

## V2 验收

```bash
systemctl status dictation-v2 --no-pager
curl -fsS http://127.0.0.1:8889/api/health
python3 shared/tools/audio_bundle.py inventory --audio-dir shared/web/audio
```

## 进一步文档

- [V2 冻结说明](docs/V2-FREEZE.md)
- [V2 可复现安装规范](docs/V2-REPRODUCIBLE-INSTALL.md)
- [V2 升级与回滚](docs/V2-UPGRADE-AND-ROLLBACK.md)
- [本地部署](DEPLOY-local.md)
- [Cloudflare 部署](DEPLOY-cloudflare.md)

## 许可

GNU AGPL v3.0。允许商业使用；通过网络向用户提供修改版程序时，需要按许可证要求
向这些用户提供对应源码。闭源发行或不同授权方式需要版权所有者另行许可。

