---
name: curator
description: 扫描 agent-created skills 并执行质量维护
cron: "0 */168 * * *"
---

## Curator 技能库维护任务

本任务由 psi-session scheduler 定时触发，实际执行由 `systems/curator.py` 中的 `maybe_run_curator()` 把关。

### 触发条件（AND，两个都满足才执行）

1. **时间间隔**：距上次 Curator 运行 ≥ 168 小时（7 天），记录在 `workspace/skills/.curator_state.json` 的 `last_run_at` 字段
2. **空闲检测**：agent 空闲时间 ≥ 2 小时（`idle_for_seconds >= 7200`）

启动时 `BackgroundReview` 会以 `idle_for_seconds=inf` 调用 `maybe_run_curator()`，模拟"刚启动 = 完全空闲"，与 hermes-agent 行为一致。

首次运行时（无 `last_run_at`），Curator 会种入当前时间戳并跳过，等待一个完整间隔后才真正执行。

### 执行内容

1. 扫描 `workspace/skills/` 目录，只处理 frontmatter 含 `created_by: agent` 的 SKILL.md
2. 按最后更新时间标记状态：
   - < 30 天：active
   - 30–90 天：stale
   - > 90 天：archive-candidate
3. 调用 LLM 进行语义审查，判断每个 skill 的处置方式（keep/patch/merge/archive）
4. 执行对应操作：
   - `archive`：移动到 `workspace/skills/.archived/<name>/`
   - `merge`：合并内容到目标 skill 后归档源 skill
   - `patch`：更新 SKILL.md 正文
5. 将操作摘要写入 `workspace/skills/.curator_report.md`
6. 更新 `workspace/skills/.curator_state.json` 的 `last_run_at`

### 调用方式

```python
from systems.curator import maybe_run_curator

summary = await maybe_run_curator(
    workspace_dir,
    complete_fn,
    idle_for_seconds=float("inf"),  # 启动时传 inf
)
```

`complete_fn` 签名：`async (messages: list[dict]) -> str`
