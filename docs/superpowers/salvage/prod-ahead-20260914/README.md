# 生产领先于 main 的三个文件 — 原样保全（2026-09-14）

## 这是什么

生产机 `root@47.100.84.197` 的 `/srv/haitun/psi-agent/workspace/tools/` 里，有三个文件的内容
**在 git 任何 commit、任何分支上都不存在**（扫过最近 120 个远端分支）。它们只活在那台机器的文件
系统上，而 `workspace*/tools/` 是 bind mount、不在镜像里，所以**下一次镜像发布或批量投放就会把
它们冲掉**。

这个目录是原样快照，不做任何修改，只为它们丢不了。**不是可以直接用的代码**——三份都不能原样
覆盖回 `agents/feishu/tools/`，原因见下。

顺带解决一个卡点：`deploy/haitun/audit-workspace-drift.sh` 判"能否安全覆盖"的判据是
`git hash-object` 出来的 blob 在不在仓库里。这三份进了仓库之后，审计不再把它们报成「领先」、
不再 EXIT=1，部署闭环不再被它们卡住。

> ⚠️ 但**进了仓库 ≠ 可以覆盖**。判据回答的是「覆盖会不会丢没进 git 的代码」，不是「该不该覆盖」。
> 这三个文件的生产版本仍然领先于 main，投放时必须跳过，否则会把线上正在跑的功能换回旧版——
> 那不丢代码，但会退功能。

## 三份各自是什么

### `tencent_meeting.py`（9-14 14:19）

**是给内容分层打的适配补丁。** 分层把技能挪出 `<workspace>/skills` 之后，这个工具原来硬编码的
`parent.parent/"skills"/...` 就断了；这份加了 `_skill_script()`，改从 `PSI_CONTENT_ROOTS` 逐层
找技能脚本，legacy 路径垫底。`_skill_script` 在整个仓库搜不到。

**但同一份改动丢了 PR #859（9-08）加的 `anyio.fail_after(call_timeout)` 与
`TENCENT_MEETING_CALL_TIMEOUT`**（这份 `grep -c` 得 0）。那个保护是防上游挂起把会议 cron 永久
阻塞的。所以收编时**两者都要**，不是二选一——已按此在仓库里落地，见同批 PR。

同一批操作还在 `workspace/skills` 下建了 4 条软链指向 `/content/official/skills/`
（`tencent-meeting-mcp` / `positive-negative-list` / `workflow` / `fusion-flow-legacy`），给另外两个
硬编码该路径的工具兜底（`_positive_negative_list/rules.py`、`_gen_mcp_skill.py`）。那 4 条链同样
不在 git 里。

### `_card_dsl.py` + `_rookie_sop_card.py`（9-12 17:44）

来自海豚会话 `ou_3699761520fee5bd873e5aa101337189`（`sender_name` 是**马亚洲**）的一个
`card-dsl-migration` 活。生产机上还留着完整交付物：`CODE-CHANGES.md`、`TEST-REPORT.md`、
`card-dsl-migration.patch`（43KB）、三个验证脚本、一个 zip，以及 `.bak-layout` 备份。

改动：`_rookie_sop_card.py` 的 8 个建卡函数从手写飞书卡片 JSON 全改成 XML DSL 声明（删掉迁移后
失效的 5 个旧构造器）；`_card_dsl.py` 新增一个 **1.0 输出目标** `render_card(..., schema="1.0")`
配 `_legacy_guard` 白名单。它自己的验证结论是 10 个用例逐字段完全一致、差异 0。

**质量不是问题，基线是问题。** 这份不能合，有两个独立原因：

1. **它缺 main 上后来的两个提交。** 改的时候拿的是「当时的部署版」当底子，不是 git 最新版。
   实测：PR #941（`7c22ea96`）新增 20 行非空、这份里只有 4 行；PR #931（`a8e39926`）新增 11 行、
   只有 1 行。缺的是 `<collapse>` 折叠面板（#941 的核心：会议卡正文不再按字数截断）和 XML 属性
   转义（#931 修的多行卡片被压成一行）。**整份覆盖 = 静默退掉这两个已上线的功能。**

2. **它搭在未合并的 PR #867 上，摘不干净。** 那一层的 `schema == "1.0"` 分支挂在
   `_compile_terminal` 与 `_compile` 上，而 `_compile_terminal` 是 #867 引入的函数，main 上
   不存在。想只要 1.0 目标、不要 #867，就得重写代码去适配 main 的 `_compile`——那不是收编，
   是重做一遍，他那份测试报告随之全部作废、验证责任转移给重写的人。

生产那份 `_card_dsl.py` 实际是**三层叠加**：

| 层 | 来源 | 状态 | 独有的顶层 def |
| --- | --- | --- | --- |
| 底 | main 9-08 基线 | 已合并 | — |
| 中 | PR #867 | **OPEN 未合并** | `_compile_terminal` `_table_blocks` `_date_picker` `_select_static` `_candidate_block` `_candidate_index` `_cell_text` `_parse_rounds` `_pick_xml` `_text_element` |
| 上 | 马亚洲的 1.0 目标 | 只在生产 | `_legacy_guard` `_item_row_element` `_note_element` |

行数：main 602 / PR867 1053 / 生产 1209。

## 怎么落地（顺序不能反）

1. **先合 PR #867**（`Twin-Ghosts` 的活，独立成立）。
2. 再把马亚洲这一层落到 #867 之上，**并带上 #941/#931**。判据是他那 10 个用例复跑仍逐字段一致。
3. `tencent_meeting.py` 不依赖上面任何一步，已单独修好进仓库。

跳过第 1 步硬合，等于替两个人的活背验证责任——他们的验证结论都不是在 main 这个基线上得出的。
