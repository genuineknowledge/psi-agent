# POC L2 · 可操作则复现（初版设计）

> 2026-09-09。承接 `2026-09-09-poc-l1-padding-benchmark.md` §1.6 / §9。
> **刻意为之：** L1 不做海豚特化；本文件只描述 L2 最小闭环。

## 目标

材料里出现**可跟走的实验/取数说明**时，海豚：

1. **先判可操作性**；
2. 能 → 按结构化剧本复跑/核对；
3. 不能 → fail-closed 声明 **L2 不适用**，禁止暗示「数据已验真」。

## 初版范围（本 PR）

| 做 | 不做 |
|----|------|
| skill `poc-l2-reproduce` | L1 灌水审查 |
| tool `poc_l2_probe`（结构化 playbook） | 公开刷榜 / 盲评真人 |
| 方案 SOP 闭环自测 playbook | 对「当前会话」同步 `chat`（会死锁） |
| 静态断言：技能/工具/文案契约 | 特权系统取数、硬件、非确定 benchmark |

**可操作的第一类实验：** 海豚自己的能力包 / 工作区文件 / 工具导出 —— 例如「方案 SOP 闭环是否按设计接好」。

## 契约

### Playbook JSON

```json
{
  "id": "proposal-sop-closed-loop",
  "title": "方案 SOP 闭环自测",
  "l2_scope": "haitun_capability",
  "steps": [
    {
      "id": "s1",
      "kind": "assert_path",
      "path": "skills/proposal-writing-standard/SKILL.md",
      "root": "agent",
      "expect": "exists"
    },
    {
      "id": "s2",
      "kind": "assert_file_contains",
      "path": "skills/proposal-writing-standard/SKILL.md",
      "root": "agent",
      "must_contain": ["feishu_proposal_nudge", "四期"]
    },
    {
      "id": "s3",
      "kind": "assert_tool",
      "tool": "feishu_proposal_nudge"
    }
  ]
}
```

支持的 `kind`（初版）：

- `assert_path` — 路径存在 / 不存在（`root`: `agent` | `workspace`）
- `assert_file_contains` — 文件须含全部子串
- `assert_tool` — `tools/*.py` 导出同名 `async def`
- `unsupported` — 显式标记该步 L2 跑不了（记入 gaps，不假装通过）

### Tool 出口

`poc_l2_probe(...)` → JSON：

| 字段 | 含义 |
|------|------|
| `verdict` | `pass` / `fail` / `l2_not_applicable` |
| `operable` | 是否具备本 playbook 所需条件 |
| `steps[]` | 逐步结果 |
| `gaps[]` | 缺口（不可操作或失败原因） |
| `disclaimer` | 固定：L2 结果 ≠ 组织验收；无方法时不得声称已验真 |

仅 `method_text`、无结构化 playbook → **`l2_not_applicable`**（初版不猜自然语言方法），并提示改用 playbook。

## 与 acceptance-poc

| | `proposal-acceptance-poc` | `poc-l2-reproduce` |
|--|---------------------------|--------------------|
| 主业 | 方案契约 vs 检测文档对照建议 | 可操作则复现 / 自测 |
| 盖章 | 禁止 | 禁止 |

## 自测入口

仓库内 playbook：

`agents/feishu/skills/poc-l2-reproduce/playbooks/proposal-sop-closed-loop.json`

对海豚说：「按 L2 用方案 SOP 闭环 playbook 自测」→ 读 skill → `poc_l2_probe(playbook_path=...)`。
