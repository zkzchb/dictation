# VPS 部署指南

V2.0.0 提供两条部署线，但共用数据库、systemd、Caddy、防火墙、备份和健康检查
实现。仓库不再保存个人域名、IP 或口令。

## V2 标准联网版

使用 `main`：

```bash
apt update && apt install -y git
git clone --branch main https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
bash deploy/install-v2-online.sh
```

完整说明：[docs/V2-DEPLOY-ONLINE.md](docs/V2-DEPLOY-ONLINE.md)。

## V2 离线版

使用 `main-offline` 或对应 GitHub Release。离线包包含完整源码、教材、标准音频、
Python wheelhouse 和 SHA-256 清单。完整说明位于该分支的
`docs/V2-DEPLOY-OFFLINE.md`。

离线是指部署时不访问 GitHub/PyPI；Ubuntu 系统包仍通过 APT 安装。

## 交互式设置

两个入口都首先询问：

1. 公网 IPv4；
2. 主域名；
3. 备用域名；
4. 是否现在启用 HTTPS；
5. 访问用户名和密码；
6. 是否启用录音工作台。

备案期间可填写 IP 和待备案域名，但选择暂不启用 HTTPS。Caddy 只监听显式
`http://IP`，不会尝试为待备案域名申请证书。备案完成后重跑入口脚本即可。

## 低级配置入口

需要自动化或非交互部署时，可复制 `deploy/vps.env.example` 为 `deploy/vps.env`，填写
后直接运行：

```bash
sudo bash deploy/vps-install.sh
```

`vps-install.sh` 是公共部署核心，一般用户不需要直接调用。

## 验收

```bash
systemctl status dictation-v2 --no-pager
curl -fsS http://127.0.0.1:8889/api/health
python3 /opt/dictation/shared/tools/audio_bundle.py inventory \
  --audio-dir /opt/dictation/shared/web/audio
sudo /usr/local/bin/backup-dictation.sh
```

期望：43 门课程、814 个知识点、869 个词条音频、25 个系统提示音，并生成数据库和
音频备份。

## V1

V1 仍保留在仓库中，需要有道 TTS 密钥。旧版 V1/V2 联合部署可根据
`deploy/vps.env.example` 的低级配置方式运行；V2.0.0 的新冻结入口只负责 V2。

