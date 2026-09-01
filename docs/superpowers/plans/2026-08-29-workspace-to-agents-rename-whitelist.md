# workspace/ → agents/ 改名：docs/ 叙事保留白名单

**结论：判据 1（`git grep -nE 'workspace[/\\](tob|toc)'` 零命中）的唯一例外是 3 个文件，共 132 处。**
非 docs 已零命中；docs/ 其余 15 个文件的活引用已全部改指 `agents/feishu`（181 处）。

沿用 `a9099a25` 的先例（那次同样保留了叙事性旧路径）。

## 保留清单（文件粒度）

| 文件 | 处数 | 保留理由 |
|---|---|---|
| `specs/2026-08-28-gateway-workspace-refactor-report.md` | 60 | **报告自带整份声明**（`:28`）：「本报告里所有 `workspace/tob`、`workspace/toc` 字面路径都是改名前的坐标，读的时候按 `agents/feishu`、`agents/desktop` 换算」。该声明是上一轮（`b098505e`）为本次改名预先写的 |
| `specs/2026-08-28-gateway-workspace-refactor-report.html` | 46 | 同上，HTML 对应处在 `:720-724`（「本报告里的路径坐标都是改名前的」） |
| `plans/2026-08-29-workspace-to-agents-rename.md` | 26 | 本次改名的实施计划本身。每一处都是**映射规则**（`workspace/tob` → `agents/feishu`）、**判据正则**或**改前/改后对照指令**，改掉会让计划自相矛盾、无法复核 |

## 为什么整份保留而不是逐处改

两份报告是**已归档的汇报材料**，且作者已在文首显式声明全篇为改名前坐标。
若只改其中的"活引用"、留下"叙事"，同一份文档里会出现新旧坐标混排，而那句
「所有字面路径都是改名前的坐标」的声明将不再成立 —— 反而制造出比旧路径更难发现的错误。
负责人已就此决定：**整份按叙事保留，进白名单。**

## 一处未按目录改名处理的记录

`plans/2026-06-30-haitun-inno-setup.md:18` 与
`specs/2026-06-30-haitun-inno-setup-design.md:11` 记的是
`haitun.ico` → `workspace/tob/haitun.ico` 这次移动。
〔实测〕`haitun.ico` 现在在 `.github/inno-setup/haitun.ico`，**不在能力包里**（`git ls-files` 确认），
说明那次移动后来被撤回。这两处按目录改名一并改成了 `agents/feishu/`（与同文件其余 26 处一致），
但**它记录的移动目标本身已不成立** —— 属这两份历史计划文档的既有过期内容，不在本轮改名范围内。

## 判据命令

```bash
# 非 docs：必须零输出
git grep -nE 'workspace[/\\](tob|toc)' -- . ':!docs'

# docs 剩余：必须恰好是上表 3 个文件
git grep -oE 'workspace[/\\](tob|toc)' -- docs | cut -d: -f1 | sort | uniq -c
```
