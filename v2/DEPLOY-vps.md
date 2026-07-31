# 听写小助手 V1 + V2 —— Ubuntu VPS 部署指南（Caddy 版）

V1（`v1.dictation.de5.net`）和 V2（`v2.dictation.de5.net`）部署在**同一台 Ubuntu 22.04/24.04 VPS**，共用一个 `caddy` 进程、一个 `dictation` 系统用户。

| 版本 | 域名 | 端口 | 特点 |
|---|---|---|---|
| V1 | v1.dictation.de5.net | 8888 | 运行时调用有道 TTS 合成音频，需要 ffmpeg |
| V2 | v2.dictation.de5.net | 8889 | 使用预录切片，无需 TTS/ffmpeg，**需要先运行 gen_slices.py** |

---

## 目录结构约定

```
/opt/dictation/
├── shared/          ← 唯一副本；V1/V2 均依赖，永不删除
│   ├── data/        题库 JSON
│   ├── web/         前端母本 + audio/ 切片（V2 用）
│   └── tools/       export_d1.py
├── v1/              V1 代码、数据库、音频缓存
├── v2/              V2 代码、数据库
├── attic/           废弃脚本（含明文密钥，部署时可省略）
└── tools/           stage.py
```

---

## 0. 前置条件

- Ubuntu 22.04 / 24.04，已有 root 或 sudo 权限
- `v1.dictation.de5.net` 和 `v2.dictation.de5.net` 均已 A 记录指向本机 IP
- 有道智云 APP_KEY / APP_SECRET（V1 TTS 用；V2 切片已在本地预生成时可不填）

验证域名解析：

```bash
dig +short v1.dictation.de5.net && dig +short v2.dictation.de5.net
```

两者均应返回本机公网 IP。与本机 IP 核对：

```bash
curl -s https://api.ipify.org && echo
```

---

## 1. 系统包

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip sqlite3 ufw curl \
    debian-keyring debian-archive-keyring apt-transport-https
```

V1 还需要 ffmpeg（pydub 依赖）：

```bash
apt install -y ffmpeg
```

V2 **不需要** ffmpeg。

---

## 2. 安装 Caddy

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list

apt update && apt install -y caddy
caddy version    # 记下版本，basic_auth 指令名与版本相关
```

---

## 3. 创建系统用户

```bash
adduser --system --group --home /opt/dictation --shell /usr/sbin/nologin dictation
```

---

## 4. 上传代码（本地执行）

**V2 需要先在本地生成音频切片**（首次约需数分钟，会调用有道 TTS）：

```bash
# 本地：填入有道密钥后执行
YOUDAO_APP_KEY=你的Key YOUDAO_APP_SECRET=你的Secret \
  python shared/gen_slices.py
```

切片生成后铺进 V2 目录：

```bash
python tools/stage.py v2
```

然后上传（把 `YOUR_VPS_IP` 换成实际 IP）：

```bash
# 上传 shared/ + tools/ + v1/ + v2/（不传 v3/、attic/、venv/、*.db）
rsync -avz \
  --exclude 'venv/' --exclude '__pycache__/' \
  --exclude '*.db' --exclude '*.db.backup' \
  --exclude 'attic/' --exclude 'v3/' \
  ./ root@YOUR_VPS_IP:/opt/dictation/
```

验证关键文件到位：

```bash
ls /opt/dictation/{v1,v2}/main.py \
   /opt/dictation/shared/data/lessons_2b.json \
   /opt/dictation/v2/audio/sys/intro.mp3
```

---

## 5. 建立虚拟环境

**V1**（含 ffmpeg 依赖的 pydub 和 requests）：

```bash
cd /opt/dictation/v1 && python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python -c "import fastapi, pydantic, requests, pydub; print('V1 依赖 OK')"
```

**V2**（不需要 ffmpeg/pydub）：

```bash
cd /opt/dictation/v2 && python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python -c "import fastapi, pydantic; print('V2 依赖 OK')"
```

---

## 6. 初始化数据库

> ⚠️ 如果你要迁移已有的 `v1/dictation.db`（含真实历史数据），**跳过本步**，直接上传 db 文件并修正属主后继续第 7 步。

```bash
# V1
cd /opt/dictation/v1 && ./venv/bin/python init_db.py

# V2（全新空库）
cd /opt/dictation/v2 && ./venv/bin/python init_db.py
```

验证：

```bash
sqlite3 /opt/dictation/v1/dictation.db \
  "SELECT COUNT(*) FROM knowledge_points; SELECT COUNT(*) FROM lessons;"
```

---

## 7. 配置密钥

```bash
mkdir -p /etc/dictation
```

**V1 密钥文件**（V1 需要有道 TTS）：

```bash
cat > /etc/dictation/v1.env << 'EOF'
YOUDAO_APP_KEY=你的AppKey
YOUDAO_APP_SECRET=你的AppSecret
AUDIO_OUTPUT_DIR=/opt/dictation/v1/audio
AUDIO_CACHE_DIR=/opt/dictation/v1/tts_cache
EOF
chmod 600 /etc/dictation/v1.env
```

**V2 密钥文件**（V2 不调用 TTS，留空或省略有道密钥）：

```bash
cat > /etc/dictation/v2.env << 'EOF'
DICTATION_DB=/opt/dictation/v2/dictation.db
EOF
chmod 600 /etc/dictation/v2.env
```

---

## 8. 设置目录权限

```bash
mkdir -p /opt/dictation/v1/audio /opt/dictation/v1/tts_cache
chown -R dictation:dictation /opt/dictation
chmod 755 /opt/dictation \
           /opt/dictation/v1 /opt/dictation/v1/audio \
           /opt/dictation/v1/dictation_www \
           /opt/dictation/v2 /opt/dictation/v2/audio \
           /opt/dictation/v2/dictation_www \
           /opt/dictation/shared
```

验证 caddy 用户可以读取前端和音频目录：

```bash
sudo -u caddy test -r /opt/dictation/v1/dictation_www/index.html && echo "V1 caddy 可读 OK"
sudo -u caddy test -r /opt/dictation/v2/dictation_www/index.html && echo "V2 caddy 可读 OK"
sudo -u caddy test -x /opt/dictation/v2/audio && echo "V2 audio caddy 可进入 OK"
```

---

## 9. systemd 服务

**V1 服务**（`/etc/systemd/system/dictation-v1.service`）：

```ini
[Unit]
Description=Dictation V1 API (TTS on-demand)
After=network.target

[Service]
Type=simple
User=dictation
Group=dictation
WorkingDirectory=/opt/dictation/v1
EnvironmentFile=/etc/dictation/v1.env
ExecStart=/opt/dictation/v1/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8888
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/dictation/v1

[Install]
WantedBy=multi-user.target
```

**V2 服务**（`/etc/systemd/system/dictation-v2.service`）：

```ini
[Unit]
Description=Dictation V2 API (pre-recorded slices)
After=network.target

[Service]
Type=simple
User=dictation
Group=dictation
WorkingDirectory=/opt/dictation/v2
EnvironmentFile=/etc/dictation/v2.env
ExecStart=/opt/dictation/v2/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8889
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/dictation/v2

[Install]
WantedBy=multi-user.target
```

启动：

```bash
systemctl daemon-reload
systemctl enable --now dictation-v1 dictation-v2
sleep 2
systemctl status dictation-v1 dictation-v2 --no-pager
```

本机验证接口可达：

```bash
curl -s http://127.0.0.1:8888/api/lessons | head -c 100
curl -s http://127.0.0.1:8889/api/lessons | head -c 100
```

---

## 10. 配置 Caddy

编辑 `/etc/caddy/Caddyfile`（把旧内容完整替换）：

```caddyfile
v1.dictation.de5.net {
    encode zstd gzip

    handle /api/* {
        reverse_proxy 127.0.0.1:8888 {
            transport http {
                dial_timeout 30s
                # V1 首次合成30词可能需要1-2分钟
                response_header_timeout 300s
            }
        }
    }

    # V1 生成的听写音频（每次新合成）
    handle /audio/* {
        root * /opt/dictation/v1
        header Cache-Control "public, max-age=3600"
        file_server
    }

    handle {
        root * /opt/dictation/v1/dictation_www
        file_server
    }
}

v2.dictation.de5.net {
    encode zstd gzip

    handle /api/* {
        reverse_proxy 127.0.0.1:8889 {
            transport http {
                dial_timeout 30s
                response_header_timeout 30s
            }
        }
    }

    # V2 预录切片（长期缓存，不会变化）
    handle /audio/* {
        root * /opt/dictation/v2
        header Cache-Control "public, max-age=604800"
        file_server
    }

    handle {
        root * /opt/dictation/v2/dictation_www
        file_server
    }
}
```

> **注意**：`handle /audio/*` 的 `root` 指向的是版本目录（`/opt/dictation/v1` 或 `/opt/dictation/v2`），**不是** `audio/` 子目录。Caddy 会把完整请求路径拼在 root 后面，所以 `/audio/x.mp3` 会被解析为 `/opt/dictation/v{1,2}/audio/x.mp3`——这是正确的。若写成 `root * /opt/dictation/v2/audio` 则会 404。

校验并重载：

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

Caddy 会在首次请求时自动申请 Let's Encrypt 证书，无需 certbot。
观察证书签发：

```bash
journalctl -u caddy -f
# 看到 "certificate obtained successfully" 后 Ctrl+C
```

---

## 11. 防火墙

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp   # HTTP/3
ufw --force enable
ufw status
# 确认 8888/8889 不在列表中
ss -tlnp | grep -E "8888|8889"
```

---

## 12. 加访问保护（建议）

```bash
# 生成密码哈希（把 "你的密码" 换掉）
caddy hash-password --plaintext '你的密码'
```

复制输出的 `$2a$14$...` 哈希，加到 Caddyfile 每个站点块最顶部（`encode` 之前）：

```caddyfile
v1.dictation.de5.net {
    basic_auth {
        mia $2a$14$哈希值
    }
    encode zstd gzip
    ...
}

v2.dictation.de5.net {
    basic_auth {
        mia $2a$14$哈希值
    }
    ...
}
```

> Caddy 2.8+ 用 `basic_auth`，2.7 及以下用 `basicauth`。用 `caddy version` 确认。

```bash
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```

---

## 13. 自动备份数据库

```bash
mkdir -p /var/backups/dictation/{v1,v2}

cat > /usr/local/bin/backup-dictation.sh << 'EOF'
#!/bin/bash
set -euo pipefail
for v in v1 v2; do
    SRC="/opt/dictation/$v/dictation.db"
    DEST="/var/backups/dictation/$v/dictation_$(date +%F).db"
    [ -f "$SRC" ] || continue
    sqlite3 "$SRC" ".backup '$DEST'"
    gzip -f "$DEST"
done
find /var/backups/dictation -name '*.db.gz' -mtime +30 -delete
EOF

chmod +x /usr/local/bin/backup-dictation.sh
/usr/local/bin/backup-dictation.sh  # 测试一次

( crontab -l 2>/dev/null; echo "0 3 * * * /usr/local/bin/backup-dictation.sh" ) | crontab -
```

---

## 14. 端到端验收

```bash
# V1 课程接口
curl -s https://v1.dictation.de5.net/api/lessons | head -c 150

# V2 课程接口
curl -s https://v2.dictation.de5.net/api/lessons | head -c 150

# V2 词表（应含 audio_url 字段）
curl -s "https://v2.dictation.de5.net/api/generate_daily/1?mode=daily" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']),'词, 首词:', d['data'][0]['target'], d['data'][0]['audio_url'])"

# V2 切片文件可访问
curl -sI "https://v2.dictation.de5.net/audio/sys/intro.mp3" | head -3
```

V1 音频合成（首次会调用有道 TTS，约30秒）：

```bash
curl -s -X POST https://v1.dictation.de5.net/api/generate_audio \
  -H 'Content-Type: application/json' \
  -d '[{"text":"诗人","pinyin":"shī rén"},{"text":"碧绿","pinyin":"bì lǜ"}]'
```

---

## 15. 日常运维

```bash
# 查看状态 / 重启
systemctl status dictation-v1 dictation-v2 --no-pager
systemctl restart dictation-v1 dictation-v2

# 实时日志
journalctl -u dictation-v1 -f
journalctl -u dictation-v2 -f

# Caddy
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy

# 磁盘占用
du -sh /opt/dictation/v1/audio /opt/dictation/v2/audio \
       /opt/dictation/v1/dictation.db /opt/dictation/v2/dictation.db
```

**代码更新**（本地执行 rsync 后重启对应服务）：

```bash
# 更新 V2 并重启（不碰 V1）
rsync -avz --exclude 'venv/' --exclude '*.db*' --exclude 'audio/' \
  v2/ root@YOUR_VPS_IP:/opt/dictation/v2/
ssh root@YOUR_VPS_IP 'chown -R dictation:dictation /opt/dictation/v2 && systemctl restart dictation-v2'
```

**新增切片后更新 V2**：

```bash
# 本地：增量生成新切片
python shared/gen_slices.py
# 本地：铺进 v2/
python tools/stage.py v2
# 上传音频到服务器
rsync -avz v2/audio/ root@YOUR_VPS_IP:/opt/dictation/v2/audio/
```

---

## 16. 故障排查

**systemd 服务起不来**：`journalctl -u dictation-v1 -n 50`。
常见原因：venv 路径错、`ProtectSystem=strict` 加了但漏掉 `ReadWritePaths`、JSON 数据库初始化未运行。

**V1 音频 500 / `ffmpeg not found`**：`apt install -y ffmpeg && systemctl restart dictation-v1`。

**V2 音频 404**：检查 `handle /audio/*` 的 `root` 是否指向版本目录而非 audio 子目录；检查 `v2/audio/` 是否已被 stage.py 填充。

**Caddy 拿不到证书**：域名 A 记录未生效，或 80 端口被防火墙挡。
`dig +short v1.dictation.de5.net` 和 `ufw status` 逐一确认。

**`attempt to write a readonly database`**：属主不对。
`chown dictation:dictation /opt/dictation/v1/dictation.db`。

---

## 附录：迁移已有历史数据（V1）

若本地 `v1/dictation.db` 含真实学习记录，上传时直接传 db 文件，**不要**运行 `init_db.py`：

```bash
# 本地
sqlite3 v1/dictation.db ".backup 'v1_upload.db'"
scp v1_upload.db root@YOUR_VPS_IP:/opt/dictation/v1/dictation.db

# 服务器
chown dictation:dictation /opt/dictation/v1/dictation.db
chmod 644 /opt/dictation/v1/dictation.db
systemctl restart dictation-v1
```

---

## 附录：将 dictation.de5.net 切换到某个版本

当某版本确定为正式版，把 `dictation.de5.net` CNAME 到对应子域名，或在 DNS 添加相同 A 记录，再在 Caddyfile 补一个同内容的站点块：

```caddyfile
dictation.de5.net {
    # 复制 v2.dictation.de5.net 的 handle 块，或直接 import（Caddy 2.8+）
    redir https://v2.dictation.de5.net{uri} permanent
}
```
