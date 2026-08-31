# V2 标准联网部署

当前联网部署需要同时检出程序与内容仓库：

```bash
apt update && apt install -y git
git clone --branch v2.1.0-rc.1 https://github.com/zkzchb/dictation.git /opt/dictation
git clone --branch content-v1.0.0 https://github.com/zkzchb/dictation-content.git /opt/dictation-content
cd /opt/dictation
bash deploy/install-v2-online.sh
```

安装器从 `v2/requirements.txt` 在线安装固定依赖，并要求选择一个通过校验、包含完整音频
的外部 content pack。交互配置、状态目录、备份、验收与升级说明见
[VPS 部署指南](../DEPLOY-vps.md)。

若使用可信 PyPI 镜像，可在运行前设置 `PIP_INDEX_URL`。真正的离线安装仍应使用匹配程序
版本和平台的已校验 wheelhouse；旧 `main-offline` 只对应 v2.0 冻结发行，不能与当前内容
包架构混用。
