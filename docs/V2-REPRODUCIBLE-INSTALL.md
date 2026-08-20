# V2 可复现安装规范

目标是从一台全新的 Ubuntu 24.04 VPS 开始，不复制旧数据库，不依赖原始 XLSX，
只用一个安装入口得到可验收的 V2：

1. Git 仓库中的教材 JSON 建立空白 SQLite；
2. 安装与教材版本严格绑定的标准 TTS 音频；
3. 可选导入真人录音覆盖层；
4. 配置 systemd、Caddy、HTTPS、防火墙和每日备份；
5. 校验题库、音频和健康接口。

## 发行物边界

| 发行物 | 存放位置 | 内容 |
|---|---|---|
| 源码 | Git 仓库 | 程序、教材 JSON、Studio 清单、建库与安装工具 |
| 标准音频 | `chinese/3a/tts/` | 869 个词条、25 个系统提示音，与源码一起版本化 |
| 真人录音 | 私有覆盖包 | 仅真人 MP3、`.recorded.json`、`.recorded_sys.json` |
| 运行数据 | VPS | SQLite 学习历史、Check 状态、待重录词表、备份 |

当前教材的短 MP3 直接进入 Git，保证一次 clone 即取得完整运行基线，不要求安装者
另有 TTS 账号或二次下载。安装器仍会逐文件检查，并把音频与三份教材/Studio JSON
绑定；教材改变后，缺失或冒充 MP3 的文件会被拒绝。

## 1. 教材包结构

```text
chinese/3a/
├── README.md
├── dataset.json
├── lessons.json
├── knowledge_points.json
├── studio_manifest.json
├── tts.sha256
└── tts/
    ├── sys/
    └── w/
```

`chinese/3a` 是只读发行资源；`shared/web/audio` 是部署时建立的可写运行副本。
真人录音只能覆盖运行副本，不能回写教材包。Git 中的基线因此始终保持纯净。

## 2. 从旧 V2 导出真人录音覆盖包

为避免更新或修改正在录音的旧 V2，在旧 VPS 另建一个只用于导出的临时检出。把
`RELEASE_REF` 换成将要部署到新 VPS 的精确提交或冻结标签：

```bash
git clone --no-checkout https://github.com/zkzchb/dictation.git /opt/dictation-export
git -C /opt/dictation-export checkout RELEASE_REF
DICTATION_CONTENT_ROOT=/opt/dictation-export/chinese/3a \
  python3 /opt/dictation-export/shared/tools/audio_bundle.py pack-human \
    --audio-dir /opt/dictation/shared/web/audio \
    --output /root/dictation-v2-human.tar.gz
sha256sum /root/dictation-v2-human.tar.gz
```

工具只读取两份真人录音台账，并只打包台账明确指向的音频，不会把未被覆盖的 TTS、
SQLite、成绩、Check 结果和待重录词表带入新站。旧版中若有扩展名为 `.mp3`、内容
实际为 WebM 的系统录音，导出会明确失败，应先转成真正 MP3。临时检出不会修改
`/opt/dictation`，也不会重启旧服务。

把覆盖包通过 SSH 传到新 VPS，例如：

```bash
scp /root/dictation-v2-human.tar.gz root@新VPS:/root/
```

## 3. 全新 VPS 一键安装

域名 A/AAAA 记录先指向新 VPS，然后执行：

```bash
apt update && apt install -y git
git clone https://github.com/zkzchb/dictation.git /opt/dictation
cd /opt/dictation
cp deploy/vps.env.example deploy/vps.env
nano deploy/vps.env
chmod 600 deploy/vps.env
sudo bash deploy/vps-install.sh
```

V2 正式发行配置示例：

```dotenv
DEPLOY_V1=no
DEPLOY_V2=yes
V2_DOMAIN=dictation.example.com
BASIC_AUTH_USER=your-user
BASIC_AUTH_PASSWORD=your-strong-password

V2_AUDIO_SOURCE=repository
CONTENT_ROOT=chinese/3a

V2_HUMAN_BUNDLE=/root/dictation-v2-human.tar.gz
V2_HUMAN_BUNDLE_SHA256=REPLACE_WITH_EXACT_SHA256
```

`deploy/vps-install.sh` 是唯一安装入口。配置文件只承载域名、口令、资源位置等数据，
不包含另一套部署逻辑。首次运行会：

- 从 `chinese/3a/lessons.json` 与 `knowledge_points.json` 建立新数据库；
- 创建 43 门课程、814 条知识点，学习动态表为空；
- 从仓库教材包复制并校验标准音频；
- 覆盖真人录音并恢复必要录音台账；
- 将 Check 与待重录状态初始化为空；
- 安装并启动 V2、Caddy、HTTPS、防火墙和每日备份；
- 验证 `/api/health` 与 894 个标准音频。

真人覆盖包按 SHA-256 记录已导入状态。重复执行安装脚本不会再次清空后来产生的
Check 进度；数据库和现有完整音频同样默认保留，因此安装器保持幂等。

## 4. 重新生成标准 TTS 时的维护模式

首次制作发行资源时，可以让新 VPS 直接调用有道 API：

```dotenv
V2_AUDIO_SOURCE=generate
YOUDAO_APP_KEY=...
YOUDAO_APP_SECRET=...
V2_TTS_INTERVAL=1.0
V2_TTS_RETRY=3
```

生成必须在没有真人台账的纯净工作目录进行，验证后再更新 `chinese/3a/tts`。正式安装
始终使用 `repository`，不要求每位安装者拥有同一 TTS 供应商账号，也不会因供应商返回
变化产生不同声音。教材规模以后明显增大时，`release` 模式仍作为备用分发方式保留。

## 5. 验收与冻结

```bash
curl -fsS http://127.0.0.1:8889/api/health
python3 /opt/dictation/shared/tools/audio_bundle.py inventory \
  --audio-dir /opt/dictation/shared/web/audio
python3 /opt/dictation/shared/tools/audio_bundle.py verify-dataset \
  --content-root /opt/dictation/chinese/3a
sqlite3 /opt/dictation/v2/dictation.db \
  'SELECT (SELECT count(*) FROM lessons), (SELECT count(*) FROM knowledge_points), (SELECT count(*) FROM dictation_history);'
```

期望结果：健康状态正常、音频 `complete: true`、数据库为 `43|814|0`。随后在手机 HTTPS
环境完成一次听写、Studio 录音、Check 标记和 Studio2 重录。全部通过后才能将精确提交
标记为 `v2.0.0`，Docker 版本从该标签创建。
