---
name: obsidian
description: "读/搜/建/改 Obsidian 知识库(vault)里的 Markdown 笔记 —— vault 就是磁盘上一个含 `.obsidian/` 的目录，笔记是带 YAML frontmatter(properties)、`[[wikilink]]` 双链和 `#tag` 标签的 `.md` 文件。用已有的 read / write / edit / find_files / search_content / list_dir + bash 工具直接操作这些文件，无需 Obsidian 应用运行、不新增依赖。LOAD whenever 用户提到 'Obsidian' / 'vault' / '知识库笔记' / 'wikilink' / '双链笔记'，或要:在 vault 里找/读某篇笔记、全文搜笔记、按标签或 frontmatter 属性过滤、新建笔记(含 frontmatter 和 [[链接]])、编辑已有笔记、维护/修复双链与反向链接、把知识沉淀成相互链接的笔记网络(作为 llm_wiki 的底层知识库载体)。NOT for 依赖 Obsidian 插件运行时的功能(Dataview 查询渲染、Canvas 白板、图谱可视化)。"
category: knowledge-base
---

# Obsidian —— 读 / 搜 / 建 / 改知识库笔记

用本 skill 直接操作一个 **Obsidian vault**：读笔记、全文/按标签搜索、新建带 frontmatter 和
双链的笔记、编辑已有笔记、维护 `[[wikilink]]` 链接网络。可作为 **`llm_wiki`** 工具的**底层
知识库载体**——把 agent 沉淀的知识落成 Obsidian 能直接打开、浏览、看关系图谱的 Markdown 文件。

**关键事实：vault 只是磁盘上的一个文件夹。** Obsidian 本身不需要运行——每篇笔记就是一个
`.md` 纯文本文件，双链、标签、属性全部是文件里的**文本约定**。所以本 skill **不调 Obsidian、
不装任何 CLI、不封装 Python tool、不新增依赖、不改 pyproject / nuitka / pyinstaller**，全部用
workspace 里已有的文件工具完成：

- `read` — 读单篇笔记全文。
- `write` — 新建 / 整篇覆写笔记。
- `edit` — 对已有笔记做精确字符串替换（改标题、补链接、更新属性）。
- `find_files` — 按文件名/路径找笔记（glob）。
- `search_content` — 跨 vault 全文搜索（含正则，用来搜 `[[链接]]`、`#标签`、frontmatter）。
- `list_dir` — 浏览 vault 目录结构。
- `bash` — 兜底的批量操作（统计、跨文件收集反链等）。

> 默认用**中文**回答，除非用户明显用别的语言。

## 何时使用

- **读**：定位并读出某篇笔记（按标题/路径），或列出某个文件夹下的笔记。
- **搜**：全文关键词搜、按 `#tag` 或 frontmatter 属性(`tags:` / `aliases:` / 自定义字段)过滤、
  找某个概念被哪些笔记引用（反向链接）。
- **建**：新建一篇笔记，带规范的 YAML frontmatter 和指向其它笔记的 `[[双链]]`。
- **改**：编辑已有笔记正文/属性，重命名后修复所有指向它的 `[[链接]]`，补齐或修复断链。
- **沉淀**：把一批知识编译成相互链接的笔记网络，长期复用（LLM wiki 模式）。

**不用本 skill：** 需要 Obsidian 插件运行时才能算的东西——Dataview 动态查询的渲染结果、Canvas
白板 `.canvas` 文件的可视化、关系图谱视图。这些要么读原始文件文本，要么本 skill 覆盖不了。

## Vault 结构与笔记格式（先搞清楚这些约定）

一个 vault 就是一个根目录，标志是里面有个 `.obsidian/` 配置文件夹。定位 vault 根：

```bash
# 从某个目录起，向下找含 .obsidian/ 的目录（即 vault 根）
find . -type d -name .obsidian -prune -exec dirname {} \; 2>/dev/null
```

一篇笔记 = 可选的 **YAML frontmatter**（Obsidian 里叫 "properties"）+ Markdown 正文：

```markdown
---
title: Rotary Positional Embeddings
aliases: [RoPE, 旋转位置编码]
tags: [transformers, attention, positional-encoding]
created: 2026-07-13
updated: 2026-07-13
---

RoPE 通过旋转 query/key 向量来编码相对位置。参见 [[Attention Is All You Need]]
和 [[Self-Attention|自注意力]]，对比 [[Absolute Positional Embeddings]]。

相关技术 #attention #长上下文
```

Obsidian 的三条核心文本约定（全是纯文本，可直接读写/正则搜）：

- **双链 wikilink**：`[[笔记标题]]` 或 `[[笔记标题|显示文字]]`；块/标题锚点写 `[[笔记#标题]]`、
  `[[笔记#^blockid]]`。**默认按文件名匹配**（Obsidian 默认设置下 `[[X]]` 指向文件名为 `X.md`
  的笔记，不含路径）。也支持嵌入 `![[图片.png]]` / `![[另一篇笔记]]`。
- **标签 tag**：正文里的 `#标签`（可嵌套 `#area/子标签`），或 frontmatter 的 `tags:` 列表。
- **属性 properties**：frontmatter 里的任意 YAML 字段，`aliases:` 让别名也能被 `[[别名]]` 链接。

> 注意 `[[wikilink]]` 是 Obsidian 特有语法，跟标准 Markdown 的 `[文字](路径.md)` 不同。本 vault
> 里两者可能混用，搜链接时两种都要考虑。`llm_wiki` 工具用的正是同一套 `[[wikilink]]` +
> frontmatter 约定，所以 `wiki/` 目录可以直接当成一个 vault 打开、互相通用。

## 读：定位并读出笔记

1. 已知标题/文件名 → 直接找文件再读：

   ```
   find_files(pattern="**/Rotary Positional Embeddings.md")
   read(path="<上一步得到的路径>")
   ```

2. 只记得大概 → 先全文搜，再读命中的文件（见下节）。
3. 浏览某个文件夹的目录结构：`list_dir(path="<vault>/某子目录")`。

读出后，正文里的 `[[X]]` 就是它链接到的其它笔记，可继续 `read` 跟着链接走。

## 搜：全文 / 标签 / 属性 / 反向链接

统一用 `search_content`（支持正则）在 vault 根下搜。常用配方：

- **全文关键词**：`search_content(pattern="旋转位置编码", path="<vault>")`
- **按标签**（正文 `#tag` 或 frontmatter）：
  `search_content(pattern="(#attention\\b|^\\s*-?\\s*attention$)", path="<vault>")`
  简单起见也可分两次：搜正文 `#attention`，搜 frontmatter 列表项 `attention`。
- **按 frontmatter 属性**：搜某字段值，如 `search_content(pattern="^status:\\s*draft", path="<vault>")`。
- **反向链接（谁引用了这篇）**：搜指向该标题的 wikilink——注意别名和 `|显示文字`、`#锚点`：
  `search_content(pattern="\\[\\[Rotary Positional Embeddings(\\||#|\\]\\])", path="<vault>")`
  记得对每个别名也搜一遍（别名在目标笔记的 `aliases:` 里）。
- **断链盘点**：先 `search_content` 抽出所有 `\[\[([^\]|#]+)` 目标名，再逐个 `find_files`
  确认对应 `.md` 是否存在；缺失的就是断链。批量时用 `bash` + `grep -rhoE` 收集更快。

## 建：新建一篇规范笔记

用 `write` 落一个新 `.md` 文件（文件名即默认的链接目标名）：

```
write(path="<vault>/Rotary Positional Embeddings.md", content="---\ntitle: Rotary Positional Embeddings\naliases: [RoPE]\ntags: [transformers, attention]\ncreated: 2026-07-13\nupdated: 2026-07-13\n---\n\n正文……用 [[Attention Is All You Need]] 交叉引用其它笔记。\n")
```

约定：
- **文件名 = 链接目标**。想让别人用 `[[某标题]]` 链到它，文件名就取 `某标题.md`（放哪个子文件夹
  通常不影响链接解析，Obsidian 默认按文件名全库解析）。
- frontmatter 里放 `title` / `aliases` / `tags` / 时间戳；正文里主动用 `[[…]]` 织进现有笔记网络，
  这样 Obsidian 的关系图谱和反链才连得起来。
- 建完可选：给被链接到的相关笔记补一条反向链接，让双链成对（用下节 `edit`）。

## 改：编辑正文 / 属性 / 修双链

- **改正文或属性**：`edit` 精确替换。如把 frontmatter 的 `status: draft` 改成 `status: done`，
  或在正文补一条 `[[新笔记]]` 链接。改内容时顺手更新 frontmatter 的 `updated:`。
- **重命名笔记 + 修所有引用**（Obsidian 应用里是自动的，这里手动做）：
  1. 用反向链接搜法找出所有引用旧名的笔记；
  2. `edit` 逐个把 `[[旧名]]` / `[[旧名|显示]]` / `[[旧名#锚点]]` 改成新名（保留 `|显示` 和 `#锚点`）；
  3. 重命名文件本身（`bash` 的 `mv "<vault>/旧名.md" "<vault>/新名.md"`）；
  4. 若旧名仍有价值，可把它加进新笔记的 `aliases:`，老链接就不算断。
- **修断链**：对断链目标，要么新建对应笔记（见"建"），要么把 `[[断名]]` 改指到正确的已存在笔记。

## 注意事项

- **不改 `.obsidian/`**：那是应用配置（主题、插件、快捷键），操作笔记时避开这个目录，搜索时用
  `find_files` / `search_content` 的路径限定绕开它。
- **wikilink 解析随 vault 设置变**：多数 vault 用默认的"最短唯一路径/纯文件名"链接；少数开了
  "绝对路径"或用标准 Markdown 链接。拿不准就先 `read` 几篇现有笔记看它们**实际**怎么写链接，
  照抄那个风格，别硬套。
- **文件名里的特殊字符**：`[]#|^` 在 wikilink 语法里有含义，起标题/文件名时尽量避开，否则链接会歧义。
- **和 `llm_wiki` 打通**：`llm_wiki` 工具把页面写在 `<workspace>/wiki/`，格式(frontmatter +
  `[[wikilink]]`)与 Obsidian 完全兼容。想让用户在 Obsidian 里打开这套知识库，直接把 `wiki/`
  （或任意笔记目录）当 vault 打开即可；反过来本 skill 也能直接读/改 `llm_wiki` 建的页面。

