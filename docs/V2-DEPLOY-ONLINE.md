# V2 标准联网部署

适用分支：`main`。该分支是 Dictation 的唯一功能开发主线，不包含 Python
wheelhouse。教材 JSON 和 894 个标准音频属于产品内容，仍随源码版本化。

## 前置条件

- Ubuntu 24.04，x86_64 或 ARM64；
- root 权限；
- 可访问 GitHub、Ubuntu APT 和一个兼容 PyPI 的软件源；
- 至少准备公网 IP 或一个域名。

## 安装

```bash
apt update && apt install -y git
git clone --branch main https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
bash deploy/install-v2-online.sh
```

脚本开始时依次询问：公网 IP、主域名、备用域名、是否现在启用 HTTPS、访问账号、
访问密码和是否启用录音工作台。个人 IP、域名和密码不会写入 Git，只保存到服务器
上的 `deploy/vps.env`，权限为 `600`。

### 备案期间只用 IP

填写公网 IP，可以同时填写待备案域名；当脚本询问是否立即启用 HTTPS 时选择 `n`。
Caddy 只会配置：

```text
http://公网IP
```

因此不会提前触发域名证书申请。备案完成后重新运行同一脚本并选择 `y`，即可把域名
加入 Caddy，并自动申请和续期证书。

### 已具备域名条件

主域名和备用域名可以同时启用。脚本会生成一个共享 Caddy 站点，两个域名使用相同
应用、访问账号和数据目录；填写 IP 时仍保留显式 HTTP IP 入口。

## 依赖来源

`main` 使用 `v2/requirements.txt` 中固定的直接和传递版本，通过 pip 在线安装。
如需临时使用镜像，可在运行前设置：

```bash
export PIP_INDEX_URL=https://你的可信镜像/simple
bash deploy/install-v2-online.sh
```

正式离线部署不要修改本脚本，请改用 `main-offline` 或 GitHub Release 中的离线包。

## 验收

```bash
systemctl status dictation-v2 --no-pager
curl -fsS http://127.0.0.1:8889/api/health
python3 /opt/dictation/shared/tools/audio_bundle.py inventory \
  --audio-dir /opt/dictation/shared/web/audio
```

期望数据库为 43 门课程、814 个知识点，音频为 869 个词条和 25 个系统提示音。

