# Haitun Agent macOS 打包与发版

## 与 Windows 的关系

macOS 打包挂在 `pyinstaller.yml` 的 `haitun-macos-dmg` job 上，是 Windows 侧
`haitun-inno-setup` 的对等物。两条 job 在**同一个 workflow run** 内，因为两平台共用
`haitun-version.txt`——这个文件是 `oss-publish.yml` 的发布闸门（OSS 上版本号等于本次
版本即跳过上传），若两平台各自 publish，先完成的那个会把后者永久闸掉。

版本号唯一来源仍是 `.github/inno-setup/haitun.iss` 的 `MyAppVersion`。macOS 不自带
版本号，`build-dmg.sh` 用与 `build-haitun-launcher.ps1` 相同的正则从 iss 里解析。

| 平台 | 编译产物 | 安装包 | 组件数 |
| --- | --- | --- | --- |
| Windows | `psi-agent.exe` | 三个 exe（Full / App / MSYS） | 2（app + msys64） |
| macOS | `psi-agent`（arm64） | 一个 `HaiTun_Agent.dmg` | 1（app） |

macOS 是单组件，所以没有 App/MSYS/Full 之分。原因是产品运行时不 spawn 任何 POSIX
工具——全仓唯一的进程调用是 `_workspace_manager.py` 里开文件管理器（macOS 走
`open -R`）。Windows 塞一整套 MSYS 是因为 Windows 没有 POSIX 环境，macOS 自带。

## 架构

只出 **arm64**（`macos-latest` runner）。Intel Mac 装不了。

要加 x86_64 就在 job 上加 matrix（`macos-13`）并出两个 dmg，但那需要改动共用版本
文件的模型（两个包一个版本号），届时下载页也要让用户自己选芯片。universal2 不可行：
matplotlib / pymupdf 这类含 C 扩展的依赖没有完整的 universal2 轮子。

## 需要的 secrets

签名与公证按 **secret 有无自动开关**。没配就产出未签名 dmg，配上就自动签名公证，
`build-dmg.sh` 和 workflow 都不用改。

| Secret | 说明 | 缺失时 |
| --- | --- | --- |
| `MACOS_CERTIFICATE` | Developer ID Application 证书 p12 的 base64 | 跳过签名与公证 |
| `MACOS_CERTIFICATE_PWD` | p12 密码 | 同上 |
| `MACOS_KEYCHAIN_PWD` | CI 临时 keychain 密码（自定义任意值） | 用内置默认值 |
| `APPLE_ID` | 公证用 Apple ID | 签名但不公证 |
| `APPLE_APP_PASSWORD` | app-specific password（**不是** Apple ID 登录密码） | 同上 |
| `APPLE_TEAM_ID` | Team ID | 同上 |

`HAITUN_DOWNLOAD_BASE_URL`、`HAITUN_UPDATE_INTERVAL_HOURS`、`ALIYUN_*` 直接复用
Windows 侧现有配置，无需新增。

导出证书的命令（在装有证书的 Mac 上执行）：

```bash
security find-identity -v -p codesigning        # 确认证书存在
# 从钥匙串导出 .p12 后：
base64 -i Certificates.p12 | pbcopy             # 粘贴为 MACOS_CERTIFICATE
```

## 三种产物形态

取决于配了哪些 secret：

1. **未签名**（当前状态，证书申请中）——能装能跑，但从网络下载的 app 会被 Gatekeeper
   拦下。用户需右键「打开」，或 `xattr -dr com.apple.quarantine "/Applications/HaiTun Agent.app"`。
   适合内部试用，不适合面向普通用户发布。
2. **已签名未公证**——Gatekeeper 仍会警告，改善有限。这是过渡态，不建议停留。
3. **签名 + 公证 + 装订**（目标形态）——双击即装，无警告，体验对等 Windows。

## bundle 结构

```
HaiTun Agent.app/Contents/
  Info.plist                        # 版本号从 haitun.iss 注入
  MacOS/haitun                      # launcher.sh，CFBundleExecutable
  MacOS/psi-agent                   # PyInstaller 产物
  Resources/haitun.icns             # 由 haitun.ico 经 sips + iconutil 转换
  Resources/updater.sh              # 后台更新检查 + 换装
  Resources/rollback.sh             # 手动回滚
  Resources/haitun-workspace/       # agent 包（含 CI 注入的 .env）
```

**签名的 app 必须只读**，写入 bundle 内部会破坏签名并让 Gatekeeper 拒绝启动。而
agent 包在运行时会被写（`.env`、`logs/`、`.private/`），所以 `launcher.sh` 首次运行
把它从 `Resources/` 拷到 `~/Library/Application Support/Haitun/agent`。这是与 Windows
最大的结构差异——Windows 的 `{app}` 本身可写，装在 localappdata 下。

运行时路径：

| 内容 | 位置 |
| --- | --- |
| agent 包 | `~/Library/Application Support/Haitun/agent` |
| 凭证 / 历史 / state | `~/Library/Application Support/Haitun`（`platformdirs`，无需改代码） |
| 日志 | `~/Library/Logs/Haitun/<时间戳>.{out,err}.log` |
| 回滚状态 | `~/Library/Application Support/Haitun/rollback-state.json` |
| 用户交付物 | `~/Desktop/haitun交付` |

后续升级只覆盖构建方拥有的三个文件（`.env`、`haitun-update.conf`、
`haitun-version.txt`），用户改过的 tools/skills 保留。

## 用户侧更新

`updater.sh --watch` 由 launcher 后台拉起，每 `HAITUN_UPDATE_INTERVAL_HOURS`
（默认 24）拉一次 `<base>/haitun-version.txt`，与本地不同则 `osascript` 弹框询问。
同意后下载 dmg，再 detached 起 `updater.sh --apply` 换装：

1. 等 Gateway 进程退出（最多 120 秒，超时才 SIGTERM）——不打断进行中的对话
2. 旧 `.app` 改名为 `.backup`，写 `rollback-state.json`（`status: pending`）
3. 从 dmg 拷新 app 就位，清 quarantine 属性
4. 状态改 `done`，重启 app

失败即回滚 `.backup`。用户也可手动跑 bundle 内的
`Contents/Resources/rollback.sh` 回到上一版。

`rollback-state.json` 沿用 Windows 的 schema，`msys` 字段留空，两边格式可对照。

## 已知风险

**重签名导致 Keychain 条目失效。** `_auth_store.py` 用 keyring 把凭证加密密钥存进
Keychain，条目绑定签名身份。换证书后已有条目读不出来，用户需重新登录。代码有降级
路径（明文落盘 + warning，见 `_auth_store.py` 的 `_load_keyring`），不会崩。首次上线
签名版本时应提前告知用户。

**tray 在 macOS 不可用。** `_tray.py` 在后台线程跑 pystray 的 `icon.run()`，但
NSStatusItem 要求 Cocoa 事件循环在主线程。macOS 上 tray 会静默失败并被 except 吞掉，
只留一条 warning。因此 launcher 用 `--browser` 而非 `--tray`。这是独立缺陷，修它涉及
`_tray.py` / `_webview.py` 的线程模型，不在打包范围内。

**`.env` 里的 `SERPER_API_KEY` 随 dmg 分发到每台用户机器。** 与 Windows 现状相同，
属既有设计取舍，但 macOS 扩大了分发面。若该 key 有配额或计费风险，值得单独评估。

**首次运行拷 workspace 增加启动耗时。** 一次性，取决于 haitun-workspace 大小。

## 真机验收清单

CI 只能验证未签名路径能产出 dmg、结构正确、脚本过 shellcheck。以下必须在一台
Mac 上手动确认，**CI 无法覆盖**：

- [ ] dmg 能挂载，拖拽安装到 `/Applications`
- [ ] 首次启动：workspace 被正确拷到 Application Support，浏览器自动打开
- [ ] Gateway 能正常对话（含调用 AI 上游）
- [ ] 交付物落在 `~/Desktop/haitun交付`
- [ ] 日志写进 `~/Library/Logs/Haitun/`
- [ ] 签名版：`spctl -a -vv "/Applications/HaiTun Agent.app"` 报 accepted
- [ ] 公证版：双击安装无 Gatekeeper 警告
- [ ] 登录后重启 app，凭证仍在（Keychain 通路正常）
- [ ] 把 `HAITUN_UPDATE_INTERVAL_HOURS` 临时设 1，验证更新弹框 → 下载 → 换装 → 重启
- [ ] 更新后跑 `rollback.sh`，确认能回到上一版
- [ ] 更新后用户自己加的 tools/skills 仍在

## 发版流程

与 Windows 完全一致，无额外步骤：

1. 改 `.github/inno-setup/haitun.iss` 的 `MyAppVersion`，推 `main`
2. `PyInstaller` workflow 同时产出 `haitun-agent-installers`（Windows 三包）和
   `haitun-agent-macos`（dmg）
3. `Publish Haitun Installer to OSS` 校验两平台版本号一致，上传四个包，最后写
   `haitun-version.txt` 和 `msys-version.txt`

macOS artifact 缺失时 publish 会**整条失败**，这是刻意的：共用版本文件的前提是两
平台同步发布，否则版本文件会把 macOS 永久闸掉。
