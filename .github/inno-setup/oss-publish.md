# Haitun Agent OSS 发版与用户侧更新

## 发版流程

1. 修改 `.github/inno-setup/haitun.iss` 里的 `MyAppVersion`，推送 `main`。
2. `PyInstaller` workflow 构建 Windows 安装包，产出 `haitun-agent-installer-pyinstaller` artifact。
3. `Publish Haitun Installer to OSS` workflow 检测该 commit 是否改动了 `haitun.iss`：
   - 如果 OSS 上的 `version.txt` 已等于本次版本，跳过；
   - 否则上传 `HaiTun_Agent_Setup.exe`、`HaiTun_Agent_Setup-<version>.exe` 和 `version.txt` 到阿里云 OSS。

当前使用 OSS bucket 直连下载，不经过 CDN，因此不需要 CDN 刷新权限。

### 为什么发布通道是 PyInstaller 而不是 Nuitka

同一个安装包，PyInstaller 全链约 17 分钟，Nuitka 约 2 小时（三平台并行，墙钟取最慢
那个，99% 花在单条编译命令上）。两者对发版是等价的：`haitun.c` 硬编码
`psi-agent.exe`，两个 builder 都把 exe 拷到
`examples/haitun-workspace/psi-agent.exe`，`build-haitun-launcher.ps1` 只从
`haitun.iss` 解析 `MyAppVersion`，不碰 agent exe。两条流水线的
`haitun-inno-setup` job 结构也完全一致，只差来源 / 产出 artifact 名。

Nuitka 因此退成"只在发版时才编"：产物没有下游消费者，跨平台可编译性由
`PyInstaller` 兜（每次 main 推送和每个 PR 都编全三平台）。

### 各流水线的触发面

| Workflow | 触发 | 说明 |
| --- | --- | --- |
| `PyInstaller` | 任何分支的推送、PR | 三平台全编 + Windows 安装包；发布通道上游 |
| `Nuitka` | `main` 推送、`v*` tag、手动 | 仅当 `haitun.iss` 变动（或 `NUITKA_PLATFORMS` 强制）才真正编译；一旦编就是三平台全编 |
| `Publish Haitun Installer to OSS` | `PyInstaller` 在 `main` / `v*` 上成功完成 | 再按 `haitun.iss` 变动和 OSS `version.txt` 决定是否上传 |

`PyInstaller` 的触发面刻意保持宽口径：它承担跨平台可编译性的兜底职责，编得越勤，
问题暴露得越早。接成发布通道的上游不需要收窄它——管住发布面的是
`Publish Haitun Installer to OSS` 自己 `workflow_run` 上的 `branches: [main, v*]`
过滤，加上 job 级的 `head_branch == 'main'` 第二道闸，特性分支和 PR 的 run 根本到
不了发布这一步。

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
| `NUITKA_PLATFORMS` | 空 | 逗号分隔的平台列表。**设了就无条件覆盖"只在发版时才编"的判断**，是恢复"每次都编三平台"的总开关（填 `ubuntu-latest,windows-latest,macos-latest`），不需要改任何 workflow 文件；留空则按 `haitun.iss` 是否变动决定 |
| `NUITKA_RELEASE_PLATFORMS` | `ubuntu-latest,windows-latest,macos-latest` | 发版时编哪些平台。默认三平台全编；想只编 Windows 省时间就设成 `windows-latest` |

## 用户侧更新

打包时 `build-haitun-launcher.ps1` 会从 `haitun.iss` 读取版本号并生成 `examples/haitun-workspace/haitun-update.conf`：

```text
HAITUN_VERSION=1.0.0
HAITUN_UPDATE_BASE_URL=https://haitun-agent.oss-cn-hangzhou.aliyuncs.com/
HAITUN_UPDATE_INTERVAL_HOURS=24
HAITUN_UPDATE_INSTALLER_NAME=HaiTun_Agent_Setup.exe
```

`haitun.exe` 启动后会开启一个后台线程，每 24 小时请求 `<base>/version.txt`。发现远端版本与本地 `HAITUN_VERSION` 不一致时弹窗询问，用户确认后弹出“正在下载安装包”的持续提示窗口并自动下载，下载完成后窗口自动关闭并启动 `<base>/HaiTun_Agent_Setup.exe`；下载失败时窗口自动关闭并回退为用浏览器打开下载链接。

## 上线前检查

- 确认 `haitun.iss` 版本号已递增；
- 确认 OSS 上 `version.txt` 内容是纯版本号（例如 `1.0.1`）；
- 确认下载文件名是 `HaiTun_Agent_Setup.exe`，与 `HAITUN_UPDATE_INSTALLER_NAME` 一致；
- 确认 launcher（`haitun.c`）包含“下载中提示窗口”的最新实现；1.0.1 及更早版本安装包不含下载中提示；
- 首次发布时如果 OSS 还没有 `version.txt`，workflow 会直接上传；
- 确认 bucket 公共读权限已开启，浏览器能直接下载。
