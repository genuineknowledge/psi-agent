---
name: macos-computer-use
description: Drive the macOS desktop in the background — screenshots, mouse, keyboard, scroll, drag — without stealing the user's cursor, keyboard focus, or Space. Works with any tool-capable model. LOAD whenever the computer_use tool is available and the task needs a native Mac app (Mail, Messages, Finder, Figma, games, non-web GUIs).
category: apple
---

# macOS computer use（后台驱动 Mac 桌面）

## 定义

**computer_use = `computer_use` tool**，封装外部 `cua-driver`（macOS app + CLI）。
它经 macOS 无障碍树（Accessibility / AX）+ 合成输入事件（基于私有 SkyLight
框架）驱动目标 app，**不移动用户光标、不抢键盘焦点、不切换 Space**。任何具备
tool 调用能力的模型都能用。

| 你做 | Tool 做 |
|------|---------|
| 选动作 + 目标（`element` 序号优先） | 组 `cua-driver call <tool> '<json>'` |
| 读返回的 AX 索引 / 截图路径 | 截图落 `generated/computer_use/` |
| 用 `MEDIA:` / `[SEND:]` 把截图交给用户 | 返回 JSON/文本 + 绝对路径 |

**仅限 macOS**，且 `cua-driver` 需已安装并授予 **Accessibility + Screen
Recording** 权限（见下方 Setup）。

---

## 何时使用

- 任务需要操作**原生 Mac 应用**：Mail、Messages、Finder、Figma、Logic、游戏，
  以及任何非网页 GUI。
- 需要**后台**看屏/点按/输入，且不能打断用户当前正在做的事（光标、前台 app、
  Space 都不许动）。

**不用本 skill：**
- 网页自动化 → 用 browser 相关工具。
- 编辑文件 → 用 `read` / `write` / `edit`。
- 跑命令 → 用 `bash` / `powershell`。

---

## Setup（首次或报错时）

`computer_use(action="setup")` 会打印一键安装脚本并顺带跑 `doctor` 自检。
安装与授权是**用户级**操作，agent 不代跑安装脚本，只引导用户执行：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"
cua-driver permissions grant   # 批准 Accessibility + Screen Recording 两个弹窗
cua-driver doctor              # 验证安装/权限
```

- `computer_use(action="version")` / `action="doctor"` / `action="permissions")`：查装好没、权限够不够。
- 缺 CLI 或权限时 tool 会返回 `[Error] ...` 并附安装提示，**照实转达用户**，别假装成功。

---

## 核心工作流

1. **先 capture**，通常带 SoM 叠加：
   `computer_use(action="capture", mode="som", app="Safari")`
   返回带编号叠加的截图 + AX 索引（形如 `#7 AXButton 'Back' @ (12,80,28,28) [Safari]`）。
2. **按 element 序号操作**（跨模型比像素坐标更稳）：
   `computer_use(action="click", element=7)`
3. **再 capture 验证**；或在动作里带 `capture_after=True` 把复查截图合进同一次调用。

**capture 模式**：`som`（截图+叠加+AX 索引，默认）、`vision`（纯截图）、
`ax`（只要 AX 树文本、无图，适合纯文本模型 / 无 Screen Recording 权限）。

---

## 动作速查

| action | 关键参数 | 说明 |
|--------|----------|------|
| `capture` | `mode`, `app` | 截图/取 AX 树；SoM 截图存 `generated/computer_use/`，用 `MEDIA:` 交付 |
| `click` / `double_click` / `right_click` / `middle_click` | `element` 或 `coordinate=[x,y]`、`modifiers` | 优先 `element` 序号，坐标是兜底 |
| `type` | `text` | 输入文本 |
| `key` | `keys`（如 `"cmd+s"`, `"return"`, `"escape"`） | 按键 / 组合键 |
| `scroll` | `direction`(up/down/left/right), `amount`, `element`/`coordinate` | 滚动 |
| `drag` | `from_element`/`to_element` 或 `from_coordinate`/`to_coordinate` | 拖拽 |
| `focus_app` | `app`, `raise_window`（默认 False） | 切前台 app 状态；**默认不 raise 窗口** |
| `list_apps` | — | 列出可驱动的 app |
| `wait` | `seconds` | 等待 |
| `list_tools` / `describe`(`tool`) / `call`(`tool`,`args`) | — | 列/查/直调底层 MCP 工具 |

所有动作都可加 `capture_after=True`；点按/拖拽类可加 `modifiers=["cmd","shift"]`。

---

## 后台规则（重要，别破坏「不抢焦点」的承诺）

- **除非用户明确要求，`raise_window` 保持 False**，不要把窗口拉到前台。
- capture 尽量 **scope 到具体 `app`**，别整屏乱截。
- **不要切换 Space**；cua-driver 能驱动任意 Space 上的元素，无需切过去。
- 画布类 app（Blender / Unity / 游戏）可能仍需短暂前台激活，这类会**打破**
  「不抢焦点」承诺——先告知用户再操作。

---

## 版本差异与排错

底层 MCP 工具名 / 参数 schema 会随 `cua-driver` 版本变化。若某个驱动动作被拒：

1. `computer_use(action="list_tools")` 看有哪些工具。
2. `computer_use(action="describe", tool="<name>")` 看确切 schema。
3. `computer_use(action="call", tool="<name>", args='{"...":"..."}')` 按 schema 直调
   （`args` 是原样透传的 JSON 对象，会覆盖同名字段）。

---

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `[Error] cua-driver CLI not found` | 未安装 → 走 Setup，引导用户跑安装脚本 |
| 截图/权限报错 | 缺 Accessibility / Screen Recording → `action="permissions"` 查，让用户 `cua-driver permissions grant` |
| 点击没反应 | element 序号过期（界面变了）→ 重新 capture 再点 |
| 网页内容右键变左键、画布 app 需前台 | cua-driver 已知限制，见上方后台规则 |
| 用户没收到截图 | 忘了用 `MEDIA:<path>` / `[SEND:<path>]` 交付 tool 返回的绝对路径 |
