# 新人入职 SOP 定时卡片与 HR 日报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新人入职后自动收到按 SOP 模块划分的可逐行勾选卡片，每日 9:30 按截止日催办，HR（罗霖）每日 18:30 收到一张汇总卡与一人一行的总览表链接。

**Architecture:** 建在 `feishu_message_send_card(multi_use=True)` 之上（PR #623 把卡片消费粒度降到单行），不复用也不修改 `feishu_todo_card_*`。飞书多维表格一个 base 两张表：明细表（一人多行）是唯一事实来源，总览表（一人一行）是从明细整体重算的投影。新人催办用 `fire=tool`（不经 LLM），HR 汇总用 `fire=prompt`（需现算聚合）。

**Tech Stack:** Python 3.14 / anyio / PyYAML / pytest（项目 venv 在 `.venv/`）；飞书多维表格与消息卡片 API 通过 workspace 工具 `feishu_bitable_*` / `feishu_message_send_card` 调用。

**设计文档:** `docs/superpowers/specs/2026-08-05-rookie-onboarding-agent-design.md`

## Global Constraints

- 所有测试用项目 venv 运行，并且**必须带 `-o addopts=""`**：
  `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest <路径> -q -o addopts=""`。
  不要用系统 `python3`（缺 `psi_agent`）。
  为什么要 `-o addopts=""`：`pyproject.toml` 的 `addopts` 里有 `--cov`，覆盖率会扫描整个仓库，
  单文件测试会因此挂住几分钟而不是零点几秒（实测：加了这个开关是 0.06s，不加会超时）。
- 测试里用 `importlib` 按路径加载 workspace 工具模块时，**必须在 `exec_module` 之前**
  `sys.modules[spec.name] = mod`。Python 3.14 的 dataclasses 内部按
  `sys.modules[cls.__module__]` 找命名空间，不注册会在 `@dataclass` 处抛 `AttributeError`。
- 中文全角标点若出现在**文档字符串或注释**里，ruff 报的是 `RUF002`/`RUF003`（不是 `RUF001`，
  那个只管字符串字面量）。按实际报出的码加 `# ruff: noqa: RUF002`（或 `RUF002, RUF003`）；
  加了不该加的码会被 `RUF100`（unused noqa）反过来报错。仓库里 `tools/_chart_caption.py` 是先例。
- 工具文件放 `examples/haitun-workspace/tools/`。**下划线开头的文件不会被注册成工具**（`_rookie_sop_*.py` 是内部模块），无下划线的每个文件对外暴露同名 async 函数。
- 纯逻辑模块（`_rookie_sop_config.py` / `_rookie_sop_progress.py` / `_rookie_sop_card.py`）**禁止 import `_feishu_impl` 或任何飞书模块**，以便单测不需要凭据。
- 工具返回值统一是 JSON 字符串；失败返回 `{"ok": false, "error": "..."}`，成功返回 `{"ok": true, ...}`。
- 卡片行的 action 名必须唯一且无前后空格：`rookie_tick_<item_id>`、`rookie_role_dev`、`rookie_role_nondev`。**没有规范 action id 的行会退回整卡去重**（退化成点一次就废）。
- **禁止**用 `feishu_message_edit_card` 改这些卡：它不重新注册回调，编辑后按钮全是死的。勾选后的原地重绘由框架自动完成。
- 飞书 bitable 的「查找引用」字段（type 19）API 建不出来，公式列写不进去 —— 总览表只能由工具双写维护。
- 总览行**永远从明细整体重算**，不做增量加减。
- 单卡最多 40 行。
- 各代码块开头写的 `# ruff: noqa: RUF00x` 是**占位提示，不要照抄**：先跑一遍 ruff，按它
  实际报出的码来写；一个都不报就整行删掉（留着会被 `RUF100` 判为无用指令而报错）。
- **不得吞掉被调用方的返回值。** 本仓库的 workspace 工具用返回字符串/字典表达失败
  （`schedule_manage` 失败返回 `"[Error] ..."` 而不抛异常；`mark_done` 会带回
  `duplicates`；`search_records` 会带回 `has_more`/`page_token`）。凡是调用后拿到的
  状态位，要么用上、要么往上报，不能读一半丢一半 —— 本计划已因此出过三次缺陷
  （Task 4 丢分页、Task 5 丢定时结果、Task 6 丢重复计数）。

## File Structure

**新建：**

| 文件 | 职责 |
|---|---|
| `examples/haitun-workspace/config/rookie_sop.yaml` | SOP 清单数据：模块、项、验收标准、窗口天数、适用角色 |
| `examples/haitun-workspace/tools/_rookie_sop_config.py` | 解析 yaml、展开条目、算截止日（纯函数） |
| `examples/haitun-workspace/tools/_rookie_sop_progress.py` | 从明细行算进度/逾期/今日到期/总览字段（纯函数） |
| `examples/haitun-workspace/tools/_rookie_sop_card.py` | 组各类卡片 JSON（纯函数） |
| `examples/haitun-workspace/tools/_rookie_sop_store.py` | bitable 读写 + 重算总览行（唯一碰飞书表格的模块） |
| `examples/haitun-workspace/tools/rookie_sop_card_send.py` | 入口工具：建表行 + 发全部模块卡 + 建催办定时 |
| `examples/haitun-workspace/tools/rookie_sop_tick.py` | 勾选回调：写明细 + 重算总览 |
| `examples/haitun-workspace/tools/rookie_sop_role_set.py` | 角色回调：定角色 + 展开或标不适用 |
| `examples/haitun-workspace/tools/rookie_sop_remind.py` | 定时 9:30：算欠项、发催办卡、全完成则收尾 |
| `examples/haitun-workspace/tools/rookie_sop_digest.py` | 定时 18:30：聚合发 HR 卡 + 兜底重算总览 |
| `examples/haitun-workspace/skills/feishu-rookie-onboarding/SKILL.md` | 何时用、回调怎么处理、零文本结束约定 |
| `examples/haitun-workspace/triggers/rookie-sop-welcome/TRIGGER.md` | 挂 `feishu.hr.user_created` |
| `examples/haitun-workspace/tests/test_rookie_sop.py` | 全部单测 |

**修改：**

| 文件 | 改什么 |
|---|---|
| `examples/haitun-workspace/AGENTS.md` | 工具表与 skill 列表各加一行（参照第 162、235 行 handbook 的写法） |

---

### Task 1: SOP 清单配置与解析

**Files:**
- Create: `examples/haitun-workspace/config/rookie_sop.yaml`
- Create: `examples/haitun-workspace/tools/_rookie_sop_config.py`
- Test: `examples/haitun-workspace/tests/test_rookie_sop.py`

**Interfaces:**
- Consumes: 无（第一个任务）
- Produces:
  - `load_sop(cfg: dict) -> list[SopItem]` — 把 yaml 字典展开成扁平条目列表
  - `SopItem` dataclass，字段：`item_id: str`、`module: str`、`title: str`、`acceptance: str`、`window_days: int`、`dev_only: bool`
  - `due_date(onboard_date: date, window_days: int) -> date` — `onboard + window_days - 1`
  - `day_index(onboard_date: date, today: date) -> int` — 入职第 N 天，自然日，入职日为 1
  - `applicable_items(items: list[SopItem], role: str) -> list[SopItem]` — `role` 为 `"dev"` / `"nondev"` / `""`（未确认）；`nondev` 过滤掉 `dev_only`，`""` 也过滤掉 `dev_only`（未选时不计入分母）

- [ ] **Step 1: 写配置文件**

创建 `examples/haitun-workspace/config/rookie_sop.yaml`。条目取自 `rookieSOP.txt`；`window_days` 按 SOP 的 Day 窗口（「Day 1-3」= 3）。

```yaml
# 新人入职 SOP 清单 —— 改这里不用改代码。
# window_days: SOP 的 Day 窗口天数（「Day 1-3」= 3）；截止日 = 入职日 + window_days - 1
# dev_only: true 表示仅研发适用（新人在卡上自选角色后生效）

company_name: 真知
card_title_prefix: 入职路线图
sop_doc_url: ""          # 完整 SOP 文档链接，上线前替换
hr_notify_id: ""         # 罗霖的 open_id，空则日报不发
hr_notify_id_type: open_id

modules:
  - name: 环境准备
    window_days: 1
    items:
      - id: wifi
        title: 连上 WiFi
        acceptance: 能上网
      - id: campus_card
        title: 领取校园卡
        acceptance: 能刷脸进校门
      - id: desk
        title: 找到工位
        acceptance: 知道「我的座位在哪」
      - id: feishu_account
        title: 开通飞书账号
        acceptance: 能登录飞书、看到团队群
      - id: todo_account
        title: 开通 TODO 账号
        acceptance: 能打开 TODO 库

  - name: 认识人
    window_days: 1
    items:
      - id: know_mentor
        title: 认识 Mentor
        acceptance: 知道 Mentor 的名字和飞书
      - id: know_leader
        title: 认识小组负责人
        acceptance: 知道谁给你派活
      - id: know_functions
        title: 认识职能对接人
        acceptance: 知道缺东西、报销、请假找谁
      - id: join_groups
        title: 加入所有必要飞书群
        acceptance: 日会群、项目群、全员群都在

  - name: 协同体系
    window_days: 2
    items:
      - id: mosaic_org
        title: 理解马赛克组织
        acceptance: 能理解「角色 ≠ 职级」
      - id: todo_is_core
        title: 理解 TODO 是核心工具
        acceptance: 打开 TODO 库并能编辑
      - id: who_assigns
        title: 搞清谁派活、谁管你
        acceptance: 能说出三个人分别是谁
      - id: role_not_promotion
        title: 理解角色不是晋升
        acceptance: 理解「专家和复合型人才同样被认可」
      - id: cross_project
        title: 理解兼角色和跨项目
        acceptance: 知道自己当前在哪些项目里

  - name: 工作规范
    window_days: 2
    items:
      - id: todo_writing
        title: TODO 写法
        acceptance: 每条 TODO = 做什么 + 标准 + 截止时间
      - id: closed_loop
        title: 闭环要求
        acceptance: 接任务→按时交付→主动反馈
      - id: communication
        title: 沟通规范
        acceptance: 带着方案找人，飞书文字留痕
      - id: delivery_standard
        title: 交付标准
        acceptance: 别人拿到可以直接用、不用返工
      - id: confidentiality
        title: 保密规范
        acceptance: 不把公司代码/数据喂给公共 AI
      - id: ai_usage
        title: AI 使用规范
        acceptance: AI 生成的代码需要审查后提交

  - name: 核心制度
    window_days: 3
    items:
      - id: attendance
        title: 了解考勤制度
        acceptance: 知道打卡时间与补卡规则
      - id: leave
        title: 了解请假制度
        acceptance: 知道飞书审批怎么请假
      - id: payroll
        title: 了解薪酬与考核
        acceptance: 知道发薪日与 7:3 考核口径

  - name: 每周节奏
    window_days: 5
    items:
      - id: daily_meeting
        title: 参加日会
        acceptance: 知道会议号和项目组
      - id: todo_update
        title: 按时更新 TODO
        acceptance: 第一次成功更新 TODO
      - id: weekly_meeting
        title: 参加周会
        acceptance: 线下参加周会（A450）
      - id: monthly_review
        title: 月度复盘
        acceptance: 完成第一次月度复盘

  - name: 开发环境
    window_days: 7
    items:
      - id: read_agents_md
        title: 读 AGENTS.md
        acceptance: 能用一句话说出项目的代码规范
        dev_only: true
      - id: setup_dev_env
        title: 配好开发环境
        acceptance: 能 clone 代码并跑起来
        dev_only: true
      - id: git_workflow
        title: 了解 Git 工作流
        acceptance: 知道怎么提 PR
        dev_only: true
      - id: code_review
        title: 了解 Code Review
        acceptance: 知道代码要谁 review
        dev_only: true
      - id: repo_access
        title: 开通仓库权限
        acceptance: 能 push 代码
        dev_only: true
```

- [ ] **Step 2: 写失败测试**

创建 `examples/haitun-workspace/tests/test_rookie_sop.py`：

```python
"""新人入职 SOP 卡片与日报。"""

from __future__ import annotations

# ruff: noqa: RUF002  # 占位: 按 ruff 实际报出的码改, 一个都不报就删掉这行
import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import Any

HAITUN = Path(__file__).resolve().parents[1]
TOOLS = HAITUN / "tools"


def _load(module_name: str) -> Any:
    """按文件路径加载 workspace 工具模块（它们不是包，靠 sys.path 找同级依赖）。"""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    path = TOOLS / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{module_name}_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CFG: dict[str, Any] = {
    "modules": [
        {
            "name": "环境准备",
            "window_days": 1,
            "items": [
                {"id": "wifi", "title": "连上 WiFi", "acceptance": "能上网"},
                {"id": "desk", "title": "找到工位", "acceptance": "知道座位"},
            ],
        },
        {
            "name": "核心制度",
            "window_days": 3,
            "items": [{"id": "attendance", "title": "了解考勤", "acceptance": "知道打卡"}],
        },
        {
            "name": "开发环境",
            "window_days": 7,
            "items": [
                {"id": "git_workflow", "title": "Git 工作流", "acceptance": "会提 PR", "dev_only": True},
            ],
        },
    ]
}


def test_load_sop_flattens_modules_and_marks_dev_only() -> None:
    cfg = _load("_rookie_sop_config")
    items = cfg.load_sop(_CFG)

    assert [i.item_id for i in items] == ["wifi", "desk", "attendance", "git_workflow"]
    assert items[0].module == "环境准备"
    assert items[0].acceptance == "能上网"
    assert items[0].window_days == 1
    assert items[0].dev_only is False
    assert items[3].dev_only is True
    assert items[3].window_days == 7


def test_due_date_is_inclusive_of_the_first_day() -> None:
    cfg = _load("_rookie_sop_config")
    # 「Day 1」窗口 1 天 → 当天截止；「Day 1-3」窗口 3 天 → 入职日 +2
    assert cfg.due_date(date(2026, 8, 5), 1) == date(2026, 8, 5)
    assert cfg.due_date(date(2026, 8, 5), 3) == date(2026, 8, 7)
    # 跨月
    assert cfg.due_date(date(2026, 8, 30), 7) == date(2026, 9, 5)


def test_day_index_counts_natural_days_from_one() -> None:
    cfg = _load("_rookie_sop_config")
    assert cfg.day_index(date(2026, 8, 5), date(2026, 8, 5)) == 1
    assert cfg.day_index(date(2026, 8, 5), date(2026, 8, 7)) == 3
    # 跨月
    assert cfg.day_index(date(2026, 8, 30), date(2026, 9, 2)) == 4


def test_applicable_items_filters_dev_only_unless_role_is_dev() -> None:
    cfg = _load("_rookie_sop_config")
    items = cfg.load_sop(_CFG)

    assert [i.item_id for i in cfg.applicable_items(items, "dev")] == [
        "wifi",
        "desk",
        "attendance",
        "git_workflow",
    ]
    assert [i.item_id for i in cfg.applicable_items(items, "nondev")] == ["wifi", "desk", "attendance"]
    # 角色未确认时也不计入分母
    assert [i.item_id for i in cfg.applicable_items(items, "")] == ["wifi", "desk", "attendance"]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`FileNotFoundError` 或 `ModuleNotFoundError`（`_rookie_sop_config.py` 还不存在）

- [ ] **Step 4: 写最小实现**

创建 `examples/haitun-workspace/tools/_rookie_sop_config.py`：

```python
"""SOP 清单解析 —— 纯逻辑，不碰飞书，便于单测。

刻意为之: 清单本身在 agent 包 config/rookie_sop.yaml 里, 改 SOP 不用改代码
(与 config/handbook_onboarding.yaml 同一模式)。
"""

from __future__ import annotations

# ruff: noqa: RUF002  # 占位: 按 ruff 实际报出的码改, 一个都不报就删掉这行
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ROLE_DEV = "dev"
ROLE_NONDEV = "nondev"


@dataclass(frozen=True)
class SopItem:
    item_id: str
    module: str
    title: str
    acceptance: str
    window_days: int
    dev_only: bool


def load_sop(cfg: dict[str, Any]) -> list[SopItem]:
    """把 yaml 的 modules → items 两层结构展开成扁平条目列表, 保持声明顺序。"""
    items: list[SopItem] = []
    modules = cfg.get("modules")
    if not isinstance(modules, list):
        return items
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_name = str(module.get("name") or "").strip()
        window_days = module.get("window_days", 1)
        window = window_days if isinstance(window_days, int) and window_days > 0 else 1
        raw_items = module.get("items")
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id") or "").strip()
            if not item_id:
                continue
            items.append(
                SopItem(
                    item_id=item_id,
                    module=module_name,
                    title=str(raw.get("title") or "").strip() or item_id,
                    acceptance=str(raw.get("acceptance") or "").strip(),
                    window_days=window,
                    dev_only=bool(raw.get("dev_only")),
                )
            )
    return items


def due_date(onboard_date: date, window_days: int) -> date:
    """SOP 的「Day 1-3」表示第 1 到第 3 天, 所以窗口 3 天的截止日是入职日 +2。"""
    window = window_days if window_days > 0 else 1
    return onboard_date + timedelta(days=window - 1)


def day_index(onboard_date: date, today: date) -> int:
    """入职第 N 天, 自然日计数, 入职日为第 1 天。

    刻意为之: 不跳过周末 —— SOP 的 Day 1-7 本身就是自然日窗口。
    """
    return (today - onboard_date).days + 1


def applicable_items(items: list[SopItem], role: str) -> list[SopItem]:
    """按角色过滤。角色未确认("")时也排除 dev_only, 免得进度分母虚高。"""
    if role.strip().casefold() == ROLE_DEV:
        return list(items)
    return [i for i in items if not i.dev_only]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 4 passed

- [ ] **Step 6: 校验 yaml 能被解析且 item_id 唯一**

Run:
```bash
cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -c "
import sys, yaml
sys.path.insert(0, 'examples/haitun-workspace/tools')
import importlib.util
spec = importlib.util.spec_from_file_location('c', 'examples/haitun-workspace/tools/_rookie_sop_config.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cfg = yaml.safe_load(open('examples/haitun-workspace/config/rookie_sop.yaml', encoding='utf-8'))
items = m.load_sop(cfg)
ids = [i.item_id for i in items]
assert len(ids) == len(set(ids)), f'重复 item_id: {[x for x in ids if ids.count(x)>1]}'
dev = [i for i in items if i.dev_only]
print(f'共 {len(ids)} 项, 其中仅研发 {len(dev)} 项, 全员 {len(ids)-len(dev)} 项')
"
```
Expected: 输出 `共 32 项, 其中仅研发 5 项, 全员 27 项`（模块分布：环境准备 5 / 认识人 4 /
协同体系 5 / 工作规范 6 / 核心制度 3 / 每周节奏 4 / 开发环境 5）

- [ ] **Step 7: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/config/rookie_sop.yaml \
        examples/haitun-workspace/tools/_rookie_sop_config.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): SOP 清单配置与解析"
```

---

### Task 2: 进度与逾期计算

**Files:**
- Create: `examples/haitun-workspace/tools/_rookie_sop_progress.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加测试，不改已有）

**Interfaces:**
- Consumes: Task 1 的 SOP 条目概念（但本模块只吃**明细行字典**，不 import config，保持解耦）
- Produces:
  - `STATUS_TODO = "未完成"` / `STATUS_DONE = "已完成"` / `STATUS_NA = "不适用"`
  - `summarize(rows: list[dict], today: date) -> Progress`；`Progress` dataclass 字段：
    `done: int`、`total: int`、`percent: int`、`overdue: list[dict]`、`due_today: list[dict]`、
    `next_due: dict | None`、`all_done: bool`
  - `overview_fields(rows: list[dict], today: date, name: str, open_id: str, role: str) -> dict[str, Any]`
    —— 返回可直接写入总览表的「列名→值」字典

**明细行字典的键**（与 Task 4 建表的列名一致）：`记录键` / `姓名` / `open_id` / `模块` / `项` /
`验收标准` / `状态` / `完成时间` / `入职日` / `截止日` / `Mentor` / `适用角色`。
日期在本模块统一用 `datetime.date`（Task 4 负责与飞书毫秒时间戳互转）。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def _row(item_id: str, status: str, due: date, title: str = "", module: str = "环境准备") -> dict[str, Any]:
    return {
        "记录键": f"ou_x:{item_id}",
        "姓名": "张三",
        "open_id": "ou_x",
        "模块": module,
        "项": title or item_id,
        "验收标准": "验收",
        "状态": status,
        "入职日": date(2026, 8, 5),
        "截止日": due,
    }


def test_summarize_counts_done_and_excludes_na_from_denominator() -> None:
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5)),
        _row("desk", p.STATUS_TODO, date(2026, 8, 5)),
        _row("git_workflow", p.STATUS_NA, date(2026, 8, 11), module="开发环境"),
    ]

    got = p.summarize(rows, date(2026, 8, 5))

    # 不适用的行既不进分子也不进分母
    assert (got.done, got.total) == (1, 2)
    assert got.percent == 50
    assert got.all_done is False


def test_summarize_splits_overdue_due_today_and_next_due() -> None:
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_TODO, date(2026, 8, 5)),         # 逾期
        _row("desk", p.STATUS_TODO, date(2026, 8, 7)),         # 今天到期
        _row("attendance", p.STATUS_TODO, date(2026, 8, 9)),   # 未来
        _row("todo_update", p.STATUS_DONE, date(2026, 8, 6)),  # 已完成, 不算逾期
    ]

    got = p.summarize(rows, date(2026, 8, 7))

    assert [r["项"] for r in got.overdue] == ["wifi"]
    assert [r["项"] for r in got.due_today] == ["desk"]
    assert got.next_due is not None and got.next_due["项"] == "attendance"


def test_summarize_all_done_ignores_na_rows() -> None:
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5)),
        _row("git_workflow", p.STATUS_NA, date(2026, 8, 11), module="开发环境"),
    ]

    got = p.summarize(rows, date(2026, 8, 6))

    assert got.all_done is True
    assert (got.done, got.total, got.percent) == (1, 1, 100)


def test_summarize_empty_rows_does_not_divide_by_zero() -> None:
    p = _load("_rookie_sop_progress")
    got = p.summarize([], date(2026, 8, 5))

    assert (got.done, got.total, got.percent) == (0, 0, 0)
    # 没有任何适用项时不能报「全部完成」, 否则会误发出新手村卡
    assert got.all_done is False


def test_overview_fields_projects_a_one_row_summary() -> None:
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5)),
        _row("desk", p.STATUS_TODO, date(2026, 8, 5)),
        _row("attendance", p.STATUS_TODO, date(2026, 8, 7)),
    ]

    got = p.overview_fields(rows, date(2026, 8, 7), "张三", "ou_x", "dev")

    assert got["open_id"] == "ou_x"
    assert got["姓名"] == "张三"
    assert got["角色"] == "研发"
    assert got["进度"] == "1/3"
    assert got["完成率"] == 33
    assert got["逾期项数"] == 1
    assert got["逾期项"] == "desk"
    assert got["状态"] == "进行中"
    assert got["入职第N天"] == 3


def test_overview_fields_marks_graduated_when_all_done() -> None:
    p = _load("_rookie_sop_progress")
    rows = [_row("wifi", p.STATUS_DONE, date(2026, 8, 5))]

    got = p.overview_fields(rows, date(2026, 8, 6), "张三", "ou_x", "nondev")

    assert got["状态"] == "已出新手村"
    assert got["角色"] == "非研发"
    assert got["逾期项数"] == 0
    assert got["逾期项"] == ""


def test_overview_fields_role_unset_shows_pending() -> None:
    p = _load("_rookie_sop_progress")
    rows = [_row("wifi", p.STATUS_TODO, date(2026, 8, 5))]

    got = p.overview_fields(rows, date(2026, 8, 5), "张三", "ou_x", "")

    assert got["角色"] == "待确认"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`_rookie_sop_progress.py` 不存在

- [ ] **Step 3: 写最小实现**

创建 `examples/haitun-workspace/tools/_rookie_sop_progress.py`：

```python
"""从明细行算进度、逾期、今日到期, 以及总览表的一行投影 —— 纯逻辑, 不碰飞书。

刻意为之: 总览行永远由本模块从明细整体重算, 不做增量加减。飞书 bitable 的
「查找引用」字段(type 19) API 建不出来、公式列也写不进去, 所以总览只能双写维护;
「整体重算」让任何一次漏写都会在下一次勾选时自愈, 不累积漂移。
"""

from __future__ import annotations

# ruff: noqa: RUF002  # 占位: 按 ruff 实际报出的码改, 一个都不报就删掉这行
from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_TODO = "未完成"
STATUS_DONE = "已完成"
STATUS_NA = "不适用"

ROLE_LABELS = {"dev": "研发", "nondev": "非研发", "": "待确认"}


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    percent: int = 0
    overdue: list[dict[str, Any]] = field(default_factory=list)
    due_today: list[dict[str, Any]] = field(default_factory=list)
    next_due: dict[str, Any] | None = None
    all_done: bool = False


def _due(row: dict[str, Any]) -> date | None:
    value = row.get("截止日")
    return value if isinstance(value, date) else None


def summarize(rows: list[dict[str, Any]], today: date) -> Progress:
    """分母只算「适用」的行 —— 不适用的既不进分子也不进分母。"""
    applicable = [r for r in rows if str(r.get("状态") or "") != STATUS_NA]
    done_rows = [r for r in applicable if str(r.get("状态") or "") == STATUS_DONE]
    todo_rows = [r for r in applicable if str(r.get("状态") or "") != STATUS_DONE]

    total = len(applicable)
    done = len(done_rows)
    percent = round(done * 100 / total) if total else 0

    overdue: list[dict[str, Any]] = []
    due_today: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    for row in todo_rows:
        due = _due(row)
        if due is None:
            future.append(row)
        elif due < today:
            overdue.append(row)
        elif due == today:
            due_today.append(row)
        else:
            future.append(row)

    future.sort(key=lambda r: (_due(r) or date.max))
    return Progress(
        done=done,
        total=total,
        percent=percent,
        overdue=overdue,
        due_today=due_today,
        next_due=future[0] if future else None,
        # 刻意为之: total==0 不算「全部完成」, 否则空清单会误发出新手村卡
        all_done=bool(total) and done == total,
    )


def overview_fields(
    rows: list[dict[str, Any]],
    today: date,
    name: str,
    open_id: str,
    role: str,
) -> dict[str, Any]:
    """总览表的一行 —— 纯投影, 删掉重建也不丢信息。"""
    progress = summarize(rows, today)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), None)
    return {
        "open_id": open_id,
        "姓名": name,
        "入职日": onboard,
        "入职第N天": (today - onboard).days + 1 if onboard else 0,
        "角色": ROLE_LABELS.get(role.strip().casefold(), "待确认"),
        "进度": f"{progress.done}/{progress.total}",
        "完成率": progress.percent,
        "逾期项数": len(progress.overdue),
        "逾期项": "、".join(str(r.get("项") or "") for r in progress.overdue),
        "状态": "已出新手村" if progress.all_done else "进行中",
        "最后更新": today,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/_rookie_sop_progress.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 进度与逾期计算, 总览行整体重算"
```

---

### Task 3: 卡片 JSON 组装

**Files:**
- Create: `examples/haitun-workspace/tools/_rookie_sop_card.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `Progress` / `STATUS_*`（import `_rookie_sop_progress`）
- Produces（每个函数都返回 `(card: dict, handlers: dict[str, str])`，handlers 即
  `action_handlers_json` 的内容）：
  - `module_card(module: str, rows: list[dict], progress_text: str, due_text: str, sop_url: str) -> tuple[dict, dict]`
  - `role_card(due_text: str) -> tuple[dict, dict]` —— 只有两个角色按钮
  - `role_settled_card(is_dev: bool, rows: list[dict], due_text: str, sop_url: str) -> tuple[dict, dict]`
    —— 选研发则展开 5 项（等价于 `module_card`），选非研发则终态无按钮
  - `remind_card(name: str, day_index: int, progress: Progress, sop_url: str) -> tuple[dict, dict]`
    （`progress` 是 Task 2 的 `Progress`；实现里标注为 `Any` 以免 `_rookie_sop_card` 为类型
    反向依赖 `_rookie_sop_progress` 的 dataclass 定义 —— 运行时仍只读它的属性）
  - `graduation_card(name: str, total: int) -> tuple[dict, dict]` —— 无 handler
  - `digest_card(overview_rows: list[dict], table_url: str, today_text: str) -> tuple[dict, dict]` —— 无 handler
  - 常量 `ACTION_TICK_PREFIX = "rookie_tick_"`、`ACTION_ROLE_DEV = "rookie_role_dev"`、
    `ACTION_ROLE_NONDEV = "rookie_role_nondev"`、`HANDLER_TICK = "rookie_sop_tick"`、
    `HANDLER_ROLE = "rookie_sop_role_set"`

**卡片外壳约定**（照 `tools/feishu_todo_card.py` 的 legacy 形态，勿改）：
`{"config": {"wide_screen_mode": True, "update_multi": True}, "header": {...}, "elements": [...]}`。
`update_multi` 必须为 true，否则卡片只对一个查看者更新。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def _dump(card: dict[str, Any]) -> str:
    import json

    return json.dumps(card, ensure_ascii=False)


def test_module_card_gives_each_unfinished_row_its_own_action() -> None:
    c = _load("_rookie_sop_card")
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5), title="连上 WiFi"),
        _row("desk", p.STATUS_TODO, date(2026, 8, 5), title="找到工位"),
    ]

    card, handlers = c.module_card("环境准备", rows, "1/2", "Day 1 截止", "https://sop.example/doc")

    # 每个未完成行一个唯一 action, 全部指向同一个 handler
    assert handlers == {"rookie_tick_desk": "rookie_sop_tick"}
    rendered = _dump(card)
    # 已完成行渲染成实心 + 删除线且不给按钮
    assert "~~连上 WiFi~~" in rendered
    assert "rookie_tick_wifi" not in rendered
    # 未完成行带验收标准与按钮
    assert "找到工位" in rendered
    assert "验收" in rendered
    assert card["config"]["update_multi"] is True


def test_module_card_all_done_turns_header_green() -> None:
    c = _load("_rookie_sop_card")
    p = _load("_rookie_sop_progress")
    rows = [_row("wifi", p.STATUS_DONE, date(2026, 8, 5))]

    card, handlers = c.module_card("环境准备", rows, "1/1", "Day 1 截止", "")

    assert handlers == {}
    assert card["header"]["template"] == "green"


def test_role_card_offers_exactly_two_choices() -> None:
    c = _load("_rookie_sop_card")

    card, handlers = c.role_card("Day 1-7 截止")

    assert handlers == {
        "rookie_role_dev": "rookie_sop_role_set",
        "rookie_role_nondev": "rookie_sop_role_set",
    }
    rendered = _dump(card)
    assert "我是研发" in rendered
    assert "我不是研发" in rendered


def test_role_settled_card_for_nondev_is_terminal_and_has_no_buttons() -> None:
    c = _load("_rookie_sop_card")

    card, handlers = c.role_settled_card(False, [], "Day 1-7 截止", "")

    assert handlers == {}
    rendered = _dump(card)
    assert "不适用" in rendered
    assert "rookie_tick_" not in rendered


def test_role_settled_card_for_dev_expands_the_five_items() -> None:
    c = _load("_rookie_sop_card")
    p = _load("_rookie_sop_progress")
    rows = [
        _row("git_workflow", p.STATUS_TODO, date(2026, 8, 11), title="Git 工作流", module="开发环境"),
        _row("repo_access", p.STATUS_TODO, date(2026, 8, 11), title="开通仓库权限", module="开发环境"),
    ]

    card, handlers = c.role_settled_card(True, rows, "Day 1-7 截止", "")

    assert handlers == {
        "rookie_tick_git_workflow": "rookie_sop_tick",
        "rookie_tick_repo_access": "rookie_sop_tick",
    }


def test_remind_card_sections_overdue_and_due_today() -> None:
    c = _load("_rookie_sop_card")
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_TODO, date(2026, 8, 5), title="连上 WiFi"),
        _row("desk", p.STATUS_TODO, date(2026, 8, 7), title="找到工位"),
        _row("attendance", p.STATUS_TODO, date(2026, 8, 9), title="了解考勤"),
    ]
    progress = p.summarize(rows, date(2026, 8, 7))

    card, handlers = c.remind_card("张三", 3, progress, "")

    rendered = _dump(card)
    assert "已逾期" in rendered
    assert "今天到期" in rendered
    assert "下一个到期" in rendered
    assert "入职第 3 天" in rendered
    # 催办卡也是 multi_use, 逾期与今日到期的行都能直接勾
    assert handlers == {
        "rookie_tick_wifi": "rookie_sop_tick",
        "rookie_tick_desk": "rookie_sop_tick",
    }


def test_digest_card_lists_one_line_per_rookie_and_links_the_table() -> None:
    c = _load("_rookie_sop_card")
    overview = [
        {"姓名": "张三", "进度": "17/18", "逾期项数": 0, "状态": "已出新手村", "入职第N天": 8, "逾期项": ""},
        {"姓名": "李四", "进度": "9/18", "逾期项数": 2, "状态": "进行中", "入职第N天": 5, "逾期项": "工位、校园卡"},
    ]

    card, handlers = c.digest_card(overview, "https://feishu.cn/base/bascnXXX", "8月5日")

    assert handlers == {}
    rendered = _dump(card)
    assert "张三" in rendered and "李四" in rendered
    assert "17/18" in rendered
    assert "工位、校园卡" in rendered
    assert "https://feishu.cn/base/bascnXXX" in rendered
    # 表格链接是普通跳转按钮, 不能是交互 action
    assert "rookie_" not in rendered


def test_graduation_card_has_no_actions() -> None:
    c = _load("_rookie_sop_card")

    card, handlers = c.graduation_card("张三", 18)

    assert handlers == {}
    assert "新手村" in _dump(card)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`_rookie_sop_card.py` 不存在

- [ ] **Step 3: 写最小实现**

创建 `examples/haitun-workspace/tools/_rookie_sop_card.py`：

```python
"""组各类入职卡片的 JSON —— 纯逻辑, 不发消息, 便于单测。

刻意为之: 建在 multi_use 卡之上, 每行一个独立 action。行的 action 名必须唯一且规范,
多选消费就是按它落墓碑的; 撞名或留空会让该行退回整卡去重(点一下整张卡就废)。
外壳沿用 tools/feishu_todo_card.py 的 legacy 形态: update_multi 必须为 true,
否则卡片只对一个查看者更新。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_progress as _p

ACTION_TICK_PREFIX = "rookie_tick_"
ACTION_ROLE_DEV = "rookie_role_dev"
ACTION_ROLE_NONDEV = "rookie_role_nondev"
HANDLER_TICK = "rookie_sop_tick"
HANDLER_ROLE = "rookie_sop_role_set"

_EMPTY = "□"
_FILLED = "■"


def _shell(title: str, elements: list[dict[str, Any]], template: str = "blue") -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": elements,
    }


def _item_id_of(row: dict[str, Any]) -> str:
    """明细行的 item_id 藏在 记录键 = "{open_id}:{item_id}" 的后半段。"""
    key = str(row.get("记录键") or "")
    return key.rsplit(":", 1)[-1] if ":" in key else key


def _row_elements(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """一行的展示 + 按钮; 返回 (elements, action 名或空串)。"""
    title = str(row.get("项") or "").strip()
    acceptance = str(row.get("验收标准") or "").strip()
    done = str(row.get("状态") or "") == _p.STATUS_DONE

    if done:
        lines = [f"{_FILLED} ~~{title}~~"]
        finished = row.get("完成时间")
        if finished is not None:
            lines[0] += f"　✅ {finished}"
        return [{"tag": "markdown", "content": "\n".join(lines)}], ""

    lines = [f"{_EMPTY} **{title}**"]
    if acceptance:
        lines.append(f"验收：{acceptance}")
    action = f"{ACTION_TICK_PREFIX}{_item_id_of(row)}"
    return (
        [
            {"tag": "markdown", "content": "\n".join(lines)},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "标记完成"},
                        "type": "default",
                        "value": {"action": action, "item_id": _item_id_of(row)},
                    }
                ],
            },
        ],
        action,
    )


def _rows_section(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    elements: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}
    for row in rows:
        elements.append({"tag": "hr"})
        row_elements, action = _row_elements(row)
        elements.extend(row_elements)
        if action:
            handlers[action] = HANDLER_TICK
    return elements, handlers


def _footer(sop_url: str) -> list[dict[str, Any]]:
    text = "遇到问题先问 Haitun"
    if sop_url.strip():
        text += f" · [查看完整 SOP]({sop_url.strip()})"
    return [{"tag": "hr"}, {"tag": "markdown", "content": text}]


def module_card(
    module: str,
    rows: list[dict[str, Any]],
    progress_text: str,
    due_text: str,
    sop_url: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": f"{due_text} · 进度 {progress_text}"}]
    rows_elements, handlers = _rows_section(rows)
    elements.extend(rows_elements)
    elements.extend(_footer(sop_url))
    template = "green" if not handlers and rows else "blue"
    return _shell(f"入职路线图 · {module}", elements, template), handlers


def role_card(due_text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """开发环境卡的第一段: 先确认角色, 再决定这部分要不要做。"""
    elements = [
        {"tag": "markdown", "content": f"{due_text}\n\n先确认你的角色，我们再决定这部分要不要做："},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "我是研发"},
                    "type": "primary",
                    "value": {"action": ACTION_ROLE_DEV, "role": "dev"},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "我不是研发"},
                    "type": "default",
                    "value": {"action": ACTION_ROLE_NONDEV, "role": "nondev"},
                },
            ],
        },
    ]
    return (
        _shell("入职路线图 · 开发环境", elements),
        {ACTION_ROLE_DEV: HANDLER_ROLE, ACTION_ROLE_NONDEV: HANDLER_ROLE},
    )


def role_settled_card(
    is_dev: bool,
    rows: list[dict[str, Any]],
    due_text: str,
    sop_url: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not is_dev:
        elements = [{"tag": "markdown", "content": "你选择了「非研发」，这部分不需要完成。"}]
        return _shell("入职路线图 · 开发环境 ✅ 不适用", elements, "grey"), {}
    return module_card(
        "开发环境",
        rows,
        f"{sum(1 for r in rows if str(r.get('状态') or '') == _p.STATUS_DONE)}/{len(rows)}",
        due_text,
        sop_url,
    )


def remind_card(
    name: str,
    day_index: int,
    progress: Any,
    sop_url: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    header = f"入职第 {day_index} 天 · 进度 {progress.done}/{progress.total}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": header}]
    handlers: dict[str, str] = {}

    for label, rows in (("⚠️ 已逾期", progress.overdue), ("📌 今天到期", progress.due_today)):
        if not rows:
            continue
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**{label} {len(rows)} 项**"})
        for row in rows:
            row_elements, action = _row_elements(row)
            elements.extend(row_elements)
            if action:
                handlers[action] = HANDLER_TICK

    if progress.next_due is not None:
        nxt = progress.next_due
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "markdown",
                "content": f"下一个到期：{nxt.get('模块') or ''}（{nxt.get('截止日')}）",
            }
        )
    elements.extend(_footer(sop_url))
    return _shell("入职提醒", elements, "orange" if progress.overdue else "blue"), handlers


def graduation_card(name: str, total: int) -> tuple[dict[str, Any], dict[str, str]]:
    elements = [
        {
            "tag": "markdown",
            "content": f"🎉 恭喜 {name}，{total} 项全部完成 —— 你出新手村了！",
        }
    ]
    return _shell("出新手村", elements, "green"), {}


def digest_card(
    overview_rows: list[dict[str, Any]],
    table_url: str,
    today_text: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """HR 日报 —— 只读: 表格链接是普通跳转按钮, 不注册任何 action。"""
    total = len(overview_rows)
    percents = [r.get("完成率") for r in overview_rows if isinstance(r.get("完成率"), int)]
    overall = round(sum(percents) / len(percents)) if percents else 0
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": f"{today_text}\n\n在途新人 {total} 人 · 整体完成率 {overall}%"}
    ]

    lines: list[str] = []
    attention: list[str] = []
    for row in overview_rows:
        name = str(row.get("姓名") or "")
        if str(row.get("状态") or "") == "已出新手村":
            icon = "✅"
            tail = "今日出新手村"
        elif int(row.get("逾期项数") or 0) > 0:
            icon = "⚠️"
            tail = f"逾期 {row.get('逾期项数')} 项（{row.get('逾期项')}）"
            attention.append(f"{name} 逾期 {row.get('逾期项数')} 项")
        else:
            icon = "🕐"
            tail = f"入职第 {row.get('入职第N天')} 天，正常"
        lines.append(f"{icon} **{name}**　{row.get('进度')}　{tail}")

    elements.append({"tag": "hr"})
    elements.append({"tag": "markdown", "content": "\n".join(lines) if lines else "今日无在途新人。"})
    if attention:
        elements.append({"tag": "markdown", "content": "**需要关注：** " + "；".join(attention)})
    else:
        elements.append({"tag": "markdown", "content": "全部正常，无需关注。"})

    if table_url.strip():
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看详情表格"},
                        "type": "primary",
                        "url": table_url.strip(),
                    }
                ],
            }
        )
    return _shell("新人入职进度日报", elements, "orange" if attention else "blue"), {}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 19 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/_rookie_sop_card.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 卡片组装 —— 模块卡/角色卡/催办卡/HR 日报"
```

---

### Task 4: bitable 读写与总览重算

**Files:**
- Create: `examples/haitun-workspace/tools/_rookie_sop_store.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 1 `_rookie_sop_config`、Task 2 `_rookie_sop_progress`
- Produces:
  - `load_config() -> dict` —— 读 `config/rookie_sop.yaml`（缺失/坏了返回 `{}`）
  - `DETAIL_FIELDS: list[dict]` / `OVERVIEW_FIELDS: list[dict]` —— 建表用的 `fields_json` 内容
  - `to_millis(value: date | None) -> int | None` / `from_millis(value: Any) -> date | None`
  - `detail_row_fields(item, *, open_id, name, onboard, role_label) -> dict` —— 单行待写字段
  - `async fetch_detail(bitable, app_token, detail_table_id, open_id) -> list[dict]`
  - `async recompute_overview(bitable, app_token, overview_table_id, *, open_id, name, role, rows, today) -> dict`
  - `async mark_done(bitable, app_token, detail_table_id, *, open_id, item_id, today) -> dict`
  - `async mark_module_na(bitable, app_token, detail_table_id, *, open_id, module, today) -> dict`

`bitable` 参数是一个**注入的适配器对象**，具备 `search_records` / `create_records` /
`update_records` 三个 async 方法，签名与 `tools/feishu_bitable.py` 的同名工具一致
（均返回 JSON 字符串）。这样单测可以传 fake，不需要飞书凭据。

**日期与飞书的转换**：bitable 日期列（type 5）收发的是**毫秒时间戳**。
本模块负责在 `date` 与毫秒之间转换，`_rookie_sop_progress` 只见 `date`。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
class _FakeBitable:
    """假的 bitable 适配器: 只记录调用并按预设返回, 不碰飞书。"""

    def __init__(self, search_results: list[list[dict[str, Any]]] | None = None) -> None:
        self._search_results = list(search_results or [])
        self.searches: list[dict[str, Any]] = []
        self.creates: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    async def search_records(
        self,
        app_token: str,
        table_id: str,
        filter_json: str = "",
        sort_json: str = "",
        field_names: str = "",
        view_id: str = "",
        page_size: int = 100,
        page_token: str = "",
        automatic_fields: bool = False,
        user_key: str = "",
    ) -> str:
        import json

        self.searches.append({"table_id": table_id, "filter_json": filter_json})
        items = self._search_results.pop(0) if self._search_results else []
        return json.dumps({"ok": True, "result": {"items": items, "has_more": False}}, ensure_ascii=False)

    async def create_records(
        self, app_token: str, table_id: str, records_json: str, user_key: str = "", identity: str = "", validate_fields: bool = True
    ) -> str:
        import json

        self.creates.append({"table_id": table_id, "records": json.loads(records_json)})
        return json.dumps({"ok": True}, ensure_ascii=False)

    async def update_records(
        self, app_token: str, table_id: str, records_json: str, user_key: str = "", identity: str = "", validate_fields: bool = True
    ) -> str:
        import json

        self.updates.append({"table_id": table_id, "records": json.loads(records_json)})
        return json.dumps({"ok": True}, ensure_ascii=False)


def _item(record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {"record_id": record_id, "fields": fields}


def test_millis_roundtrip_preserves_the_date() -> None:
    s = _load("_rookie_sop_store")
    assert s.from_millis(s.to_millis(date(2026, 8, 5))) == date(2026, 8, 5)
    assert s.to_millis(None) is None
    assert s.from_millis(None) is None
    assert s.from_millis("") is None


def test_detail_fields_put_a_text_key_column_first() -> None:
    s = _load("_rookie_sop_store")
    # 飞书要求索引列(第一列)是 1/2/5/13/15/20/22 之一 —— 这里必须是文本 1
    assert s.DETAIL_FIELDS[0]["field_name"] == "记录键"
    assert s.DETAIL_FIELDS[0]["type"] == 1
    assert s.OVERVIEW_FIELDS[0]["field_name"] == "open_id"
    assert s.OVERVIEW_FIELDS[0]["type"] == 1
    # 「查找引用」(19) API 建不出来, 不许出现
    assert all(f["type"] != 19 for f in s.DETAIL_FIELDS + s.OVERVIEW_FIELDS)


def test_fetch_detail_parses_rows_and_converts_dates() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    fake = _FakeBitable(
        [
            [
                _item(
                    "rec1",
                    {
                        "记录键": "ou_x:wifi",
                        "姓名": "张三",
                        "open_id": "ou_x",
                        "模块": "环境准备",
                        "项": "连上 WiFi",
                        "状态": p.STATUS_TODO,
                        "入职日": s.to_millis(date(2026, 8, 5)),
                        "截止日": s.to_millis(date(2026, 8, 5)),
                    },
                )
            ]
        ]
    )

    rows = anyio.run(s.fetch_detail, fake, "app1", "tblDetail", "ou_x")

    assert len(rows) == 1
    assert rows[0]["record_id"] == "rec1"
    assert rows[0]["入职日"] == date(2026, 8, 5)
    assert rows[0]["截止日"] == date(2026, 8, 5)
    # 按 open_id 过滤
    assert "ou_x" in fake.searches[0]["filter_json"]


def test_mark_done_updates_status_and_completion_time() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    fake = _FakeBitable([[_item("rec1", {"记录键": "ou_x:wifi", "状态": p.STATUS_TODO})]])

    out = anyio.run(
        lambda: s.mark_done(fake, "app1", "tblDetail", open_id="ou_x", item_id="wifi", today=date(2026, 8, 6))
    )

    assert out["ok"] is True
    assert fake.updates[0]["records"][0]["record_id"] == "rec1"
    fields = fake.updates[0]["records"][0]["fields"]
    assert fields["状态"] == p.STATUS_DONE
    assert fields["完成时间"] == s.to_millis(date(2026, 8, 6))


def test_mark_done_is_idempotent_when_already_done() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    fake = _FakeBitable([[_item("rec1", {"记录键": "ou_x:wifi", "状态": p.STATUS_DONE})]])

    out = anyio.run(
        lambda: s.mark_done(fake, "app1", "tblDetail", open_id="ou_x", item_id="wifi", today=date(2026, 8, 6))
    )

    assert out["ok"] is True
    assert out["already_done"] is True
    # 已完成就不再写一次
    assert fake.updates == []


def test_mark_module_na_marks_every_row_of_that_module() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    fake = _FakeBitable(
        [
            [
                _item("recA", {"记录键": "ou_x:git_workflow", "模块": "开发环境", "状态": p.STATUS_TODO}),
                _item("recB", {"记录键": "ou_x:repo_access", "模块": "开发环境", "状态": p.STATUS_TODO}),
            ]
        ]
    )

    out = anyio.run(
        lambda: s.mark_module_na(fake, "app1", "tblDetail", open_id="ou_x", module="开发环境", today=date(2026, 8, 6))
    )

    assert out["ok"] is True and out["marked"] == 2
    updated = fake.updates[0]["records"]
    assert {r["record_id"] for r in updated} == {"recA", "recB"}
    assert all(r["fields"]["状态"] == p.STATUS_NA for r in updated)


def test_recompute_overview_updates_the_existing_row_instead_of_adding_one() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    detail = [
        {"记录键": "ou_x:wifi", "状态": p.STATUS_DONE, "入职日": date(2026, 8, 5), "截止日": date(2026, 8, 5), "项": "wifi"},
        {"记录键": "ou_x:desk", "状态": p.STATUS_TODO, "入职日": date(2026, 8, 5), "截止日": date(2026, 8, 5), "项": "desk"},
    ]
    # 总览里已有该人一行 → 走 update 而不是 create
    fake = _FakeBitable([[_item("recOv", {"open_id": "ou_x"})]])

    out = anyio.run(
        lambda: s.recompute_overview(
            fake, "app1", "tblOverview", open_id="ou_x", name="张三", role="dev", rows=detail, today=date(2026, 8, 7)
        )
    )

    assert out["ok"] is True
    assert fake.creates == []
    fields = fake.updates[0]["records"][0]["fields"]
    assert fields["进度"] == "1/2"
    assert fields["逾期项数"] == 1
    assert fields["入职日"] == s.to_millis(date(2026, 8, 5))


def test_recompute_overview_creates_the_row_when_absent() -> None:
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    detail = [
        {"记录键": "ou_x:wifi", "状态": p.STATUS_TODO, "入职日": date(2026, 8, 5), "截止日": date(2026, 8, 5), "项": "wifi"}
    ]
    fake = _FakeBitable([[]])  # 总览里还没有这一行

    out = anyio.run(
        lambda: s.recompute_overview(
            fake, "app1", "tblOverview", open_id="ou_x", name="张三", role="", rows=detail, today=date(2026, 8, 5)
        )
    )

    assert out["ok"] is True
    assert fake.updates == []
    assert fake.creates[0]["records"][0]["fields"]["open_id"] == "ou_x"


def test_recompute_overview_heals_a_corrupted_row() -> None:
    """人为改坏总览行后, 下一次重算必须把它算回正确值(验证「重算而非增量」)。"""
    import anyio

    s = _load("_rookie_sop_store")
    p = _load("_rookie_sop_progress")
    detail = [
        {"记录键": "ou_x:a", "状态": p.STATUS_DONE, "入职日": date(2026, 8, 5), "截止日": date(2026, 8, 5), "项": "a"},
        {"记录键": "ou_x:b", "状态": p.STATUS_DONE, "入职日": date(2026, 8, 5), "截止日": date(2026, 8, 5), "项": "b"},
    ]
    # 总览行被改成了荒谬的值
    fake = _FakeBitable([[_item("recOv", {"open_id": "ou_x", "进度": "99/99", "逾期项数": 42})]])

    anyio.run(
        lambda: s.recompute_overview(
            fake, "app1", "tblOverview", open_id="ou_x", name="张三", role="nondev", rows=detail, today=date(2026, 8, 6)
        )
    )

    fields = fake.updates[0]["records"][0]["fields"]
    assert fields["进度"] == "2/2"
    assert fields["逾期项数"] == 0
    assert fields["状态"] == "已出新手村"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`_rookie_sop_store.py` 不存在

- [ ] **Step 3: 写最小实现**

创建 `examples/haitun-workspace/tools/_rookie_sop_store.py`：

```python
"""明细表与总览表的读写 —— 唯一碰飞书表格的模块。

刻意为之: bitable 操作通过注入的适配器对象调用(具备 search_records /
create_records / update_records 三个 async 方法), 这样单测传 fake 就能跑,
不需要飞书凭据。日期列(type 5)收发的是毫秒时间戳, 转换只发生在本模块,
上层只见 datetime.date。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _runtime_paths as _paths
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p

_CONFIG_PATH = "config/rookie_sop.yaml"

# 飞书字段类型: 1 文本, 2 数字, 3 单选, 5 日期。19(查找引用) API 建不出来。
# 第一列是索引列, 必须是 1/2/5/13/15/20/22 之一 —— 两张表都用文本键列打头。
DETAIL_FIELDS: list[dict[str, Any]] = [
    {"field_name": "记录键", "type": 1},
    {"field_name": "姓名", "type": 1},
    {"field_name": "open_id", "type": 1},
    {"field_name": "模块", "type": 1},
    {"field_name": "项", "type": 1},
    {"field_name": "验收标准", "type": 1},
    {
        "field_name": "状态",
        "type": 3,
        "property": {
            "options": [
                {"name": _p.STATUS_TODO, "color": 1},
                {"name": _p.STATUS_DONE, "color": 0},
                {"name": _p.STATUS_NA, "color": 2},
            ]
        },
    },
    {"field_name": "完成时间", "type": 5},
    {"field_name": "入职日", "type": 5},
    {"field_name": "截止日", "type": 5},
    {"field_name": "Mentor", "type": 1},
    {"field_name": "适用角色", "type": 1},
]

OVERVIEW_FIELDS: list[dict[str, Any]] = [
    {"field_name": "open_id", "type": 1},
    {"field_name": "姓名", "type": 1},
    {"field_name": "入职日", "type": 5},
    {"field_name": "入职第N天", "type": 2},
    {"field_name": "角色", "type": 1},
    {"field_name": "进度", "type": 1},
    {"field_name": "完成率", "type": 2},
    {"field_name": "逾期项数", "type": 2},
    {"field_name": "逾期项", "type": 1},
    {"field_name": "状态", "type": 1},
    {"field_name": "最后更新", "type": 5},
]

_DATE_KEYS = ("完成时间", "入职日", "截止日", "最后更新")


async def load_config() -> dict[str, Any]:
    path = _paths.resolve_agent() / _CONFIG_PATH
    try:
        text = await path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def to_millis(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time()).timestamp() * 1000)


def from_millis(value: Any) -> date | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000).date()


def detail_row_fields(
    item: _cfg.SopItem,
    *,
    open_id: str,
    name: str,
    onboard: date,
    role_label: str = "",
) -> dict[str, Any]:
    return {
        "记录键": f"{open_id}:{item.item_id}",
        "姓名": name,
        "open_id": open_id,
        "模块": item.module,
        "项": item.title,
        "验收标准": item.acceptance,
        "状态": _p.STATUS_TODO,
        "入职日": to_millis(onboard),
        "截止日": to_millis(_cfg.due_date(onboard, item.window_days)),
        "Mentor": "",
        "适用角色": role_label or ("仅研发" if item.dev_only else "全员"),
    }


def _parse_result(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _items_of(raw: str) -> list[dict[str, Any]]:
    payload = _parse_result(raw)
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _row_of(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields")
    row: dict[str, Any] = dict(fields) if isinstance(fields, dict) else {}
    row["record_id"] = str(item.get("record_id") or "")
    for key in _DATE_KEYS:
        if key in row:
            row[key] = from_millis(row[key])
    return row


def _eq_filter(field_name: str, value: str) -> str:
    return json.dumps(
        {"conjunction": "and", "conditions": [{"field_name": field_name, "operator": "is", "value": [value]}]},
        ensure_ascii=False,
    )


async def fetch_detail(bitable: Any, app_token: str, detail_table_id: str, open_id: str) -> list[dict[str, Any]]:
    raw = await bitable.search_records(app_token, detail_table_id, _eq_filter("open_id", open_id), page_size=500)
    return [_row_of(i) for i in _items_of(raw)]


async def mark_done(
    bitable: Any,
    app_token: str,
    detail_table_id: str,
    *,
    open_id: str,
    item_id: str,
    today: date,
) -> dict[str, Any]:
    key = f"{open_id}:{item_id}"
    raw = await bitable.search_records(app_token, detail_table_id, _eq_filter("记录键", key), page_size=2)
    rows = [_row_of(i) for i in _items_of(raw)]
    if not rows:
        return {"ok": False, "error": f"detail row not found for {key}"}
    row = rows[0]
    if str(row.get("状态") or "") == _p.STATUS_DONE:
        return {"ok": True, "already_done": True, "record_id": row["record_id"]}
    await bitable.update_records(
        app_token,
        detail_table_id,
        json.dumps(
            [{"record_id": row["record_id"], "fields": {"状态": _p.STATUS_DONE, "完成时间": to_millis(today)}}],
            ensure_ascii=False,
        ),
    )
    return {"ok": True, "already_done": False, "record_id": row["record_id"]}


async def mark_module_na(
    bitable: Any,
    app_token: str,
    detail_table_id: str,
    *,
    open_id: str,
    module: str,
    today: date,
) -> dict[str, Any]:
    rows = await fetch_detail(bitable, app_token, detail_table_id, open_id)
    targets = [r for r in rows if str(r.get("模块") or "") == module and str(r.get("状态") or "") != _p.STATUS_DONE]
    if not targets:
        return {"ok": True, "marked": 0}
    await bitable.update_records(
        app_token,
        detail_table_id,
        json.dumps(
            [{"record_id": r["record_id"], "fields": {"状态": _p.STATUS_NA}} for r in targets],
            ensure_ascii=False,
        ),
    )
    return {"ok": True, "marked": len(targets)}


async def recompute_overview(
    bitable: Any,
    app_token: str,
    overview_table_id: str,
    *,
    open_id: str,
    name: str,
    role: str,
    rows: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """从明细整体重算总览行 —— 不做增量, 所以任何漏写都会在下一次调用时自愈。"""
    fields = _p.overview_fields(rows, today, name, open_id, role)
    for key in _DATE_KEYS:
        if key in fields:
            fields[key] = to_millis(fields[key])

    raw = await bitable.search_records(app_token, overview_table_id, _eq_filter("open_id", open_id), page_size=2)
    existing = _items_of(raw)
    if existing:
        record_id = str(existing[0].get("record_id") or "")
        await bitable.update_records(
            app_token,
            overview_table_id,
            json.dumps([{"record_id": record_id, "fields": fields}], ensure_ascii=False),
        )
        return {"ok": True, "created": False, "record_id": record_id, "fields": fields}

    await bitable.create_records(app_token, overview_table_id, json.dumps([{"fields": fields}], ensure_ascii=False))
    return {"ok": True, "created": True, "fields": fields}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 28 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/_rookie_sop_store.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): bitable 读写与总览整体重算"
```

---

### Task 5: 建表与发卡入口

**Files:**
- Create: `examples/haitun-workspace/tools/_rookie_sop_runtime.py`
- Create: `examples/haitun-workspace/tools/rookie_sop_card_send.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 1-4 全部
- Produces:
  - `_rookie_sop_runtime.bitable_adapter() -> Any` —— 把真实 `feishu_bitable_*` 工具包成
    Task 4 期望的适配器（`search_records` / `create_records` / `update_records`）
  - `_rookie_sop_runtime.ensure_base(bitable_api, cfg) -> dict` —— 首次运行时建 base + 两张表，
    把 `app_token` / `detail_table_id` / `overview_table_id` 存进 workspace 状态文件
    `.psi/rookie_sop/base.json`，之后直接复用
  - `_rookie_sop_runtime.load_state() -> dict` / `save_state(state: dict) -> None`
  - `_rookie_sop_runtime.plan_module_cards(items, rows, onboard, today, sop_url) -> list[dict]`
    —— 返回 `[{"module": str, "card": dict, "handlers": dict, "is_role_card": bool}]`，
    开发环境模块产出角色卡，其余模块产出普通模块卡
  - `rookie_sop_card_send(open_id="", name="", event_payload_json="", onboard_date="") -> str`

**状态文件为什么需要**：`app_token` 与两个 `table_id` 是运行时才有的值，不能写进 yaml。
放 workspace 的 `.psi/rookie_sop/base.json`，与 `feishu_auth` 把 token 放
`.psi/feishu/uat.json` 同一惯例。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def test_plan_module_cards_makes_the_dev_module_a_role_card() -> None:
    r = _load("_rookie_sop_runtime")
    cfg = _load("_rookie_sop_config")
    p = _load("_rookie_sop_progress")
    items = cfg.load_sop(_CFG)
    rows = [
        {
            "记录键": f"ou_x:{i.item_id}",
            "项": i.title,
            "验收标准": i.acceptance,
            "模块": i.module,
            "状态": p.STATUS_TODO,
            "入职日": date(2026, 8, 5),
            "截止日": cfg.due_date(date(2026, 8, 5), i.window_days),
        }
        for i in items
    ]

    plans = r.plan_module_cards(items, rows, date(2026, 8, 5), date(2026, 8, 5), "")

    by_module = {p_["module"]: p_ for p_ in plans}
    # 开发环境先问角色, 不直接列 5 项
    assert by_module["开发环境"]["is_role_card"] is True
    assert by_module["开发环境"]["handlers"] == {
        "rookie_role_dev": "rookie_sop_role_set",
        "rookie_role_nondev": "rookie_sop_role_set",
    }
    # 其余模块直接给勾选行
    assert by_module["环境准备"]["is_role_card"] is False
    assert "rookie_tick_wifi" in by_module["环境准备"]["handlers"]


def test_plan_module_cards_keeps_each_card_within_the_forty_row_cap() -> None:
    r = _load("_rookie_sop_runtime")
    cfg = _load("_rookie_sop_config")
    items = cfg.load_sop(_CFG)
    rows = [
        {
            "记录键": f"ou_x:{i.item_id}",
            "项": i.title,
            "模块": i.module,
            "状态": "未完成",
            "入职日": date(2026, 8, 5),
            "截止日": date(2026, 8, 5),
        }
        for i in items
    ]

    for plan in r.plan_module_cards(items, rows, date(2026, 8, 5), date(2026, 8, 5), ""):
        assert len(plan["handlers"]) <= 40
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`_rookie_sop_runtime.py` 不存在

- [ ] **Step 3: 写 runtime 模块**

创建 `examples/haitun-workspace/tools/_rookie_sop_runtime.py`：

```python
"""运行时接线: bitable 适配器、base/表的一次性创建、状态文件、卡片编排。

刻意为之: app_token 与两个 table_id 是运行时才有的值, 不能写进 yaml, 所以存
workspace 的 .psi/rookie_sop/base.json (与 feishu_auth 把 token 放
.psi/feishu/uat.json 同一惯例)。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _runtime_paths as _paths
import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _rookie_sop_store as _store

_STATE_REL = ".psi/rookie_sop/base.json"
_DEV_MODULE = "开发环境"
_MAX_ROWS_PER_CARD = 40


def bitable_adapter() -> Any:
    """把真实 feishu_bitable_* 工具包成 store 期望的适配器。"""
    import feishu_bitable as _bt

    class _Adapter:
        search_records = staticmethod(_bt.feishu_bitable_search_records)
        create_records = staticmethod(_bt.feishu_bitable_create_records)
        update_records = staticmethod(_bt.feishu_bitable_update_records)

    return _Adapter()


async def load_state() -> dict[str, Any]:
    path = _paths.resolve_workspace() / _STATE_REL
    try:
        text = await path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def save_state(state: dict[str, Any]) -> None:
    path = _paths.resolve_workspace() / _STATE_REL
    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _module_window(items: list[_cfg.SopItem], module: str) -> int:
    return next((i.window_days for i in items if i.module == module), 1)


def _due_text(onboard: date, window_days: int) -> str:
    if window_days <= 1:
        return "Day 1 截止"
    return f"Day 1-{window_days} 截止（{_cfg.due_date(onboard, window_days)}）"


def plan_module_cards(
    items: list[_cfg.SopItem],
    rows: list[dict[str, Any]],
    onboard: date,
    today: date,
    sop_url: str,
) -> list[dict[str, Any]]:
    """按模块编排要发的卡。开发环境先问角色, 其余直接列勾选行。"""
    modules: list[str] = []
    for item in items:
        if item.module not in modules:
            modules.append(item.module)

    plans: list[dict[str, Any]] = []
    for module in modules:
        window = _module_window(items, module)
        due_text = _due_text(onboard, window)
        if module == _DEV_MODULE:
            card, handlers = _card.role_card(due_text)
            plans.append({"module": module, "card": card, "handlers": handlers, "is_role_card": True})
            continue
        module_rows = [r for r in rows if str(r.get("模块") or "") == module][:_MAX_ROWS_PER_CARD]
        done = sum(1 for r in module_rows if str(r.get("状态") or "") == _p.STATUS_DONE)
        card, handlers = _card.module_card(
            module, module_rows, f"{done}/{len(module_rows)}", due_text, sop_url
        )
        plans.append({"module": module, "card": card, "handlers": handlers, "is_role_card": False})
    return plans


async def ensure_base(bitable_api: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """首次运行时建 base + 明细表 + 总览表; 之后复用状态文件里的 id。"""
    state = await load_state()
    if state.get("app_token") and state.get("detail_table_id") and state.get("overview_table_id"):
        return state

    import feishu_api as _api
    import feishu_bitable as _bt

    company = str(cfg.get("company_name") or "").strip() or "团队"
    created = _store._parse_result(
        await _api.feishu_api(
            "POST",
            "/open-apis/bitable/v1/apps",
            body_json=json.dumps({"name": f"{company}新人入职进度"}, ensure_ascii=False),
        )
    )
    app_token = str(((created.get("result") or {}).get("app") or {}).get("app_token") or "")
    if not app_token:
        return {"ok": False, "error": f"cannot create bitable base: {created}"}

    detail = _store._parse_result(
        await _bt.feishu_bitable_create_table(
            app_token, "入职明细", json.dumps(_store.DETAIL_FIELDS, ensure_ascii=False)
        )
    )
    overview = _store._parse_result(
        await _bt.feishu_bitable_create_table(
            app_token, "入职总览", json.dumps(_store.OVERVIEW_FIELDS, ensure_ascii=False)
        )
    )
    state = {
        "app_token": app_token,
        "detail_table_id": str((detail.get("result") or {}).get("table_id") or ""),
        "overview_table_id": str((overview.get("result") or {}).get("table_id") or ""),
        "table_url": f"https://feishu.cn/base/{app_token}",
    }
    await save_state(state)
    return state
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 30 passed

- [ ] **Step 5: 写入口工具**

创建 `examples/haitun-workspace/tools/rookie_sop_card_send.py`：

```python
"""新人入职: 建明细/总览行 + 发全部模块卡 + 建每日催办定时任务。

由 feishu.hr.user_created 触发器 fire=tool 调用(Session 注入 event_payload_json),
也可手动传 open_id 联调。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_config as _cfg
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_send_card
from schedule_manage import schedule_manage


async def rookie_sop_card_send(
    open_id: str = "",
    name: str = "",
    event_payload_json: str = "",
    onboard_date: str = "",
) -> str:
    """Send a new hire the full onboarding SOP as per-module tickable cards.

    Prefer calling with empty ``open_id``/``name`` from a ``feishu.hr.user_created``
    trigger — Session injects ``event_payload_json``. Idempotent: re-running for the
    same person reuses the existing detail rows instead of duplicating them.

    Args:
        open_id: New hire Feishu open_id (ou_...). Empty → read from event_payload_json.
        name: Display name. Empty → from payload, else the open_id.
        event_payload_json: The event envelope payload (injected by Session).
        onboard_date: 'YYYY-MM-DD'; empty means today.
    """
    payload = _store._parse_result(event_payload_json) if event_payload_json else {}
    resolved_open_id = (open_id or "").strip() or str(payload.get("open_id") or "").strip()
    resolved_name = (name or "").strip() or str(payload.get("name") or "").strip() or resolved_open_id
    if not resolved_open_id:
        return json.dumps({"ok": False, "error": "open_id is required"}, ensure_ascii=False)

    try:
        onboard = datetime.strptime(onboard_date.strip(), "%Y-%m-%d").date() if onboard_date.strip() else date.today()
    except ValueError:
        return json.dumps({"ok": False, "error": f"invalid onboard_date {onboard_date!r}"}, ensure_ascii=False)

    cfg = await _store.load_config()
    items = _cfg.load_sop(cfg)
    if not items:
        return json.dumps({"ok": False, "error": "config/rookie_sop.yaml has no items"}, ensure_ascii=False)

    state = await _rt.ensure_base(None, cfg)
    if not state.get("app_token"):
        return json.dumps({"ok": False, "error": f"bitable base unavailable: {state}"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    app_token = str(state["app_token"])
    detail_table = str(state["detail_table_id"])
    overview_table = str(state["overview_table_id"])

    # 幂等: 已有明细行就不再建, 免得重复入职事件写出两套
    rows = await _store.fetch_detail(bitable, app_token, detail_table, resolved_open_id)
    if not rows:
        await bitable.create_records(
            app_token,
            detail_table,
            json.dumps(
                [
                    _store.detail_row_fields(i, open_id=resolved_open_id, name=resolved_name, onboard=onboard)
                    for i in items
                ],
                ensure_ascii=False,
            ),
        )
        rows = await _store.fetch_detail(bitable, app_token, detail_table, resolved_open_id)

    today = date.today()
    await _store.recompute_overview(
        bitable,
        app_token,
        overview_table,
        open_id=resolved_open_id,
        name=resolved_name,
        role="",
        rows=rows,
        today=today,
    )

    sop_url = str(cfg.get("sop_doc_url") or "")
    sent: list[str] = []
    for plan in _rt.plan_module_cards(items, rows, onboard, today, sop_url):
        business = {
            "type": "rookie_sop",
            "open_id": resolved_open_id,
            "name": resolved_name,
            "module": plan["module"],
            "app_token": app_token,
            "detail_table_id": detail_table,
            "overview_table_id": overview_table,
        }
        await feishu_message_send_card(
            resolved_open_id,
            json.dumps(plan["card"], ensure_ascii=False),
            "open_id",
            "",
            json.dumps(business, ensure_ascii=False),
            json.dumps(plan["handlers"], ensure_ascii=False),
            True,
        )
        sent.append(plan["module"])

    # 每人一份催办定时任务, 落在这个新人自己的 Session workspace 里
    await schedule_manage(
        action="create",
        schedule_name=f"rookie-remind-{resolved_open_id[-8:]}",
        cron="30 9 * * *",
        fire="tool",
        tool="rookie_sop_remind",
        tool_args=json.dumps({"open_id": resolved_open_id}, ensure_ascii=False),
        visibility="silent",
        description=f"{resolved_name} 入职 SOP 每日催办",
    )

    return json.dumps(
        {"ok": True, "open_id": resolved_open_id, "items": len(items), "cards_sent": sent},
        ensure_ascii=False,
    )
```

- [ ] **Step 6: 校验模块能被导入（不跑真实飞书）**

Run:
```bash
cd /public/home/wwb/Dolphin-Agent/examples/haitun-workspace && ../../.venv/bin/python -c "
import sys; sys.path.insert(0, 'tools')
import ast
for f in ('tools/rookie_sop_card_send.py', 'tools/_rookie_sop_runtime.py'):
    ast.parse(open(f, encoding='utf-8').read())
    print(f, 'syntax OK')
"
```
Expected: 两行 `syntax OK`

- [ ] **Step 7: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/_rookie_sop_runtime.py \
        examples/haitun-workspace/tools/rookie_sop_card_send.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 建表与发卡入口, 每人一份催办定时"
```

---

### Task 6: 勾选回调

**Files:**
- Create: `examples/haitun-workspace/tools/rookie_sop_tick.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 4 `mark_done` / `recompute_overview` / `fetch_detail`；Task 5 `bitable_adapter` / `load_state`
- Produces:
  - `rookie_sop_tick(card_action_json: str = "") -> str`
  - `_resolve_context(payload: dict) -> dict` —— 从回调里取 `open_id` / `name` / `item_id` /
    三个表 id；`business_context` 优先，缺了再退回 `action.value` 与 `source.operator_open_id`

**为什么状态不从卡片快照读**：勾选后的原地重绘由框架完成，本工具只负责把状态落到明细表并重算总览。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def _callback(item_id: str, *, open_id: str = "ou_x", with_business: bool = True) -> str:
    import json

    payload: dict[str, Any] = {
        "action": {"value": {"action": f"rookie_tick_{item_id}", "item_id": item_id}},
        "source": {"operator_open_id": open_id},
        "dispatch": {"handler": "rookie_sop_tick", "matched": True},
    }
    if with_business:
        payload["business_context"] = {
            "type": "rookie_sop",
            "open_id": open_id,
            "name": "张三",
            "module": "环境准备",
            "app_token": "app1",
            "detail_table_id": "tblDetail",
            "overview_table_id": "tblOverview",
        }
    return json.dumps(payload, ensure_ascii=False)


def test_resolve_context_prefers_business_context() -> None:
    t = _load("rookie_sop_tick")
    import json

    got = t._resolve_context(json.loads(_callback("wifi")))

    assert got["open_id"] == "ou_x"
    assert got["item_id"] == "wifi"
    assert got["detail_table_id"] == "tblDetail"


def test_resolve_context_falls_back_to_operator_and_action_value() -> None:
    t = _load("rookie_sop_tick")
    import json

    got = t._resolve_context(json.loads(_callback("desk", with_business=False)))

    # business_context 缺失时仍能拿到点击者与 item_id
    assert got["open_id"] == "ou_x"
    assert got["item_id"] == "desk"
    # 表 id 只能来自 business_context 或状态文件, 这里留空由工具兜底
    assert got["detail_table_id"] == ""


def test_resolve_context_rejects_a_wrong_handler() -> None:
    t = _load("rookie_sop_tick")
    import json

    payload = json.loads(_callback("wifi"))
    payload["dispatch"] = {"handler": "something_else", "matched": True}

    got = t._resolve_context(payload)

    assert got["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`rookie_sop_tick.py` 不存在

- [ ] **Step 3: 写实现**

创建 `examples/haitun-workspace/tools/rookie_sop_tick.py`：

```python
"""勾选一条 SOP 项: 写明细完成状态, 再从明细整体重算该人的总览行。

卡片的原地重绘由框架完成, 本工具不发卡、不改卡。连点会被合并成
<feishu_card_action_batch>, 里面每条都要各调一次本工具(漏掉就丢一项完成)。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store

_HANDLER = "rookie_sop_tick"


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _resolve_context(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch = _as_dict(payload.get("dispatch"))
    handler = str(dispatch.get("handler") or "").strip()
    if handler and handler != _HANDLER:
        return {"error": f"unexpected handler {handler!r}; expected {_HANDLER!r}"}
    if handler == _HANDLER and dispatch.get("matched") is False:
        return {"error": "dispatch.matched is false; do not invent a handler"}

    action = _as_dict(payload.get("action"))
    value = action.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    value = _as_dict(value)

    business = _as_dict(payload.get("business_context"))
    source = _as_dict(payload.get("source"))
    operator = str(source.get("operator_open_id") or source.get("open_id") or "").strip()

    item_id = str(value.get("item_id") or "").strip()
    if not item_id:
        action_name = str(value.get("action") or action.get("action_id") or "")
        if action_name.startswith("rookie_tick_"):
            item_id = action_name[len("rookie_tick_") :]

    return {
        "error": "",
        "open_id": str(business.get("open_id") or "").strip() or operator,
        "name": str(business.get("name") or "").strip(),
        "item_id": item_id,
        "app_token": str(business.get("app_token") or "").strip(),
        "detail_table_id": str(business.get("detail_table_id") or "").strip(),
        "overview_table_id": str(business.get("overview_table_id") or "").strip(),
    }


async def rookie_sop_tick(card_action_json: str = "") -> str:
    """Record one ticked onboarding SOP item, then recompute that person's overview row.

    Call this for a ``<feishu_card_action>`` whose ``dispatch.handler`` is
    ``rookie_sop_tick``. Pass the **entire** JSON object inside the tag. The card has
    already been redrawn by the framework — do not re-send it, do not narrate the click.
    Finish with zero assistant content unless this tool reports an error.

    If the payload arrived wrapped in ``<feishu_card_action_batch>``, call this once per
    ``<feishu_card_action>`` inside it (skipping one silently loses that item), then send
    at most one short summary for the whole batch.

    Args:
        card_action_json: Full ``<feishu_card_action>`` payload JSON string.
    """
    payload = _store._parse_result(card_action_json)
    if not payload:
        return json.dumps({"ok": False, "error": "card_action_json must be a JSON object"}, ensure_ascii=False)

    ctx = _resolve_context(payload)
    if ctx.get("error"):
        return json.dumps({"ok": False, "error": ctx["error"]}, ensure_ascii=False)
    if not ctx["open_id"] or not ctx["item_id"]:
        return json.dumps({"ok": False, "error": "cannot resolve open_id / item_id"}, ensure_ascii=False)

    state = await _rt.load_state()
    app_token = ctx["app_token"] or str(state.get("app_token") or "")
    detail_table = ctx["detail_table_id"] or str(state.get("detail_table_id") or "")
    overview_table = ctx["overview_table_id"] or str(state.get("overview_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()
    marked = await _store.mark_done(
        bitable, app_token, detail_table, open_id=ctx["open_id"], item_id=ctx["item_id"], today=today
    )
    if marked.get("ok") is not True:
        return json.dumps({"ok": False, "error": marked.get("error") or "mark_done failed"}, ensure_ascii=False)

    rows = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    role = ""
    for row in rows:
        label = str(row.get("适用角色") or "")
        if label in {"研发", "非研发"}:
            role = "dev" if label == "研发" else "nondev"
            break

    overview = {}
    if overview_table:
        overview = await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=ctx["open_id"],
            name=ctx["name"] or ctx["open_id"],
            role=role,
            rows=rows,
            today=today,
        )

    result: dict[str, Any] = {
        "ok": True,
        "item_id": ctx["item_id"],
        "already_done": bool(marked.get("already_done")),
        "overview_updated": bool(overview.get("ok")),
    }
    # 明细表出现同一 记录键 的重复行是数据完整性问题: mark_done 只会勾掉第一行,
    # 孪生行会永远停在「未完成」而无人知晓, 总览的分母也就一直是错的。
    # 所以 duplicates 必须往上报, 但只在真的 >0 时出现, 免得平常噪声化。
    if int(marked.get("duplicates") or 0) > 0:
        result["duplicates"] = int(marked["duplicates"])
    return json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 33 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/rookie_sop_tick.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 勾选回调 —— 写明细并重算总览"
```

---

### Task 7: 角色选择回调

**Files:**
- Create: `examples/haitun-workspace/tools/rookie_sop_role_set.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 4 `mark_module_na` / `fetch_detail` / `recompute_overview`；Task 3 `role_settled_card`
- Produces: `rookie_sop_role_set(card_action_json: str = "") -> str`

**行为**：选「非研发」→ 开发环境模块全标 `不适用`，发一张终态卡；
选「研发」→ 发一张展开 5 项的新卡（**新卡**，因为原卡的角色按钮已被消费，
不能靠 `edit_card` 复活按钮）。两种都重算总览。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def _role_callback(role: str, *, open_id: str = "ou_x") -> str:
    import json

    action = "rookie_role_dev" if role == "dev" else "rookie_role_nondev"
    return json.dumps(
        {
            "action": {"value": {"action": action, "role": role}},
            "source": {"operator_open_id": open_id},
            "dispatch": {"handler": "rookie_sop_role_set", "matched": True},
            "business_context": {
                "type": "rookie_sop",
                "open_id": open_id,
                "name": "张三",
                "module": "开发环境",
                "app_token": "app1",
                "detail_table_id": "tblDetail",
                "overview_table_id": "tblOverview",
            },
        },
        ensure_ascii=False,
    )


def test_role_context_reads_dev_and_nondev() -> None:
    rs = _load("rookie_sop_role_set")
    import json

    assert rs._resolve_role(json.loads(_role_callback("dev")))["role"] == "dev"
    assert rs._resolve_role(json.loads(_role_callback("nondev")))["role"] == "nondev"


def test_role_context_rejects_wrong_handler() -> None:
    rs = _load("rookie_sop_role_set")
    import json

    payload = json.loads(_role_callback("dev"))
    payload["dispatch"] = {"handler": "rookie_sop_tick", "matched": True}

    assert rs._resolve_role(payload)["error"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`rookie_sop_role_set.py` 不存在

- [ ] **Step 3: 写实现**

创建 `examples/haitun-workspace/tools/rookie_sop_role_set.py`：

```python
"""新人在开发环境卡上自选角色: 非研发则整模块标不适用, 研发则展开 5 项。

刻意为之: 选「研发」时发一张新卡而不是 edit 原卡 —— 原卡的角色按钮点完就被消费了,
edit_card 不重新注册回调, 编辑出来的勾选按钮全是死的。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_send_card

_HANDLER = "rookie_sop_role_set"
_DEV_MODULE = "开发环境"


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _resolve_role(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch = _as_dict(payload.get("dispatch"))
    handler = str(dispatch.get("handler") or "").strip()
    if handler and handler != _HANDLER:
        return {"error": f"unexpected handler {handler!r}; expected {_HANDLER!r}"}
    if handler == _HANDLER and dispatch.get("matched") is False:
        return {"error": "dispatch.matched is false; do not invent a handler"}

    action = _as_dict(payload.get("action"))
    value = action.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    value = _as_dict(value)

    action_name = str(value.get("action") or action.get("action_id") or "")
    role = str(value.get("role") or "").strip()
    if not role:
        role = "dev" if action_name == _card.ACTION_ROLE_DEV else "nondev" if action_name == _card.ACTION_ROLE_NONDEV else ""
    if role not in {"dev", "nondev"}:
        return {"error": f"cannot resolve role from action {action_name!r}"}

    business = _as_dict(payload.get("business_context"))
    source = _as_dict(payload.get("source"))
    return {
        "error": "",
        "role": role,
        "open_id": str(business.get("open_id") or "").strip()
        or str(source.get("operator_open_id") or source.get("open_id") or "").strip(),
        "name": str(business.get("name") or "").strip(),
        "app_token": str(business.get("app_token") or "").strip(),
        "detail_table_id": str(business.get("detail_table_id") or "").strip(),
        "overview_table_id": str(business.get("overview_table_id") or "").strip(),
    }


async def rookie_sop_role_set(card_action_json: str = "") -> str:
    """Record the new hire's role from the 开发环境 card, then settle that module.

    Call this for a ``<feishu_card_action>`` whose ``dispatch.handler`` is
    ``rookie_sop_role_set``. Non-dev marks every 开发环境 row 不适用 (excluded from the
    progress denominator, reminders and the HR digest) and sends one terminal card.
    Dev sends a **new** card listing the five dev items — the original card's buttons were
    consumed on click and cannot be revived by editing.

    Args:
        card_action_json: Full ``<feishu_card_action>`` payload JSON string.
    """
    payload = _store._parse_result(card_action_json)
    if not payload:
        return json.dumps({"ok": False, "error": "card_action_json must be a JSON object"}, ensure_ascii=False)

    ctx = _resolve_role(payload)
    if ctx.get("error"):
        return json.dumps({"ok": False, "error": ctx["error"]}, ensure_ascii=False)
    if not ctx["open_id"]:
        return json.dumps({"ok": False, "error": "cannot resolve open_id"}, ensure_ascii=False)

    state = await _rt.load_state()
    app_token = ctx["app_token"] or str(state.get("app_token") or "")
    detail_table = ctx["detail_table_id"] or str(state.get("detail_table_id") or "")
    overview_table = ctx["overview_table_id"] or str(state.get("overview_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()
    is_dev = ctx["role"] == "dev"
    label = "研发" if is_dev else "非研发"

    rows = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    dev_rows = [r for r in rows if str(r.get("模块") or "") == _DEV_MODULE]
    if dev_rows:
        await bitable.update_records(
            app_token,
            detail_table,
            json.dumps(
                [{"record_id": r["record_id"], "fields": {"适用角色": label}} for r in dev_rows],
                ensure_ascii=False,
            ),
        )
    if not is_dev:
        await _store.mark_module_na(
            bitable, app_token, detail_table, open_id=ctx["open_id"], module=_DEV_MODULE, today=today
        )

    rows = await _store.fetch_detail(bitable, app_token, detail_table, ctx["open_id"])
    cfg = await _store.load_config()
    items = _cfg.load_sop(cfg)
    window = next((i.window_days for i in items if i.module == _DEV_MODULE), 7)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), today)
    due_text = f"Day 1-{window} 截止（{_cfg.due_date(onboard, window)}）"

    fresh_dev_rows = [
        r for r in rows if str(r.get("模块") or "") == _DEV_MODULE and str(r.get("状态") or "") != _p.STATUS_NA
    ]
    card, handlers = _card.role_settled_card(is_dev, fresh_dev_rows, due_text, str(cfg.get("sop_doc_url") or ""))
    business = {
        "type": "rookie_sop",
        "open_id": ctx["open_id"],
        "name": ctx["name"] or ctx["open_id"],
        "module": _DEV_MODULE,
        "app_token": app_token,
        "detail_table_id": detail_table,
        "overview_table_id": overview_table,
    }
    await feishu_message_send_card(
        ctx["open_id"],
        json.dumps(card, ensure_ascii=False),
        "open_id",
        "",
        json.dumps(business, ensure_ascii=False),
        json.dumps(handlers, ensure_ascii=False),
        True,
    )

    if overview_table:
        await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=ctx["open_id"],
            name=ctx["name"] or ctx["open_id"],
            role=ctx["role"],
            rows=rows,
            today=today,
        )

    return json.dumps({"ok": True, "role": ctx["role"], "dev_items": len(fresh_dev_rows)}, ensure_ascii=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 35 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/rookie_sop_role_set.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 角色自选回调 —— 非研发整模块不适用"
```

---

### Task 8: 每日催办（9:30，fire=tool）

**Files:**
- Create: `examples/haitun-workspace/tools/rookie_sop_remind.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 2 `summarize`；Task 3 `remind_card` / `graduation_card`；Task 4 `fetch_detail`
- Produces:
  - `rookie_sop_remind(open_id: str = "") -> str`
  - `decide_remind(rows: list[dict], today: date) -> dict` —— 纯函数，返回
    `{"kind": "silent" | "remind" | "graduate", "progress": Progress}`。
    抽成纯函数是为了让「静默/催办/毕业」三条分支能单测，不需要飞书。

**静默规则**（设计里定的）：全部完成 → 发一次毕业卡并删定时；无逾期无到期 → 静默不发。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def test_decide_remind_stays_silent_when_nothing_is_due() -> None:
    rm = _load("rookie_sop_remind")
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5)),
        _row("attendance", p.STATUS_TODO, date(2026, 8, 20)),  # 还早
    ]

    got = rm.decide_remind(rows, date(2026, 8, 7))

    assert got["kind"] == "silent"


def test_decide_remind_fires_when_overdue_or_due_today() -> None:
    rm = _load("rookie_sop_remind")
    p = _load("_rookie_sop_progress")

    overdue = [_row("wifi", p.STATUS_TODO, date(2026, 8, 5))]
    assert rm.decide_remind(overdue, date(2026, 8, 7))["kind"] == "remind"

    due_today = [_row("desk", p.STATUS_TODO, date(2026, 8, 7))]
    assert rm.decide_remind(due_today, date(2026, 8, 7))["kind"] == "remind"


def test_decide_remind_graduates_when_all_applicable_items_are_done() -> None:
    rm = _load("rookie_sop_remind")
    p = _load("_rookie_sop_progress")
    rows = [
        _row("wifi", p.STATUS_DONE, date(2026, 8, 5)),
        _row("git_workflow", p.STATUS_NA, date(2026, 8, 11), module="开发环境"),
    ]

    got = rm.decide_remind(rows, date(2026, 8, 7))

    assert got["kind"] == "graduate"
    assert got["progress"].total == 1


def test_decide_remind_on_empty_rows_is_silent_not_graduate() -> None:
    rm = _load("rookie_sop_remind")

    # 明细还没建好时不能误报毕业
    assert rm.decide_remind([], date(2026, 8, 7))["kind"] == "silent"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`rookie_sop_remind.py` 不存在

- [ ] **Step 3: 写实现**

创建 `examples/haitun-workspace/tools/rookie_sop_remind.py`：

```python
"""每日 9:30 催办: 只在有逾期或今日到期时发卡; 全部完成则发毕业卡并删掉自己的定时。

由 schedules/rookie-remind-<后8位> 以 fire=tool 调用, 到点不经过 LLM。
按截止日驱动而非发放日 —— SOP 的模块几乎都从 Day 1 开始, 只是截止日不同。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_send_card
from schedule_manage import schedule_manage


def decide_remind(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """三条分支: 静默 / 催办 / 毕业。抽成纯函数以便单测。"""
    progress = _p.summarize(rows, today)
    if progress.all_done:
        return {"kind": "graduate", "progress": progress}
    if progress.overdue or progress.due_today:
        return {"kind": "remind", "progress": progress}
    # 刻意为之: 无欠项就不发 —— 让消息量随完成度自然衰减, 避免日推麻木
    return {"kind": "silent", "progress": progress}


async def rookie_sop_remind(open_id: str = "") -> str:
    """Remind one new hire of overdue / due-today onboarding items, or graduate them.

    Fired by that person's ``rookie-remind-<suffix>`` schedule with ``fire=tool``, so no
    LLM is involved. Silent when nothing is overdue or due today. When every applicable
    item is done it sends one graduation card and deletes its own schedule.

    Args:
        open_id: The new hire's Feishu open_id (written into the schedule's tool_args).
    """
    target = (open_id or "").strip()
    if not target:
        return json.dumps({"ok": False, "error": "open_id is required"}, ensure_ascii=False)

    state = await _rt.load_state()
    app_token = str(state.get("app_token") or "")
    detail_table = str(state.get("detail_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    rows = await _store.fetch_detail(bitable, app_token, detail_table, target)
    today = date.today()
    decision = decide_remind(rows, today)
    kind = decision["kind"]
    progress = decision["progress"]

    if kind == "silent":
        return json.dumps({"ok": True, "sent": False, "reason": "nothing overdue or due today"}, ensure_ascii=False)

    cfg = await _store.load_config()
    name = next((str(r.get("姓名") or "") for r in rows if r.get("姓名")), target)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), today)

    if kind == "graduate":
        card, handlers = _card.graduation_card(name, progress.total)
    else:
        card, handlers = _card.remind_card(
            name, _cfg.day_index(onboard, today), progress, str(cfg.get("sop_doc_url") or "")
        )

    business = {
        "type": "rookie_sop",
        "open_id": target,
        "name": name,
        "module": "催办",
        "app_token": app_token,
        "detail_table_id": detail_table,
        "overview_table_id": str(state.get("overview_table_id") or ""),
    }
    await feishu_message_send_card(
        target,
        json.dumps(card, ensure_ascii=False),
        "open_id",
        "",
        json.dumps(business, ensure_ascii=False),
        json.dumps(handlers, ensure_ascii=False),
        bool(handlers),
    )

    if kind == "graduate":
        await schedule_manage(action="delete", schedule_name=f"rookie-remind-{target[-8:]}")

    return json.dumps(
        {"ok": True, "sent": True, "kind": kind, "overdue": len(progress.overdue), "due_today": len(progress.due_today)},
        ensure_ascii=False,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 39 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/rookie_sop_remind.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): 每日催办 —— 无欠项静默, 全完成发毕业卡"
```

---

### Task 9: HR 日报（18:30，fire=prompt）

**Files:**
- Create: `examples/haitun-workspace/tools/rookie_sop_digest.py`
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加）

**Interfaces:**
- Consumes: Task 3 `digest_card`；Task 4 `fetch_detail` / `recompute_overview`
- Produces:
  - `rookie_sop_digest(hr_open_id: str = "") -> str`
  - `active_rookies(overview_rows: list[dict], today: date) -> list[dict]` —— 纯函数：只留「进行中」
    与「今天刚出新手村」的人；已毕业且非今日的从日报里退场

**为什么走 fire=prompt**：日报内容必须现算聚合，`fire=tool` 到点不经 LLM 只能调一个工具传固定参数。
本工具自己完成聚合与发卡，所以 TASK 正文只需要一句「调用 rookie_sop_digest」即可。

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def test_active_rookies_keeps_in_progress_and_todays_graduates() -> None:
    dg = _load("rookie_sop_digest")
    overview = [
        {"姓名": "张三", "状态": "进行中", "最后更新": date(2026, 8, 7)},
        {"姓名": "李四", "状态": "已出新手村", "最后更新": date(2026, 8, 7)},  # 今天毕业, 报一次
        {"姓名": "王五", "状态": "已出新手村", "最后更新": date(2026, 8, 1)},  # 早就毕业, 退场
    ]

    got = dg.active_rookies(overview, date(2026, 8, 7))

    assert [r["姓名"] for r in got] == ["张三", "李四"]


def test_active_rookies_on_empty_overview_is_empty() -> None:
    dg = _load("rookie_sop_digest")
    assert dg.active_rookies([], date(2026, 8, 7)) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: FAIL，`rookie_sop_digest.py` 不存在

- [ ] **Step 3: 写实现**

创建 `examples/haitun-workspace/tools/rookie_sop_digest.py`：

```python
"""每日 18:30 给 HR 发一张在途新人进度日报, 带总览表链接; 顺带重算总览做兜底对账。

由 schedules/rookie-digest-daily 以 fire=prompt 触发(内容要现算聚合, fire=tool
到点不经 LLM、只能调一个工具传固定参数)。本工具自己完成聚合与发卡。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_message import feishu_message_send_card


def active_rookies(overview_rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """在途 = 进行中, 外加今天刚出新手村的(报一次就退场)。"""
    active: list[dict[str, Any]] = []
    for row in overview_rows:
        status = str(row.get("状态") or "")
        if status != "已出新手村":
            active.append(row)
            continue
        updated = row.get("最后更新")
        if isinstance(updated, date) and updated == today:
            active.append(row)
    return active


async def rookie_sop_digest(hr_open_id: str = "") -> str:
    """Send HR one card summarising every in-flight new hire, plus the overview table link.

    Recomputes each person's overview row from the detail table first, so the digest also
    serves as the daily reconciliation pass. Sends nothing when no one is in flight; when
    everyone is on track it still sends («全部正常»), because HR must be able to tell
    "no news because nothing is wrong" from "no news because the job is broken".

    Args:
        hr_open_id: HR's Feishu open_id. Empty → ``hr_notify_id`` from
            ``config/rookie_sop.yaml``.
    """
    cfg = await _store.load_config()
    target = (hr_open_id or "").strip() or str(cfg.get("hr_notify_id") or "").strip()
    if not target:
        return json.dumps(
            {"ok": False, "error": "hr_open_id is required (or set hr_notify_id in config/rookie_sop.yaml)"},
            ensure_ascii=False,
        )

    state = await _rt.load_state()
    app_token = str(state.get("app_token") or "")
    detail_table = str(state.get("detail_table_id") or "")
    overview_table = str(state.get("overview_table_id") or "")
    if not app_token or not overview_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()
    today = date.today()

    raw = await bitable.search_records(app_token, overview_table, "", page_size=500)
    overview_rows = [_store._row_of(i) for i in _store._items_of(raw)]

    # 兜底对账: 每人从明细整体重算一遍, 修掉任何漏写造成的漂移
    for row in overview_rows:
        open_id = str(row.get("open_id") or "").strip()
        if not open_id or not detail_table:
            continue
        detail = await _store.fetch_detail(bitable, app_token, detail_table, open_id)
        if not detail:
            continue
        role_label = next((str(r.get("适用角色") or "") for r in detail if r.get("适用角色") in {"研发", "非研发"}), "")
        role = "dev" if role_label == "研发" else "nondev" if role_label == "非研发" else ""
        await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=open_id,
            name=str(row.get("姓名") or open_id),
            role=role,
            rows=detail,
            today=today,
        )

    raw = await bitable.search_records(app_token, overview_table, "", page_size=500)
    overview_rows = [_store._row_of(i) for i in _store._items_of(raw)]
    active = active_rookies(overview_rows, today)
    if not active:
        return json.dumps({"ok": True, "sent": False, "reason": "no active rookies"}, ensure_ascii=False)

    card, handlers = _card.digest_card(
        active,
        str(state.get("table_url") or f"https://feishu.cn/base/{app_token}"),
        f"{today.month}月{today.day}日",
    )
    await feishu_message_send_card(
        target,
        json.dumps(card, ensure_ascii=False),
        "open_id",
        "",
        json.dumps({"type": "rookie_sop_digest", "date": today.isoformat()}, ensure_ascii=False),
        json.dumps(handlers, ensure_ascii=False),
    )
    return json.dumps({"ok": True, "sent": True, "rookies": len(active)}, ensure_ascii=False)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 41 passed

- [ ] **Step 5: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/tools/rookie_sop_digest.py \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): HR 日报 —— 聚合在途新人并兜底重算总览"
```

---

### Task 10: skill、触发器与文档接线

**Files:**
- Create: `examples/haitun-workspace/skills/feishu-rookie-onboarding/SKILL.md`
- Create: `examples/haitun-workspace/triggers/rookie-sop-welcome/TRIGGER.md`
- Modify: `examples/haitun-workspace/AGENTS.md`（工具表 + skill 列表各加一行）
- Modify: `examples/haitun-workspace/tests/test_rookie_sop.py`（追加登记校验）

**Interfaces:**
- Consumes: Task 5-9 的五个工具名
- Produces: 无新代码接口；本任务把工具接进 agent 包，使触发器与 skill 生效

- [ ] **Step 1: 写失败测试**

追加到 `examples/haitun-workspace/tests/test_rookie_sop.py` 末尾：

```python
def test_all_five_tools_exist_as_files() -> None:
    for name in (
        "rookie_sop_card_send",
        "rookie_sop_tick",
        "rookie_sop_role_set",
        "rookie_sop_remind",
        "rookie_sop_digest",
    ):
        assert (TOOLS / f"{name}.py").is_file(), f"缺少工具文件 {name}.py"


def test_trigger_and_skill_are_registered() -> None:
    trigger = HAITUN / "triggers" / "rookie-sop-welcome" / "TRIGGER.md"
    skill = HAITUN / "skills" / "feishu-rookie-onboarding" / "SKILL.md"
    assert trigger.is_file()
    assert skill.is_file()

    trigger_text = trigger.read_text(encoding="utf-8")
    # fire=tool: 到点/命中不经过 LLM
    assert "fire: tool" in trigger_text
    assert "tool: rookie_sop_card_send" in trigger_text
    assert "event: feishu.hr.user_created" in trigger_text

    skill_text = skill.read_text(encoding="utf-8")
    assert "rookie_sop_tick" in skill_text
    assert "rookie_sop_role_set" in skill_text


def test_agents_md_documents_the_new_tools() -> None:
    text = (HAITUN / "AGENTS.md").read_text(encoding="utf-8")
    assert "rookie_sop_card_send" in text
    assert "feishu-rookie-onboarding" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -k "trigger or agents_md" -o addopts=""`
Expected: FAIL，TRIGGER.md / SKILL.md 不存在

- [ ] **Step 3: 写触发器**

创建 `examples/haitun-workspace/triggers/rookie-sop-welcome/TRIGGER.md`：

```markdown
---
name: rookie-sop-welcome
description: 通讯录新建员工时，给新人发入职 SOP 的逐项可勾选卡片
event: feishu.hr.user_created
source: feishu
filter: {}
visibility: silent
run_once: false
fire: tool
raw_event: contact.user.created_v3
tool: rookie_sop_card_send
tool_args: {}
---

向 payload.open_id 发送按 SOP 模块拆分的 multi_use 勾选卡，并为该新人建立每日 9:30 催办定时任务。
open_id / name 由 Session 注入 event_payload_json，不要写死 tool_args。

入职触发方式待核对：若真实流程是 HR 先在表里登记、通讯录后建，把本触发器停用，
改为手动或表驱动调用 `rookie_sop_card_send`（入口是独立工具，换触发方式不影响其余部分）。
```

- [ ] **Step 4: 写 skill**

创建 `examples/haitun-workspace/skills/feishu-rookie-onboarding/SKILL.md`：

```markdown
---
name: feishu-rookie-onboarding
description: "Use when a new hire joins (feishu.hr.user_created), when sending or re-sending the onboarding SOP cards, or when handling a <feishu_card_action> whose handler is rookie_sop_tick / rookie_sop_role_set. Covers per-module tickable cards, the 研发/非研发 role choice, daily 9:30 reminders, and the 18:30 HR digest with its overview-table link."
category: productivity
agent_editable: true
---

# 新人入职 SOP 卡片闭环

新人入职后：按 SOP 模块发**逐行可勾选**的卡（multi_use，勾一行只结那一行）→ 新人自己勾 →
写明细表并重算总览行 → 每日 9:30 按截止日催办 → 每日 18:30 给 HR 发汇总卡 + 总览表链接。

## When to use

- 通讯录新建员工（`feishu.hr.user_created`），或用户要求「给某人发入职 SOP 卡」。
- 收到 `<feishu_card_action>`，且 `dispatch.handler` 为 `rookie_sop_tick` 或 `rookie_sop_role_set`。

## When not to use

- 管理制度确认卡（那是 `feishu-handbook-onboarding`，两者互不替代）。
- 普通待办清单 → `feishu-todo-card`；一张卡只要一个答案 → `feishu_message_send_card`。
- 签字确认、背调材料收集等线下环节。

## Instructions

### 发卡

1. 调 `rookie_sop_card_send`。触发器场景参数留空，靠 Session 注入的 `event_payload_json`。
2. 手工联调传 `open_id`（必填）、可选 `name` 与 `onboard_date`（`YYYY-MM-DD`，默认今天）。
3. 幂等：同一人重复调用复用已有明细行，不会写出两套，也不会重复建定时任务。
4. 工具成功后卡片已可见：本轮**零 assistant 文本**（不要说「卡片已发送」）。

### 处理勾选

1. 解析 `<feishu_card_action>` 整段 JSON，调 `rookie_sop_tick(card_action_json=<整段 JSON>)`。
2. 不要先复述「你点击了…」—— 卡片已由框架原地重绘。
3. 成功 → 零文本结束；只有工具返回 `ok=false` 才回报必要错误，不得谎称成功。
4. **批量**：payload 若包在 `<feishu_card_action_batch>` 里，**每条各调一次**
   （漏一条就丢一项完成），然后最多回一条汇总，或直接零文本。

### 处理角色选择

1. `dispatch.handler` 为 `rookie_sop_role_set` 时调 `rookie_sop_role_set(card_action_json=…)`。
2. 选「非研发」→ 开发环境模块全标 `不适用`（不计进度分母、不催办、不进 HR 日报）。
3. 选「研发」→ 工具会发一张**新卡**列出 5 个研发项。这是刻意的：原卡按钮点完即被消费，
   `feishu_message_edit_card` 不重新注册回调，编辑出来的按钮全是死的。

### 定时任务

- 催办：每人一份 `rookie-remind-<open_id 后 8 位>`，`cron="30 9 * * *"`、`fire=tool`、
  `tool="rookie_sop_remind"`、`tool_args={"open_id": "ou_…"}`。由 `rookie_sop_card_send` 自动建。
  新人出新手村后工具会删掉自己这一份。
- HR 日报：全局一份 `rookie-digest-daily`，落在 HR 自己的 Session，`cron="30 18 * * *"`、
  **`fire=prompt`**（内容要现算聚合，`fire=tool` 到点不经 LLM 只能传固定参数），
  TASK 正文写「调用 rookie_sop_digest」。

## 边界

- 禁止用 `feishu_message_edit_card` 改这些卡（不重新注册回调，按钮会全死）。
- 禁止手写 `schedules/*/TASK.md` 或 `triggers/*/TRIGGER.md`，一律走
  `schedule_manage` / `trigger_manage`。
- 总览表是**投影**：只由工具从明细整体重算，不要手工改它、也不要写增量更新逻辑。
- 单卡最多 40 行。

## 配置

`config/rookie_sop.yaml`：SOP 清单（模块 / 项 / 验收标准 / `window_days` / `dev_only`）、
`sop_doc_url`、`hr_notify_id`。改 SOP 只改这里，不动代码。
运行时的 `app_token` 与两个 `table_id` 存在 workspace 的 `.psi/rookie_sop/base.json`。
```

- [ ] **Step 5: 登记到 AGENTS.md**

在 `examples/haitun-workspace/AGENTS.md` 的工具表里，紧跟 `handbook_onboarding_*` 那一行（约 162 行）之后插入：

```markdown
| `rookie_sop_card_send` / `rookie_sop_tick` / `rookie_sop_role_set` / `rookie_sop_remind` / `rookie_sop_digest` | 新人入职 SOP 闭环：按模块发 multi_use 逐行勾选卡 → 勾选写明细表并从明细整体重算总览行 → 每日 9:30 按截止日催办（无欠项静默、全完成发毕业卡并删定时）→ 每日 18:30 给 HR 发汇总卡 + 总览表链接。开发环境模块由新人自选研发/非研发，非研发整模块标不适用。配置 `config/rookie_sop.yaml`；触发器 `rookie-sop-welcome`；卡片回调 skill：`feishu-rookie-onboarding`。 |
```

在 skill 列表里，紧跟 `feishu-handbook-onboarding` 那一行（约 235 行）之后插入：

```markdown
- `feishu-rookie-onboarding` — 新人入职 SOP 闭环：`feishu.hr.user_created` → `rookie_sop_card_send` 按模块发逐行可勾选卡；`<feishu_card_action>` → `rookie_sop_tick` / `rookie_sop_role_set`；定时 `rookie_sop_remind`（9:30，fire=tool）与 `rookie_sop_digest`（18:30，fire=prompt）。配置 `config/rookie_sop.yaml`。
```

- [ ] **Step 6: 运行全部测试**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_rookie_sop.py -q -o addopts=""`
Expected: 44 passed

- [ ] **Step 7: 跑工具发现测试确认没破坏既有登记**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m pytest examples/haitun-workspace/tests/test_tool_discovery.py examples/haitun-workspace/tests/test_prompt_sections.py -q -o addopts=""`
Expected: 全部 PASS（若报「工具未在 AGENTS.md 登记」，按提示补齐 Step 5 的两行）

- [ ] **Step 8: 跑 lint**

Run: `cd /public/home/wwb/Dolphin-Agent && .venv/bin/python -m ruff check examples/haitun-workspace/tools/_rookie_sop_*.py examples/haitun-workspace/tools/rookie_sop_*.py examples/haitun-workspace/tests/test_rookie_sop.py`
Expected: `All checks passed!`（中文全角标点在注释/docstring 里报 RUF002/RUF003，按实际报出的码补 noqa；不报就别加，否则 RUF100）

- [ ] **Step 9: 提交**

```bash
cd /public/home/wwb/Dolphin-Agent
git add examples/haitun-workspace/skills/feishu-rookie-onboarding/SKILL.md \
        examples/haitun-workspace/triggers/rookie-sop-welcome/TRIGGER.md \
        examples/haitun-workspace/AGENTS.md \
        examples/haitun-workspace/tests/test_rookie_sop.py
git commit -m "feat(haitun/rookie): skill、触发器与 AGENTS.md 登记"
```

---

### Task 11: 端到端联调清单（人工，需真实飞书凭据）

**Files:**
- Modify: `examples/haitun-workspace/config/rookie_sop.yaml`（填真实链接与 HR open_id）

这一任务不写代码，是上线前的人工验证。前 10 个任务的单测都不需要飞书凭据，
但下列行为只能在真实环境确认。

- [ ] **Step 1: 填配置**

编辑 `config/rookie_sop.yaml`：
- `sop_doc_url` 换成真实 SOP 文档链接
- `hr_notify_id` 填罗霖的 open_id（`ou_…`）

- [ ] **Step 2: 确认进程环境变量**

Gateway/Session 与 Channel 进程都需要 `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`；
飞书后台需订阅 `contact.user.created_v3`。

- [ ] **Step 3: 手动发一次卡**

对自己的 open_id 调 `rookie_sop_card_send(open_id="ou_自己", onboard_date="<今天>")`，确认：
- 收到 7 张卡（6 个普通模块 + 1 张开发环境角色卡）
- `.psi/rookie_sop/base.json` 生成，且飞书里出现「入职明细」「入职总览」两张表
- 明细表行数与 `rookie_sop.yaml` 条目数一致

- [ ] **Step 4: 验证逐行勾选（这是核心）**

在一张模块卡上**连续勾多行**，确认：
- 勾过的行变成 `■ ~~删除线~~` 且按钮消失，**其余行仍可点**
- 重复点同一行不会重复写表
- 明细表状态与完成时间同步更新，总览表进度随之变化

- [ ] **Step 5: 验证角色分支**

选「我不是研发」，确认开发环境 5 项在明细表里变成 `不适用`，且总览的进度分母从 32 降到 27。
另找一个测试 open_id 选「我是研发」，确认收到一张新卡列出 5 项。

- [ ] **Step 6: 验证定时**

- `schedule_manage(action="view", schedule_name="rookie-remind-<后8位>")` 确认 `fire: tool` 与 `cron: "30 9 * * *"`
- 建 HR 日报定时：
```text
schedule_manage(
  action="create",
  schedule_name="rookie-digest-daily",
  cron="30 18 * * *",
  fire="prompt",
  content="调用 rookie_sop_digest 给 HR 发今天的新人入职进度日报。",
  visibility="silent",
  description="新人入职进度 HR 日报"
)
```
- 想立刻看效果就直接调 `rookie_sop_remind(open_id="ou_自己")` 与 `rookie_sop_digest()`，不必等到点

- [ ] **Step 7: 给 HR 开总览表查看权限**

表由机器人持有，罗霖默认看不到。用 `feishu_api` 打 drive permission member 接口，
把罗霖加为 `view`（`feishu-drive` 技能里有该接口的参数约束）。
确认罗霖点日报卡上的「查看详情表格」能打开。

---

## Self-Review

按规格逐节核对：

| 规格要求 | 落在哪个任务 |
|---|---|
| SOP 清单可配、不改代码 | Task 1（`rookie_sop.yaml` + 解析） |
| 截止日 = 入职日 + 窗口 - 1；入职第 N 天自然日 | Task 1（`due_date` / `day_index`，含跨月测试） |
| 进度分母口径（研发/非研发/未选） | Task 1 `applicable_items` + Task 2 `summarize` |
| 明细表一人多行、唯一事实来源 | Task 4 `DETAIL_FIELDS` / `fetch_detail` |
| 总览表一人一行、从明细整体重算 | Task 4 `recompute_overview`（含「改坏能自愈」测试） |
| 索引列必须是文本、不得用 type 19 | Task 4 字段测试 |
| Day 1 发齐全部模块卡 | Task 5 `plan_module_cards` |
| 每行独立 action、multi_use 逐行勾选 | Task 3 `module_card` + Task 6 |
| 开发环境两段式角色自选 | Task 3 `role_card` / `role_settled_card` + Task 7 |
| 非研发整模块不适用、不进分母/催办/日报 | Task 7 `mark_module_na` + Task 2 + Task 8 |
| Mentor 不在卡片上体现 | Task 3（卡片不渲染 Mentor 列）+ Task 4（表里保留供筛选） |
| 催办按截止日、无欠项静默、全完成毕业并删定时 | Task 8 `decide_remind` |
| HR 日报现算聚合、无在途不发、正常也发 | Task 9 `active_rookies` + Task 3 `digest_card` |
| 详情链接为普通跳转按钮、非交互 action | Task 3 `digest_card` 测试断言无 `rookie_` |
| 每人一份催办 / 全局一份日报 | Task 5（建催办）+ Task 10（建日报）+ Task 9 |
| 幂等：重复入职不重复建行发卡 | Task 5（先 `fetch_detail` 再决定是否建） |
| 幂等：重复勾选被拒一次 | 框架 per-action 墓碑 + Task 4 `mark_done` 的 `already_done` |
| 批量回调每条都处理 | Task 6 docstring + Task 10 SKILL.md |
| 禁用 `edit_card` | Task 7 注释 + Task 10 SKILL.md 边界 |
| 待核对项隔离在入口层 | Task 10 TRIGGER.md 注明可换触发方式 |
| 机器人身份持有表、HR 显式授权 | Task 11 Step 7 |

类型与命名一致性核对：
- `STATUS_TODO/DONE/NA` 定义在 Task 2，Task 3/4/7/8 全部从 `_rookie_sop_progress` 引用，无重复定义。
- `ACTION_TICK_PREFIX = "rookie_tick_"` 定义在 Task 3；Task 6 解析时用同一字面量前缀，一致。
- `HANDLER_TICK = "rookie_sop_tick"` / `HANDLER_ROLE = "rookie_sop_role_set"` 与 Task 6/7 的
  `_HANDLER` 常量、以及工具文件名三者一致。
- bitable 适配器的三个方法名（`search_records` / `create_records` / `update_records`）在
  Task 4 的 fake、Task 5 的真实适配器、Task 9 的直接调用处一致。
- 日期：`_rookie_sop_progress` 只见 `date`；毫秒转换只在 `_rookie_sop_store` 发生（`_DATE_KEYS`）。

---
