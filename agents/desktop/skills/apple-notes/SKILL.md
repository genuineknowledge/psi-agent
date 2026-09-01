---
name: apple-notes
description: Manage Apple Notes from the terminal via the external `memo` CLI — list, search, view, create, and edit notes that sync across Apple devices through iCloud. LOAD whenever a task on macOS needs to read, find, capture, or update Apple Notes (jot a note, look something up, append to an existing note). macOS only; needs `memo` installed via Homebrew.
category: apple
---

# Apple Notes（用 memo CLI 管理苹果备忘录）

## 定义

**apple-notes = 外部 `memo` CLI**（[antoniorodr/memo](https://github.com/antoniorodr/memo)），
一个 Python 写的命令行工具，经 **AppleScript / osascript** 驱动 macOS 的 **Notes.app**。
没有专用 tool——你用 `bash` 直接调 `memo notes`。笔记通过 iCloud 在所有 Apple 设备间同步。

**仅限 macOS。** 在其它系统上 `memo` 不存在也无法工作（Notes.app + osascript 是 Mac 专属）。

---

## 何时使用

- 用户要**记一条备忘**（购物清单、想法、待办）到苹果备忘录。
- 要**查找 / 查看**已有备忘录内容。
- 要**编辑/ 追加**某条备忘录。

**不用本 skill：**
- 不是苹果生态的普通文本文件 → 用 `read` / `write` / `edit`。
- 提醒事项（带时间/闹钟的 Reminders）→ 那是 `memo rem`，本 skill 只讲 `memo notes`。
- 非 macOS 环境 → memo 不可用，直接告诉用户。

---

## Setup（首次或报 command not found 时）

`memo` 是**用户级**外部 CLI，agent 不代跑安装，只引导用户执行：

```bash
brew tap antoniorodr/memo
brew install antoniorodr/memo/memo
```

首次运行会让 macOS 弹出 **Automation** 授权框，要允许终端 / psi-agent 控制 **Notes.app**，否则 osascript 调用会失败。

自检：

```bash
command -v memo && memo notes --help
```

缺 CLI 时把上面的安装提示**照实转达用户**，别假装成功。

---

## 核心命令速查（`memo notes`）

| 目的 | 命令 | 交互？ |
|------|------|--------|
| 列出全部笔记 | `memo notes` | 否 |
| 按文件夹过滤 | `memo notes -f "Folder Name"` | 否 |
| 查看第 N 条正文 | `memo notes -v N` | 否 |
| 列文件夹 | `memo notes -fl` | 否 |
| 绕过缓存读最新 | `memo notes -nc` | 否 |
| 新建笔记 | `memo notes -a`（`-f` 指定文件夹） | **是**（开 `$EDITOR`） |
| 编辑笔记 | `memo notes -e` | **是**（选编号 + 开 `$EDITOR`） |
| 搜索 | `memo notes -s` | **是**（交互式 fzf） |
| 删除 | `memo notes -d` | **是** |
| 移动到文件夹 | `memo notes -m` | **是** |
| 导出 HTML/Markdown | `memo notes -ex` | **是** |

笔记在列表里带**全局编号**，形如 `1. 购物清单`。`-v`/`-e` 用的就是这个编号。

---

## 非交互驱动（关键）

memo 的 create/edit/search 默认是给人用的交互式流程。agent 在 `bash` 里要这样绕开：

### 列出 / 查看（天然非交互，直接用）

```bash
memo notes                         # 全部，拿编号+标题
memo notes -f "Work"               # 只看 Work 文件夹
memo notes -v 1                    # 看第 1 条的 Markdown 正文
```

### 搜索（自己过滤，别用 -s）

`memo notes -s` 是交互式 fzf，不能脚本化传 query。改成**列出 + grep 标题**：

```bash
memo notes | grep -i "购物"        # 标题里含「购物」的笔记（连编号一起出）
```

### 新建（用 EDITOR shim 注入正文）

`memo notes -a` 会打开 `$EDITOR` 编辑一个临时 md 文件。把 `EDITOR` 换成一个
「把我们的正文写进那个文件」的小脚本，即可非交互创建：

```bash
# 把正文写好
cat > /tmp/note.md <<'EOF'
# 购物清单

- 牛奶
- 鸡蛋
- 咖啡豆
EOF

# EDITOR shim：memo 会执行 `$EDITOR <临时文件>`，shim 把我们的正文覆盖进去
printf '#!/bin/sh\ncat /tmp/note.md > "$1"\n' > /tmp/memo_editor.sh
chmod +x /tmp/memo_editor.sh

EDITOR=/tmp/memo_editor.sh memo notes -a -f "Notes"
```

第一行 `# 标题` 会成为笔记标题。不指定 `-f` 时默认进 `Notes` 文件夹（文件夹须已存在，
否则报错——先用 `memo notes -fl` 看有哪些文件夹）。

### 编辑（stdin 喂编号 + EDITOR shim 换正文）

`memo notes -e` 先从 stdin 读要编辑的**编号**，再开 `$EDITOR`。注意：编辑用的是
**不带 `-f`** 的全局编号（先 `memo notes` 看编号）。整篇正文会被替换：

```bash
memo notes                         # 先看编号，假设要改第 2 条
cat > /tmp/note.md <<'EOF'
# 购物清单

今天已全部买齐。
EOF
printf '#!/bin/sh\ncat /tmp/note.md > "$1"\n' > /tmp/memo_editor.sh
chmod +x /tmp/memo_editor.sh

echo "2" | EDITOR=/tmp/memo_editor.sh memo notes -e
```

用完清理临时文件：`rm -f /tmp/note.md /tmp/memo_editor.sh`。

---

## 典型工作流

**记一条**：想好标题+正文 → 写 `/tmp/note.md` → EDITOR shim + `memo notes -a`。
**查一条**：`memo notes | grep -i "关键词"` 拿编号 → `memo notes -v <编号>` 看正文。
**改一条**：`memo notes` 看编号 → 写新正文 → `echo <编号> | EDITOR=shim memo notes -e`。

---

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `memo: command not found` | 未安装 → 走 Setup，引导用户 `brew install`，别自己装 |
| osascript / Automation 报错 | 未授权终端控制 Notes.app → 让用户在「系统设置 → 隐私与安全性 → 自动化」里勾选 |
| `-a`/`-e` 卡住不返回 | 没设 EDITOR shim，memo 在等交互式编辑器 → 必须用上面的 shim 方案 |
| 新建报「folder 不存在」 | `-f` 的文件夹须先存在 → `memo notes -fl` 查，或在 Notes.app 里先建 |
| 想搜正文而非标题 | `memo notes -s` 不可脚本化；退而求其次逐条 `memo notes -v N` 再 grep 正文 |
| 含图片的笔记编辑异常 | memo 对图片/附件支持有限（AppleScript 限制），复杂笔记建议只读不改 |
| 非 macOS 上调用 | memo 只在 Mac 上有意义，别在 Windows/Linux 上尝试 |
