# V2 离线部署

适用分支：`main-offline`。它与 `main` 使用完全相同的应用源码、教材、标准音频和
公共部署核心，只额外携带 Python wheelhouse、校验工具和离线发行入口。

“离线”表示安装时不访问 GitHub或 PyPI。Ubuntu 系统包仍需要通过 APT 安装。

## 方法一：使用 GitHub Release 离线包

在能够访问 GitHub 的电脑下载：

- `dictation-v2.0.0-offline.1.tar.gz`
- `SHA256SUMS`

通过 Xftp/SCP 上传到服务器 `/root`，然后：

```bash
cd /root
sha256sum -c SHA256SUMS
mkdir -p /opt/dictation
tar -xzf dictation-v2.0.0-offline.1.tar.gz \
  --strip-components=1 -C /opt/dictation
cd /opt/dictation
bash deploy/install-v2-offline.sh
```

如果 `/opt/dictation` 已存在未知文件或中断的 clone，请先将整个目录改名保存，不要
直接覆盖。已有正式部署则先运行备份，并按升级文档操作。

## 方法二：检出离线分支

适合服务器本身能访问 GitHub、但不能稳定访问 PyPI 的环境：

```bash
git clone --branch main-offline \
  https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
bash deploy/install-v2-offline.sh
```

## 交互设置

脚本开始时询问公网 IP、主域名、备用域名、是否立即启用 HTTPS、访问账号、密码和
录音工作台设置。所有个人值只写入服务器上的 `deploy/vps.env`。

备案期间选择暂不启用 HTTPS，Caddy 只配置 `http://IP`；备案完成后重新运行同一
脚本并启用 HTTPS。

## 离线依赖校验

安装器在创建 venv 前执行：

```bash
python3 shared/tools/verify_wheelhouse.py v2/wheelhouse
```

它会拒绝：缺失或多余 wheel、SHA-256 不一致、不支持的 CPU 架构以及与当前 Python
版本不匹配的发行包。随后 pip 强制使用 `--no-index --find-links`，不会读取系统中
配置的 PyPI 或镜像地址。

## 验收

```bash
systemctl status dictation-v2 --no-pager
curl -fsS http://127.0.0.1:8889/api/health
python3 shared/tools/verify_wheelhouse.py v2/wheelhouse
v2/venv/bin/pip check
python3 shared/tools/audio_bundle.py inventory --audio-dir shared/web/audio
```

