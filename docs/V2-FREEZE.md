# V2 冻结说明

冻结目标：把当前已验证的听写、批量录音、集中质检和批量重录流程固化为
Docker 版本的唯一功能基线。冻结不是停止修复，而是停止向 V2 增加新功能；
冻结后只接受数据安全、严重兼容性和安全漏洞修复。

## 冻结候选修复

- 录音、质检和重录 JSON 台账使用进程内锁、唯一临时文件、原子替换和有效备份；
  状态损坏不再静默重置。
- 浏览器录制的系统提示音由 WebM 真正转码为 MP3。
- 保存词语切片时验证 `hash == md5(text)[:12]`，并限制上传大小、录音时长和批量数量。
- 听写成绩提交由 `submission_id` 保证重试幂等，拒绝客户端更换用户、重复或未知词条。
- SQLite 增加 30 秒忙等待和外键检查；业务日期固定使用 `Asia/Shanghai`。
- 真人录音禁止共享/长期缓存；播放端增加冻结版本参数，重录后立即生效。
- 质检状态必须确认服务端保存成功后才进入下一词；失败时停留在当前词。
- 录音目录（包括真人 MP3 和四份台账）加入每日备份。
- 增加 `/api/health`、冻结依赖版本、自动回归测试和 GitHub CI。
- V2 运行依赖以带 SHA-256 清单的离线 wheelhouse 随源码发行，冻结安装不依赖 pip 镜像。

## 首次部署冻结候选版

这次不仅改了 Python/HTML，还改了 Caddy、systemd 环境和备份脚本。因此不能只重启服务，
需要在服务器已有的 `deploy/vps.env` 配置基础上重跑一次幂等安装器：

```bash
cd /opt/dictation
git pull origin main
sudo bash deploy/vps-install.sh
```

安装器会在覆盖 Caddyfile 前保留带时间戳的备份。完成后检查：

```bash
curl -fsS http://127.0.0.1:8889/api/health
sudo systemctl status dictation-v2 --no-pager
sudo /usr/local/bin/backup-dictation.sh
ls -lh /var/backups/dictation/audio/
```

## 线上验收清单

1. 手机和桌面各完成一次生字听写，确认语音、停顿、暂停/继续、核对和成绩入库。
2. 同一成绩在“入库失败”后重试，历史只新增一条。
3. Studio 录制一组 10 词，切片数匹配时自动保存。
4. Check 连续检查至少 20 词；正常词自动保存，手工标记后可生成重录词表。
5. Studio2 重录 1 词后，Check 立即听到新音频并显示为未检查。
6. 录制 1 条系统提示音，主听写页能够正常播放。
7. 确认当天打卡日期与中国时区一致。
8. 手动运行备份脚本，确认数据库和 `audio_YYYY-MM-DD.tar.gz` 同时存在。

全部通过后，从已部署的精确提交创建 `v2.0.0` 标签和 GitHub Release；Docker 分支只从
该标签创建，禁止从后续漂移的 `main` 直接复制。

冻结前还必须完成一次[全新 VPS 可复现安装](V2-REPRODUCIBLE-INSTALL.md)：从仓库教材
JSON 建立 `43|814|0` 的空白数据库，安装带 SHA-256 的 894 个标准 TTS 音频，再以独立
覆盖包导入真人录音。不能把旧 V2 的 SQLite 或质检历史复制过去充当冷启动验收。

## 回滚

代码回滚到冻结前提交并重启 V2：

```bash
cd /opt/dictation
git switch --detach 7895202851800efc2a65d2f4b83b49a8b4c0b937
sudo systemctl restart dictation-v2
```

录音台账或音频损坏时，先停止 V2，再从指定日期恢复完整音频快照：

```bash
sudo systemctl stop dictation-v2
cd /opt/dictation/shared/web
sudo tar -xzf /var/backups/dictation/audio/audio_YYYY-MM-DD.tar.gz
sudo chown -R dictation:dictation /opt/dictation/shared/web/audio
sudo systemctl start dictation-v2
```

恢复数据库时使用同一天的 `/var/backups/dictation/v2/dictation_YYYY-MM-DD.db.gz`，
避免成绩历史与录音工作流时间点不一致。

## Docker 开发入口

Docker 版本必须保留以下数据卷边界：

- `/data/dictation.db`：SQLite；
- `/data/audio`：真人/TTS 音频；
- `/data/state`：录音、质检和重录台账；

容器镜像内只放只读程序、前端和初始题库。必须有健康检查、非 root 用户、明确时区、
固定 Python/ffmpeg 版本、启动迁移、备份/恢复命令，以及从旧 VPS 数据目录导入的工具。
外部 Tailwind、Alpine 和 Font Awesome CDN 资源需要打包进镜像，不能作为离线运行依赖。
