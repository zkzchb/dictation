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
git clone https://github.com/zkzchb/dictation.git /opt/dictation
git clone https://github.com/zkzchb/dictation-content.git /opt/dictation-content
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
git -C /opt/dictation pull --ff-only
git -C /opt/dictation-content pull --ff-only
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
