# Haitun Agent OSS 发版与用户侧更新

## 发版流程

1. 修改 `.github/inno-setup/haitun.iss` 里的 `MyAppVersion`，推送 `main`。
2. `PyInstaller` workflow 构建两平台安装包，产出两个 artifact：
   - `haitun-agent-installers`（Windows）内含：
     - `HaiTun_Agent_Setup.exe`（完整包）
     - `HaiTun_Agent_App_Setup.exe`（海豚组件包）
     - `msys-setup.exe`（环境组件包）
     - `haitun-version.txt` / `msys-version.txt`
   - `haitun-agent-macos`（macOS arm64）内含：
     - `HaiTun_Agent.dmg`
     - `haitun-version.txt`
3. `Publish Haitun Installer to OSS` workflow 检测该 commit 是否改动了 `haitun.iss`：
   - 如果 OSS 上的 `haitun-version.txt` 已等于本次版本，跳过；
   - 否则校验两平台版本号一致，按顺序上传四个安装包，最后上传
     `haitun-version.txt` 和 `msys-version.txt`。

macOS 的打包细节、所需 secrets 与真机验收清单见
[`.github/macos/macos-release.md`](../macos/macos-release.md)。

### 两平台共用 haitun-version.txt

macOS 与 Windows 共用同一个版本文件，因此**两个平台必须在同一个 workflow run 内
打包、同一次 publish 上传**。原因是这个文件同时充当发布闸门（OSS 版本号等于本次版本
即跳过上传）：若两平台各自 publish，先完成的那个会写上新版本号，把后者永久闸掉——
「mac 这次没编出来」会静默退化成「mac 再也发不出去」。

所以 `oss-publish.yml` 里下载 macOS artifact 的步骤刻意不设 `continue-on-error`，
mac 缺席就让整条 publish 变红。

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
| `Publish Haitun Installer to OSS` | `PyInstaller` 在 `main` / `v*` 上成功完成 | 再按 `haitun.iss` 变动和 OSS `haitun-version.txt` 决定是否上传 |

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
| `NUITKA_PLATFORMS` | 空 | 逗号分隔的平台列表。**设了就无条件覆盖"只在发版时才编"的判断**，是恢复"每次都编三平台"的总开关（填 `ubuntu-latest,windows-latest,macos-latest`），不需要改任何 workflow 文件；留空则按 `haitun.iss` 是否变动决定 |
| `NUITKA_RELEASE_PLATFORMS` | `ubuntu-latest,windows-latest,macos-latest` | 发版时编哪些平台。默认三平台全编；想只编 Windows 省时间就设成 `windows-latest` |

## 用户侧更新

打包时 `build-haitun-launcher.ps1` 会从 `haitun.iss` 读取版本号，生成
`examples/haitun-workspace/haitun-update.conf` 和
`examples/haitun-workspace/haitun-version.txt`；CI 同时计算 msys64 全量文件树指纹并写入
`examples/haitun-workspace/msys64/msys-version.txt`：

```text
HAITUN_UPDATE_BASE_URL=https://haitun-agent.oss-cn-hangzhou.aliyuncs.com/
HAITUN_UPDATE_INTERVAL_HOURS=24
```

`haitun.exe` 启动后读取本地 `app\haitun-version.txt` 与
`msys64\msys-version.txt`，每 24 小时请求 `<base>/haitun-version.txt` 和
`<base>/msys-version.txt`：

- 只有海豚不同：下载 `HaiTun_Agent_App_Setup.exe`；
- 只有环境不同：下载 `msys-setup.exe`；
- 两个都不同：下载 `HaiTun_Agent_Setup.exe`。

下载完成后启动对应安装器；安装器在 `PrepareToInstall` 中停掉海豚、把旧组件目录改名
为 `.backup`、安装新组件，并写 `rollback-state.json`。用户可双击安装目录下的
`rollback.cmd` 回滚到上一个版本。

## 安装期协议同意

安装向导第一页是协议页：两个链接分别打开《Haitun Agent 软件许可及服务协议》与《Haitun Agent 隐私保护政策》，**一个勾选框同时覆盖两份**，不勾则「下一步」禁用。这个形态由许可协议导言本身规定（「您在本软件安装过程中勾选同意本协议，即视为您同时同意隐私保护政策」），不是 UI 选择。

两份协议的 HTML 是 `docs/` 下 md 源的生成物，由 `scripts/gen_legal_html.py` 产出到 `src/psi_agent/gateway/spa-v2/public/`，安装器与产品内共用同一份。**改了 md 必须重新生成**，否则 CI 的 `--check` 步骤会失败。

**不记录同意状态。** 无注册表、无标记文件 —— 团队决定每次安装都勾。自动更新走完整向导（`haitun.c` 拉起 setup 未带 `/SILENT`），因此升级也会经过协议页。

**静默安装视为同意。** `/SILENT` 与 `/VERYSILENT` 会跳过全部向导页含协议页，未提供 `/ACCEPTTOS` 之类的显式参数。以静默方式批量部署本软件的一方，视为已代表最终用户接受上述两份协议，并应自行向最终用户完成协议告知。

## 上线前检查

- 确认 `haitun.iss` 版本号已递增；
- 确认协议 HTML 与 `docs/` 下 md 源一致（`python scripts/gen_legal_html.py --check`），协议换版时尤其要查；
- 确认 OSS 上 `haitun-version.txt` 是纯版本号、`msys-version.txt` 是环境指纹；
- 确认三个安装包均已上传：`HaiTun_Agent_Setup.exe`、`HaiTun_Agent_App_Setup.exe`、`msys-setup.exe`；
- 确认 launcher（`haitun.c`）包含“下载中提示窗口”的最新实现；1.0.1 及更早版本安装包不含下载中提示；
- 首次发布时如果 OSS 还没有 `haitun-version.txt`，workflow 会直接上传；
- 确认 bucket 公共读权限已开启，浏览器能直接下载。
