---
name: skill-authoring-when
description: "Decide WHETHER to create or patch a skill. REQUIRED before skill_manage(create): list existing skills first; if a similar domain skill exists, patch it — never spawn a parallel skill. Covers reuse-value gate and prefer-update-over-create. Load before authoring; not for one-shot tasks."
category: knowledge-base
---

# 何时创建 / 更新 Skill（判定门）

在调用 `skill_manage(action="create")` **之前**必须读本 skill。  
自进化 / background review / 用户口述新规则，**同一套判定**，且 **先于**「写新 skill」。

## When to use

- 用户给了可复用规则（评分标准、SOP、提醒偏好、领域流程）并希望「记住」。
- 你打算 `skill_manage create` 或自进化要落库时。

## When not to use

- 一次性任务、单次对比、纯闲聊 → 不建 skill。
- 定时提醒 → `feishu-schedule-message` / `schedule_manage`。
- 触发器 → `feishu-event-remind` / `trigger_manage`。

## 硬规则：先查重，有则更新，无则创建

**顺序（刻意为之，不可跳过）：**

1. `skill_manage(action="list")` — 扫现有 name + description。  
2. 若 name/描述/领域明显同类（例：已有 `feishu-resume-review`，用户又给「简历要看科研」）→ `view` 该 skill → **`patch`**，把新规则合并进对应章节。  
3. **仅当** list 后确认没有可承载的同类 skill → 才允许 `create`。  
4. 禁止为同一领域叠出 `resume-v2` / `简历评分-张三岗` 等平行 skill；岗位差异写进原 skill 的可编辑节，或在原 skill 增加「岗位覆盖」小节。

「类似」判定（满足任一条即应 patch）：

- 同一业务对象（简历 / 报销 / 合同审查 / …）  
- description 关键词高度重叠  
- 用户明确说「改一下以前的规则 / 在原来的标准上加」

## 复用价值门（create 仍要过）

只有**未来还会再用**的程序才建 skill。不过门 → 本回合直接做完，不落库。

| 可建 | 勿建 |
|------|------|
| 公司评分量表、固定 SOP、可复用检查清单 | 只服务这一次的临时步骤 |
| 用户纠正后的稳定偏好 | 环境/缺二进制等瞬时故障 |

## Boundaries

- 自进化也必须遵守「先 list → patch 优先 → 最后 create」。  
- 不可变包（如 `workflow`）禁止 patch/create 同名。
- 细节写法见 `skill-authoring-how`。
