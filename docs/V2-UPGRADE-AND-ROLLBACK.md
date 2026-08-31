# V2 升级与回滚

程序、内容和用户状态独立版本化。升级前先运行 `/usr/local/bin/backup-dictation.sh`，并保存
`/var/lib/dictation/deployment.json`。

## 升级

```bash
git -C /opt/dictation fetch --tags origin
git -C /opt/dictation-content fetch --tags origin
git -C /opt/dictation switch --detach <program-tag-or-commit>
git -C /opt/dictation-content switch --detach <content-tag-or-commit>
cd /opt/dictation
bash deploy/install-v2-online.sh
```

安装器会校验两个检出，先同步数据库，再铺装静态文件，最后重启服务。同一 pack id 的追加
更新保留历史；删除稳定 ID、切换 pack id 或孤立已有录音会阻塞升级。

## 回滚程序和内容

优先检出升级前 `deployment.json` 中记录的两个 commit，然后重跑安装器。若升级已经改变
SQLite 内容或录音状态，还应一起恢复同一天的数据库与 audio 备份。

```bash
sudo systemctl stop dictation-v2
sudo sh -c 'gunzip -c /var/backups/dictation/v2/dictation_YYYY-MM-DD.db.gz > /var/lib/dictation/v2/dictation.db'
sudo tar -C /var/lib/dictation/web -xzf \
  /var/backups/dictation/audio/audio_YYYY-MM-DD.tar.gz
sudo chown -R dictation:dictation /var/lib/dictation
sudo systemctl start dictation-v2
curl -fsS http://127.0.0.1:8889/api/health
```

恢复前应另行复制当前状态以便返工；不要把不同日期的 SQLite 与录音台账混合。
