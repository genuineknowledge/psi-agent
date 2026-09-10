---
name: proposal-acceptance-poc
description: "POC/评测节点验收建议(非 binding). LOAD when user submits POC test report/检测文档/评测结果 against a proposal step, or after a writing-standard POC 索要卡 and they paste the POC doc link. Haitun-feature POC 特化: 优先 haitun-feature-uat(A→B 干净 Session), 禁止对本会话同步 chat. Check scenario+samples+pass criteria vs report → short suggestion; human decides. NEVER 组织验收通过. NOT for 普通交付文档对照(proposal-acceptance-doc). NOT for 只催进度/建定时(proposal-writing-standard)."
category: productivity
---

# 方案 · POC 验收建议

针对方案(**执行跟进与验收 SOP v2.0**)里业务类为 **场景闭环**、落表验收门=`POC`
(明示评测/样本/及格线/可复跑) 的步骤(常见于实验期或实施期):
用户交来 **POC 检测文档** 之后, 对照方案里的场景·样本·及格线给**分钟级建议**.
人拍板; 海豚不盖章. 业务类识别见 writing-standard「三类里程碑」.

共享对照纪律见 [`proposal-acceptance-suggest`](../proposal-acceptance-suggest/SKILL.md);
本文是 **POC 材料到达后的特化入口**.

### 特化 · 海豚功能 POC(自对话 UAT · 刻意为之)

当本步 POC **测的是海豚自己的功能模块**(方案提醒/撰写检查/卡片闭环/飞书能力等, 而非外部业务系统)时,
**只读用户交来的检测文档不够** —— 那是 L1 文证, 省不掉「人跟海豚聊一遍」的真实验法.

1. **优先跑 A→B 对话验收** —— 加载 [`haitun-feature-uat`](../haitun-feature-uat/SKILL.md),
   调 `poc_feature_uat`(默认剧本或方案约定的 playbook).
   **禁止**对**当前** Session 同步 `subagent_chat` / chat(会死锁; 根因见该 skill).
2. **目标选对再跑**(与 feature-uat 同表): 已部署 defaults → 默认;
   同机 WIP 包 → `agent=`; 另一套 Gateway → `target_gateway_url=`; 已有干净 Session → `target_session_id=`.
3. **自动追问**(用户本轮已答清可不再盘问, 但仍须写进建议报告):
   - **为什么用这套及格线/样本判定「过」?**(相对方案契约 + 可选友商/业界评测口径)
   - **这份证据为什么有价值?**(对话 UAT 命中了什么; 文档对照补了什么; 还缺哪段可复跑)
4. **接线/复现声称** —— 用户或报告声称「已复现 / 数据已验真 / L2」时,
   另走 [`poc-l2-reproduce`](../poc-l2-reproduce/SKILL.md) / `poc_l2_probe`;
   **不得**仅凭 method_text 说验真.
5. **报告合并** —— 建议报告须同时写: 文档对照结论 + (若已跑) `poc_feature_uat` 的 `verdict`/失败案 gaps;
   仍 **非 binding**, 文末请人拍板.

非海豚功能的外部业务 POC: 仍以检测文档对照为主; 不强制开 feature-uat.
可在追问里对照友商/替代评测做法(有则写, 无则「未找对照·待验证」), **禁止编造**友商数字.

## When to use

- 「POC 报告在这 / 检测文档链接 / 评测跑完了, 帮看过没过」.
- 跟进索要 POC 材料之后, 用户丢回文档或说明.
- 方案步骤门类型为 POC, 且本回合有检测材料.
- 「帮我用干净 Session 把方案两能力/某功能 POC 打一遍」→ 本 skill 分流到 feature-uat.

## When not to use

- 交的是设计说明/交付说明/无评测跑批的文档 →
  [`proposal-acceptance-doc`](../proposal-acceptance-doc/SKILL.md).
- 还没交材料, 只要催 / 方案还在写 → [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md).
- 要 binding「验收通过可上线」→ **拒绝盖章**, 只出建议报告.
- 要按实验方法**重跑/验数据真实性（接线断言）**且用户不谈「功能对话 UAT」→
  [`poc-l2-reproduce`](../poc-l2-reproduce/SKILL.md)
- 用户**只要**对话 UAT、不谈方案契约对照 → 可直开 [`haitun-feature-uat`](../haitun-feature-uat/SKILL.md);
  若同时要「对照方案及格线」仍以**本文**为入口再调 feature-uat.

## 纪律(硬)

1. **非 binding** — 禁止「POC 验收通过」「组织认定合格」; 文末固定请人拍板.
2. **三者齐全才谈客观对照** — 方案侧须能定位: **场景 + 样本(或样本指针) + 及格线**;
   缺任一 → 报告写「无法完成 POC 对照 + 缺什么」, 不假装跑过.
3. **禁止编造跑批结果** — 没读到的数字/用例不得填写; 标「材料未提及」;
   feature-uat 未跑成功不得编造 Pass.
4. **检测文档优先于口述** — 证据以用户提交的 POC 文档为准; 对话口述可作补充但要标明.
5. **海豚功能 POC 不得跳过 A→B 机会** — 用户拒绝跑 UAT 须在报告写明「未跑对话 UAT, 仅文证」;
   不得把文证说成「已自测通过」.
6. **不加人第二套长表** — 建议几分钟读完.

## 输入

| 输入 | 必需? |
|------|--------|
| 方案中该步的场景/样本/及格线(或章节指针) | **是**(谈客观对照时) |
| POC 检测文档(链接或正文) **或** 明确要跑对话 UAT | **是**(二者至少其一; 都缺则只列待交清单) |
| 步骤名 / 方案链接 | 建议有 |
| 是否海豚功能 POC / playbook 路径 | 海豚功能时建议有; 缺省可用方案两能力自然语境剧本 |

## 引擎

```text
0. 判定是否海豚功能 POC
   → 是: 先对齐目标(agent=/gateway_url=), 提议或直接跑 poc_feature_uat;
         追问两句(及格线为何 / 证据价值); 接线声称 → poc_l2_probe
   → 否: 跳到文档对照路径
1. 固定 POC 契约: 场景列表、样本指针、及格线(摘自方案)
2. 读检测文档(feishu_doc_read / 粘贴) — 有则对照; 无文档但已跑 UAT → 以 UAT 为证据源并标明
3. 逐场景对照:
   - 已覆盖且数字/结论可指回报告或 UAT case
   - 未跑 / 未提及
   - 与及格线冲突或口径不一致
   - 无法判断(缺权限、报告含糊、样本对不上)
4. 输出「POC 验收建议报告」(下方模板; 海豚功能须含 UAT 段)
5. 可选: 请用户 1–5 分评价本建议是否有用(评海豚, 非盖章交付)
```

### 深度

| 档 | 本版 |
|----|------|
| 对照用户提交的 POC 文档 vs 方案契约 | **要做**(有文档时) |
| 海豚功能: A→B `poc_feature_uat` | **特化要做**(用户拒绝则报告注明) |
| 接线/静态可操作断言 `poc_l2_probe` | **声称复现时要做** |
| Agent 自己搭外部业务环境重跑题库 | **不做**(外部 L2 仍按 poc-l2-reproduce 边界) |

## 报告模板

```text
POC 验收建议 — <步骤名>

方案契约: 场景 / 样本 / 及格线 ← <章节或摘录>
检测文档: <链接或「未交」>
对话 UAT: <未跑 / verdict=… / playbook=… / target_mode=…>(海豚功能必填此行)
及格线/证据价值(追问纪要): …

对照:
| 场景或用例 | 建议看法 | 依据 |
|------------|----------|------|
| ... | 达到及格线迹象 / 未跑 / 冲突 / 无法判断 | 摘录+出处 或 UAT case id |

缺口: ...
说明: 以上为 Agent 建议, 是否通过由 <验收人> 决定.
```

## 成功标准

- 无三者齐全时不假装客观通过.
- 有材料或 UAT 时每条能对上场景或及格线.
- 海豚功能 POC: 未跑 UAT 不得写成「已自测通过」.
- 无 binding 盖章句.
---

## 与相关 skill

| Skill | 关系 |
|-------|------|
| [`haitun-feature-uat`](../haitun-feature-uat/SKILL.md) | 海豚功能 POC 的**执行器**; 本文管「对照方案契约 + 何时调它」 |
| [`poc-l2-reproduce`](../poc-l2-reproduce/SKILL.md) | 接线/静态复现; 与对话 UAT 正交 |
| [`proposal-writing-standard`](../proposal-writing-standard/SKILL.md) | 场景闭环到点催材料后进本文 |
