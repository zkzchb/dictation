# Ubuntu VPS 部署 V2

VPS 使用 Caddy 提供 HTTPS 和 Basic Auth，FastAPI 只监听回环地址。程序、内容和可写状态
使用三个独立目录：

```text
/opt/dictation/             程序仓库
/opt/dictation-content/     内容仓库
/var/lib/dictation/         SQLite、运行音频、录音台账和部署记录
```

## 1. 检出程序与内容

```bash
apt update && apt install -y git
git clone --branch v2.1.0-rc.1 https://github.com/zkzchb/dictation.git /opt/dictation
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git /opt/dictation-content
cd /opt/dictation
```

## 2. 交互式安装

```bash
bash deploy/install-v2-online.sh
```

入口会询问公网 IPv4、主域名、备用域名、是否立即启用 HTTPS、访问用户名/密码、录音
工作台开关以及外部内容包绝对路径。默认内容路径是
`/opt/dictation-content/packs/zh-cn/primary-3a`。

备案或 DNS 尚未完成时，可以只启用 `http://公网IP`；条件具备后重跑同一入口，把域名加入
Caddy。程序仓库不保存实际域名、IP 或口令。

## 3. 自动化入口

非交互部署可直接准备低级配置：

```bash
cp deploy/vps.env.example deploy/vps.env
chmod 600 deploy/vps.env
nano deploy/vps.env
sudo bash deploy/vps-install.sh
```

`APP_ROOT`、`CONTENT_ROOT`、`STATE_ROOT` 必须是绝对路径。安装器幂等，可用于首次安装、
程序升级和兼容的内容追加。

## 4. 安装器行为

安装过程按顺序完成：

1. 安装 Python、SQLite、ffmpeg、Caddy、UFW 等系统依赖；
2. 验证外部内容包、逐文件音频哈希与程序语法；
3. 安装固定 Python 依赖；
4. 从旧仓库内路径复制已有数据库/录音（仅在新状态目录为空时）；
5. 初始化或按稳定 ID 同步 SQLite 内容，同时保留学习记录；
6. 在 `/var/lib/dictation/web` 铺装前端和音频；
7. 安装 `dictation-v2.service`、Caddy 鉴权、防火墙和每日备份；
8. 验证 API、数据库和音频清单。

## 5. 验收与运维

```bash
systemctl status dictation-v2 --no-pager
journalctl -u dictation-v2 -n 50 --no-pager
curl -fsS http://127.0.0.1:8889/api/health
DICTATION_CONTENT_ROOT=/opt/dictation-content/packs/zh-cn/primary-3a \
  python3 /opt/dictation/shared/tools/audio_bundle.py inventory \
  --audio-dir /var/lib/dictation/web/audio
sudo /usr/local/bin/backup-dictation.sh
```

组合版本记录位于 `/var/lib/dictation/deployment.json`，包括程序提交、内容提交、内容版本、
pack id 与 dataset SHA-256。

## 6. 更新

```bash
git -C /opt/dictation fetch --tags
git -C /opt/dictation switch --detach v2.1.0-rc.1
git -C /opt/dictation-content fetch --tags
git -C /opt/dictation-content switch --detach content-v1.0.0
cd /opt/dictation
bash deploy/install-v2-online.sh
```

同一 pack id 的兼容更新可以追加课程和知识点；程序无需改动。若更新试图删除稳定 ID、
切换 pack id 或孤立已有真人录音，安装会在写入前停止。不同 pack 使用独立 SQLite 数据库。

## 7. 备份与恢复

每日 03:00 自动备份：

- `/var/backups/dictation/v2/*.db.gz`：SQLite 一致性备份；
- `/var/backups/dictation/audio/*.tar.gz`：运行音频、录音台账与组合版本记录。

内容仓库本身由 Git 版本化，不应以运行目录备份代替。恢复时先恢复数据库和 audio 目录，
再检出 `deployment.json` 记录的程序/内容提交并重跑安装器。

## 8. 卸载服务

```bash
sudo bash deploy/vps-uninstall.sh
```

卸载器移除 systemd、Caddy 配置、运行环境文件、cron 和系统用户；程序、内容仓库、
`/var/lib/dictation` 与 `/var/backups/dictation` 都保留。

## 9. 从本地工作站执行全新重装与验收

需要删除 VPS 上的旧 Dictation 部署并验证候选版时，在可信的本地 Ubuntu 工作站运行：

```bash
cp deploy/vps.env.example /path/to/private/bce-vps.env
chmod 600 /path/to/private/bce-vps.env
# 编辑私有配置后先执行本地校验和远端只读预检
bash deploy/vps-fresh-redeploy.sh \
  --validate-env-only --env-file /path/to/private/bce-vps.env
bash deploy/vps-fresh-redeploy.sh \
  --host <BCE_IP_OR_SSH_ALIAS> \
  --user root \
  --env-file /path/to/private/bce-vps.env \
  --preflight-only

# 确认预检结果后执行完整流程；脚本会再次要求输入目标主机确认
bash deploy/vps-fresh-redeploy.sh \
  --host <BCE_IP_OR_SSH_ALIAS> \
  --user root \
  --env-file /path/to/private/bce-vps.env
```

脚本固定部署 `v2.1.0-rc.1` 与 `content-v1.0.0`。它只删除文档列出的 Dictation
路径；删除前会校验 SQLite、空间、Caddy 边界、快照归档和 SHA-256。安装或验收失败时，
脚本自动恢复删除前快照。若现有 Caddyfile 不能识别为 Dictation 专用配置，流程默认停止；
只有确认整台 VPS 专用于 Dictation 时才能显式加入 `--allow-caddy-replace`。

原始日志分别保存在本地私有状态目录和 VPS 的 `/var/backups/dictation/acceptance/`；脚本
另生成不含主机、域名、账号、路径、学习记录或凭据的 `ubuntu-vps-bce.md`，只有该脱敏
报告适合提交到 `docs/verification/v2.1.0-rc.1/` 或链接到 Pull Request。
