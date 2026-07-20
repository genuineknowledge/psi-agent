# SPA 打开即用：远程默认 AI 代理（VPS）

本目录属于 Web Console「打开即用」能力的运维侧：客户端（本机 Gateway / SPA）在未手动接模型时，请求 `https://haitun.addchess.cn/chat/completions`。VPS **只**做 HTTPS + 反代智谱并注入服务器上的 API Key，**不**跑 Haitun / Gateway / SPA。

```
本机 Gateway（无用户自配模型）
        │  HTTPS
        ▼
 haitun.addchess.cn     ← 本目录安装的 Nginx（TLS + 限流 + Authorization）
        │  HTTPS
        ▼
 open.bigmodel.cn       ← 智谱 BigModel（glm-4-flash）
```

密钥只留在 VPS 的 `config.env`（生成进 Nginx 私有片段），不进 Git、不进安装包、不进 `spa/dist`。

与前端的对应关系：

| 客户端 | 本目录 |
|--------|--------|
| `spa/src/bootstrapAi.js` → `POST /ais/bootstrap` | Nginx 入口 |
| Gateway `_ai_defaults.py` → `DEFAULT_REMOTE_BASE_URL` | `HAITUN_AI_HOST` |

## 目录

| 路径 | 作用 |
|------|------|
| `config.env.example` | 复制为 `config.env`，填 key |
| `nginx-haitun-ai.conf.example` | 站点模板 |
| `scripts/install-nginx-ai.sh` | 写密钥片段 + 装站点 |
| `scripts/reload-ai-secret.sh` | 只更新 key 后重载 Nginx |
| `scripts/healthcheck.sh` | 探活 |
| `scripts/remove-site.sh` | 可选卸载 |

## 同步到 VPS（推荐：zip + OrcaTerm）

不必用 `scp`（轻量常卡密码）。本机打 zip，控制台免密终端上传即可。

### 1. 本机打压缩包

```powershell
cd "D:\Haitun develop"
# 已确保 scripts 为 LF；也可直接用桌面上打好的包
Compress-Archive -Path "src\psi_agent\gateway\spa\remote-ai" `
  -DestinationPath "$env:USERPROFILE\Desktop\haitun-ai-deploy.zip" -Force
```

zip 根目录是一层 **`remote-ai/`**。

### 2. OrcaTerm 上传

1. https://console.cloud.tencent.com/lighthouse/instance → **登录**（OrcaTerm）
2. 工具栏 **上传**，选桌面 `haitun-ai-deploy.zip` → 传到家目录 `~`

### 3. 远程解压并覆盖 `/opt/haitun-ai`

**已装过站点（只更新脚本/模板）**：

```bash
cd ~
unzip -o haitun-ai-deploy.zip
sudo mkdir -p /opt/haitun-ai
sudo cp -a ~/remote-ai/. /opt/haitun-ai/
cd /opt/haitun-ai
sudo sed -i 's/\r$//' scripts/*.sh
sudo chmod +x scripts/*.sh
# 若只改了脚本/nginx 模板，重装站点片段：
sudo bash scripts/install-nginx-ai.sh
sudo bash scripts/healthcheck.sh
```

**注意**：`install-nginx-ai.sh` 会读现有 `/opt/haitun-ai/config.env`。不要删这份文件；zip 里只有 `config.env.example`，不含真密钥。

### 4. 远程机从零（一次性，仅新机）

```bash
cd ~
unzip -o haitun-ai-deploy.zip
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx unzip

sudo mkdir -p /opt/haitun-ai
sudo cp -a ~/remote-ai/. /opt/haitun-ai/
cd /opt/haitun-ai
sudo cp config.env.example config.env
sudo nano config.env   # 填写 PSI_AI_API_KEY=...

sudo sed -i 's/\r$//' scripts/*.sh
sudo chmod +x scripts/*.sh
sudo bash scripts/install-nginx-ai.sh
sudo certbot --nginx -d haitun.addchess.cn
sudo bash scripts/healthcheck.sh
```

改 key 后：

```bash
sudo nano /opt/haitun-ai/config.env
sudo bash /opt/haitun-ai/scripts/reload-ai-secret.sh
```

## 不要做的事

- 不要在 VPS 上 `uv sync` 整份 Haitun
- 不要在 VPS 上跑 Gateway / SPA
- 不要把 `config.env` 提交到 GitHub
- 不要把本目录拷进 `spa/dist`（Vite 默认也不会打包它）
