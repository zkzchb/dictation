# V2 升级与回滚

## 分支关系

- `main`：唯一功能主线；
- `main-offline`：从冻结的 `main` 提交生成，只增加离线依赖和发行工具；
- 功能修复不得先提交到 `main-offline`。

`main-offline` 根目录的 `OFFLINE-BASE` 记录其对应的应用版本、`main` 提交和离线
修订号。只有该文件与实际父版本一致时，才能发布离线包。

## 升级前备份

```bash
sudo /usr/local/bin/backup-dictation.sh
sudo systemctl status dictation-v2 --no-pager
```

确认 `/var/backups/dictation/v2/` 和 `/var/backups/dictation/audio/` 中都出现当天文件。

## 标准版升级

```bash
cd /opt/dictation
git fetch --tags origin
git switch --detach v2.0.0
sudo bash deploy/install-v2-online.sh
```

安装器是幂等的：保留现有 SQLite、完整运行音频和后来产生的质检状态。

## 回滚程序

```bash
cd /opt/dictation
git switch --detach 上一个已验收标签
sudo systemctl restart dictation-v2
```

## 恢复数据

停止服务后，从同一天的数据库与音频快照一起恢复，避免成绩历史和录音状态时间点
不一致：

```bash
sudo systemctl stop dictation-v2
gunzip -c /var/backups/dictation/v2/dictation_YYYY-MM-DD.db.gz \
  | sudo tee /opt/dictation/v2/dictation.db >/dev/null
cd /opt/dictation/shared/web
sudo tar -xzf /var/backups/dictation/audio/audio_YYYY-MM-DD.tar.gz
sudo chown -R dictation:dictation /opt/dictation/v2/dictation.db \
  /opt/dictation/shared/web/audio
sudo systemctl start dictation-v2
```

