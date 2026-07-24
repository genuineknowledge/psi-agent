# Subagent 与后台进程 — 工具 / Skill 索引

**日期**: 2026-07-24  
**范围**: `examples/haitun-workspace`  
**用途**: 交接与排查；标明「统一后台进程」与「子 Agent 编排」各落在哪些文件

---

## 1. 关系一句话

Subagent = **新起一个后台 Session**（可选复用父 AI）。  
落地时：**`subagent_*` 负责规划与对话**；**`background_*` 负责真正拉起/停止 OS 进程**；完整步骤写在 skill **`subagent-orchestration`**。

典型串法：

```text
subagent_plan
  → background_start（起 session / 必要时起 ai）
  → subagent_wait（等 socket）
  → subagent_chat（下发任务、收回最终文本）
  → background_stop
```

提示层说明见 `systems/prompt_sections.py`（Subagent 段 + tool 描述表）。

---

## 2. Tools — 统一后台进程

路径均相对 `examples/haitun-workspace/`。

| 文件 | 导出 tool | 作用 |
|------|-----------|------|
| `tools/background_start.py` | `background_start` | 后台启动 shell 命令，返回 `process_id`；进程在 tool 返回后继续跑 |
| `tools/background_stop.py` | `background_stop`、`background_list` | 按 `process_id` 停止；列出本工作区已注册后台进程 |
| `tools/_background_process_registry.py` | （无导出，`_` 不加载为 tool） | 注册表实现：落盘、起停、列表；也被其它模块当 `resolve_workspace` 等公共能力复用 |

---

## 3. Tools — Subagent

| 文件 | 导出 tool | 作用 |
|------|-----------|------|
| `tools/subagent_plan.py` | `subagent_plan` | 规划路径、socket、待执行命令；**不**启动进程 |
| `tools/subagent_wait.py` | `subagent_wait` | 等待 `ai_socket` / `channel_socket` 可连接 |
| `tools/subagent_chat.py` | `subagent_chat` | 向子 Session 发一条消息，只返回最终文本（不灌 reasoning） |
| `tools/_subagent_helpers.py` | （无导出） | `plan_subagent` / `chat_subagent` 等实现 |

---

## 4. 相关但不算「subagent 核心」的文件

| 文件 | 关系 |
|------|------|
| `tools/_session_helpers.py` | 会话索引里记录 `background_processes`；部分路径会调 `chat_subagent` |
| `tools/session_task_search.py` | 可按 `category=subagent` / `background` 检索任务 |
| `systems/prompt_sections.py` | 注册/描述 `background_*`、`subagent_*`；写静默 spawn 约定 |
| `systems/background_review.py` | hermes 风格后台回顾模块；**框架未接线**，不是 spawn 工具链 |

---

## 5. Skills — 专门 / 强相关

| 文件 | 角色 |
|------|------|
| `skills/subagent-orchestration/SKILL.md` | **主配方**：何时委派、如何逐步调用上述 tools、Gateway 复用父 AI 等 |
| `skills/session-management/SKILL.md` | 与 subagent 的边界（handoff vs 后台隔离）；监视在跑的 subagent |
| `skills/simplify-code/SKILL.md` | 实战：按 orchestration 并行派 3 路子 Agent 做精简 |

---

## 6. Skills — 仅引用 orchestration（非实现本体）

下列 skill 在文中提到或建议使用 `subagent-orchestration` / 并行子任务，**自身不实现** subagent 工具链。排查「subagent 坏了」时一般不用先改它们：

- `skills/research-paper-writing/SKILL.md` — 并行写章节  
- `skills/spike/SKILL.md` — 多路探针  
- 以及文中偶发提及的如 `plan`、`task-planning`、`dogfood`、`tmux`、`opencode` 等  

（以各 `SKILL.md` 内链为准；上表为常见引用方。）

---

## 7. 测试与文档交叉引用

| 位置 | 内容 |
|------|------|
| `tools/` 上列各 `*.py` | 行为以 docstring 为准 |
| `skills/subagent-orchestration/SKILL.md` | 操作步骤权威来源 |
| `tests/test_session_search.py` | 含 `category=subagent` 检索用例 |
| 包内 `AGENTS.md` Tools / Skills 表 | 产品级能力索引（若有条目以 AGENTS 为准同步） |

---

## 8. 维护约定（给改路径 / 解耦方案的人）

- 后台注册表、todo 等今日常落在「工作区」侧隐藏目录；若 AppData / Agent 路径拆分落地，优先改 `_background_process_registry` 的根解析，再核对 `_session_helpers` 与 todo 路径。  
- `skill_manage` / 改 skills 内容时：编排逻辑在 **skill**；进程生命周期在 **background_***；子会话协议在 **subagent_*** —— 三者职责不要揉进一个文件。  
