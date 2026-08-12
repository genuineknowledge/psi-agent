# Haitun Agent OSS 发版与用户侧更新

## 发版流程

1. 修改 `.github/inno-setup/haitun.iss` 里的 `MyAppVersion`，推送 `main`。
2. `Nuitka` workflow 构建 Windows 安装包，产出 `haitun-agent-installer-nuitka` artifact。
3. `Publish Haitun Installer to OSS` workflow 检测该 commit 是否改动了 `haitun.iss`：
   - 如果 OSS 上的 `version.txt` 已等于本次版本，跳过；
   - 否则上传 `HaiTun_Agent_Setup.exe`、`HaiTun_Agent_Setup-<version>.exe` 和 `version.txt` 到阿里云 OSS。

当前使用 OSS bucket 直连下载，不经过 CDN，因此不需要 CDN 刷新权限。

## 需要的阿里云权限

- OSS bucket 的 `AccessKeyId` / `AccessKeySecret`，具备 `oss:PutObject` 写权限；
- bucket 名称和 endpoint（例如 `https://oss-cn-hangzhou.aliyuncs.com`）；
- bucket 需要对公网开放读权限（bucket ACL 为“公共读”，或至少 `HaiTun_Agent_Setup.exe` 对象为公共读），否则用户无法下载；上传脚本也会把新上传对象设为“公共读”。

## GitHub Actions 配置

Secrets：

| Secret | 说明 |
| --- | --- |
| `ALIYUN_ACCESS_KEY_ID` | 阿里云 AccessKeyId |
| `ALIYUN_ACCESS_KEY_SECRET` | 阿里云 AccessKeySecret |
| `ALIYUN_OSS_BUCKET` | OSS bucket 名称 |
| `ALIYUN_OSS_ENDPOINT` | OSS endpoint |

Variables：

| Variable | 默认值 | 说明 |
| --- | --- | --- |
| `HAITUN_DOWNLOAD_BASE_URL` | 空 | 公开下载目录 URL，末尾建议带 `/`，例如 `https://haitun-agent.oss-cn-hangzhou.aliyuncs.com/`；为空时安装包不启动更新检查 |
| `ALIYUN_OSS_PREFIX` | 空 | OSS 对象前缀；bucket 根目录就填 `/`（如果 GitHub 不允许留空） |
| `HAITUN_UPDATE_INTERVAL_HOURS` | `24` | 用户端检查更新的间隔小时数；联调时可临时设为 `1` |
| `HAITUN_UPDATE_INSTALLER_NAME` | `HaiTun_Agent_Setup.exe` | 用户端下载的安装包文件名 |

## 用户侧更新

打包时 `build-haitun-launcher.ps1` 会从 `haitun.iss` 读取版本号并生成 `examples/haitun-workspace/haitun-update.conf`：

```text
HAITUN_VERSION=1.0.0
HAITUN_UPDATE_BASE_URL=https://haitun-agent.oss-cn-hangzhou.aliyuncs.com/
HAITUN_UPDATE_INTERVAL_HOURS=24
HAITUN_UPDATE_INSTALLER_NAME=HaiTun_Agent_Setup.exe
```

`haitun.exe` 启动后会开启一个后台线程，每 24 小时请求 `<base>/version.txt`。发现远端版本与本地 `HAITUN_VERSION` 不一致时弹窗询问，用户确认后自动下载并启动 `<base>/HaiTun_Agent_Setup.exe`；下载失败时回退为用浏览器打开下载链接。

## 上线前检查

- 确认 `haitun.iss` 版本号已递增；
- 确认 OSS 上 `version.txt` 内容是纯版本号（例如 `1.0.1`）；
- 确认下载文件名是 `HaiTun_Agent_Setup.exe`，与 `HAITUN_UPDATE_INSTALLER_NAME` 一致；
- 首次发布时如果 OSS 还没有 `version.txt`，workflow 会直接上传；
- 确认 bucket 公共读权限已开启，浏览器能直接下载。
