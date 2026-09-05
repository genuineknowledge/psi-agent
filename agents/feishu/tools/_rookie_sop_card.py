"""组各类入职卡片的 JSON —— 纯逻辑, 不发消息, 便于单测。

刻意为之: 建在 multi_use 卡之上, 每行一个独立 action。行的 action 名必须唯一且规范,
多选消费就是按它落墓碑的; 撞名或留空会让该行退回整卡去重(点一下整张卡就废)。
外壳沿用 tools/feishu_todo_card.py 的 legacy 形态: update_multi 必须为 true,
否则卡片只对一个查看者更新。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import sys
from datetime import date
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
# 与 rookie_sop_role_set / config/rookie_sop.yaml 里的 id 一致: 角色确认这一项
_ROLE_CONFIRMED_ITEM = "role_confirmed"
_FILLED = "■"

# 未勾选的方框按钮文字。刻意用空心方框而非「标记完成」:
# 文字在左、方框在右, 框架勾选后把这个按钮换成「● ~~□~~」——
# 那个实心 ● 恰好就是「打上勾」的视觉反馈。
_BOX = "完成"

# DDL 状态 → (emoji, 卡片主题色)。绿=还早, 黄=今天到期, 红=已逾期。
_DUE_OK = "🟢"
_DUE_TODAY = "🟡"
_DUE_LATE = "🔴"
_DONE_MARK = "✅"
_NA_MARK = "⚪"


def _due_state(row: dict[str, Any], today: date | None) -> str:
    """一行的 DDL 状态标记 —— 已完成/不适用优先, 其余按截止日与今天比。"""
    status = str(row.get("状态") or "")
    if status == _p.STATUS_DONE:
        return _DONE_MARK
    if status == _p.STATUS_NA:
        return _NA_MARK
    due = row.get("截止日")
    if today is None or not isinstance(due, date):
        return _DUE_OK
    if due < today:
        return _DUE_LATE
    if due == today:
        return _DUE_TODAY
    return _DUE_OK


def _card_template(rows: list[dict[str, Any]], today: date | None) -> str:
    """入职卡只用红绿两色, 按**入职第几天**定, 不按每行的 DDL。

    Day 1 绿(还早)、Day 2 红(最后一天)。全部做完一律绿 —— 做完了就没有紧迫可言。
    刻意不再用橙/蓝: 需求就是红绿两色, 多一种颜色就多一层要解释的语义。
    """
    marks = {_due_state(r, today) for r in rows}
    if marks and marks <= {_DONE_MARK, _NA_MARK}:
        return "green"
    return "red" if _day_index(rows, today) >= 2 else "green"


def _day_index(rows: list[dict[str, Any]], today: date | None) -> int:
    """入职第几天(第一天 = 1)。取不到入职日时按第 1 天算, 宁可显绿也不误报红。"""
    if today is None:
        return 1
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), None)
    if onboard is None:
        return 1
    return max(1, (today - onboard).days + 1)


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


def _row_elements(row: dict[str, Any], today: date | None = None) -> tuple[list[dict[str, Any]], str]:
    """一行 = 左侧「状态标记 + 条目名 + 验收小字」, 右侧一个方框按钮。

    方框只写一个符号(见 _BOX), 条目名留在左侧文字里 —— 框和字挤在一个按钮内很难看。
    勾选后的完成态由 rookie_sop_tick 重绘整卡实现, 不依赖框架替换 div.extra
    (那条路会被飞书拒: div.extra 不接受 markdown, 详见 _two_col 的说明)。
    """
    title = str(row.get("项") or "").strip()
    acceptance = str(row.get("验收标准") or "").strip()
    status = str(row.get("状态") or "")
    mark = _due_state(row, today)

    if status == _p.STATUS_DONE:
        finished = row.get("完成时间")
        tail = f"　<font color='grey'>{finished}</font>" if finished is not None else ""
        return _two_col(f"{mark} ~~{title}~~{tail}", None), ""
    if status == _p.STATUS_NA:
        return _two_col(f"{mark} <font color='grey'>~~{title}~~　不适用</font>", None), ""

    left = f"{mark} **{title}**"
    if acceptance:
        left += f"\n<font color='grey'>{acceptance}</font>"
    action = f"{ACTION_TICK_PREFIX}{_item_id_of(row)}"
    return _two_col(left, {"action": action, "item_id": _item_id_of(row)}), action


def _two_col(left_markdown: str, action_value: dict[str, Any] | None, label: str = "") -> list[dict[str, Any]]:
    """一行 = div 左侧文字 + extra 右侧方框, 这是飞书原生的「左文右控件」结构。

    刻意为之: 按钮放 div.extra, 文字与方框同一行, 观感最紧凑。
    但飞书的 div.extra 只接受 [img button select_static select_person overflow
    date_picker ...] —— 不接受 markdown。所以**不能**让框架去替换它:
    框架勾选后会把被点元素换成 markdown「● ~~…~~」, patch 会被拒
    (230099 / ErrCode 11310), 卡面永远不更新, 表现为「点了完成没反应」。
    因此勾选后的重绘改由本方做: rookie_sop_tick 重算整张卡再 edit,
    同时用 rewrite_card_snapshot 保住 multi_use 快照(否则按钮全死)。
    这样头部「已完成 N / M 项」也能跟着变 —— 框架只换被点的那一块, 不会重算头部。
    """
    element: dict[str, Any] = {"tag": "div", "text": {"tag": "lark_md", "content": left_markdown}}
    if action_value is not None:
        element["extra"] = {
            "tag": "button",
            "text": {"tag": "plain_text", "content": _BOX},
            "type": "primary",
            "size": "tiny",
            "value": action_value,
        }
    return [element]


def _rows_section(rows: list[dict[str, Any]], today: date | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """紧凑排布: 行与行之间不再插 hr —— 每行本身已是一个 column_set,
    分隔线会把卡片撑得很松散。只在段落之间用一条 hr。"""
    elements: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}
    for row in rows:
        row_elements, action = _row_elements(row, today)
        elements.extend(row_elements)
        if action:
            handlers[action] = HANDLER_TICK
    return elements, handlers


def _footer(sop_url: str) -> list[dict[str, Any]]:
    text = "💡 遇到问题先问 Haitun"
    if sop_url.strip():
        text += f" · [查看完整 SOP]({sop_url.strip()})"
    return [{"tag": "hr"}, {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}]


def _progress_bar(progress_text: str) -> str:
    """进度用纯数字。

    刻意为之: 不画 ▰▱ 方块进度条 —— 那串方框会和行尾按钮在视觉上撞车,
    整张卡显得杂乱(实测反馈)。全部做完时加一个 🎉 作为完成信号。
    """
    try:
        done_s, total_s = progress_text.split("/", 1)
        done, total = int(done_s), int(total_s)
    except ValueError, AttributeError:
        return progress_text
    if total <= 0:
        return progress_text
    tail = "　🎉" if done == total else ""
    return f"**已完成 {done} / {total} 项**{tail}"


def entry_card(
    name: str,
    rows: list[dict[str, Any]],
    doc_url: str,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """入口卡 —— 一条消息交代全貌, 详情在文档里勾。

    刻意为之: 不再一次性发 7 张模块卡(用户反馈观感太糟), 改成一张入口卡 +
    一个跳转到本人清单文档的按钮。卡上只做两件事: 报总进度、按模块列出各自欠项,
    让新人一眼知道还差什么; 真正的勾选在文档里做, 那里一屏能装完 33 项。

    返回的 handlers 恒为空 —— 卡上只有一个 URL 跳转按钮, 没有回调动作。
    进度靠文档变更事件驱动同步后重绘, 不靠卡上的按钮。
    """
    today = today or date.today()
    progress = _p.summarize(rows, today)
    head = f"👋 **{name}**，这是你的入职卡\n{_progress_bar(f'{progress.done}/{progress.total}')}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": head}, {"tag": "hr"}]

    # 卡片转红(入职第 2 天起)且还没做完时, 把 19:00 那条规则说在前面 ——
    # rookie_sop_digest 确实只报「第 2 天结束仍未完成」的人, 事先告知比事后被问公平。
    # 措辞用「了解原因」而非「上报」: 卡住多半有正当理由(等权限、等人带),
    # HR 要做的是解开堵点而不是问责。
    # 两档都要写清时限 —— 只给颜色不给话, 新人不知道「绿」意味着什么时候之前做完。
    # 绿(Day 1): 说清「两天内完成」这个窗口; 红(Day 2 起): 今天是最后一天, 没完成
    # HR 会来问是否遇到困难。
    # 措辞刻意用「了解原因 / 遇到困难」而非「上报、通报」: 卡住多半有正当理由
    # (等权限、等人带), HR 要做的是解开堵点而不是问责, 所以后半句直接给出路。
    if not progress.all_done:
        if _day_index(rows, today) >= 2:
            urgency = (
                "🔴 **今天是最后一天**——若今天收尾时仍未完成，HR 会来了解你是否遇到困难。"
                "卡在哪里直接说，缺权限、缺人带都能帮你推。"
            )
        else:
            urgency = (
                "🟢 **请在明天之内完成**——今天先把能做的做掉，明天收尾。若到明天还没完成，HR 会来了解你是否遇到困难。"
            )
        elements.append({"tag": "markdown", "content": urgency})
        elements.append({"tag": "hr"})

    modules: list[str] = []
    for row in rows:
        module = str(row.get("模块") or "")
        if module and module not in modules:
            modules.append(module)

    lines: list[str] = []
    for module in modules:
        module_rows = [r for r in rows if str(r.get("模块") or "") == module]
        applicable = [r for r in module_rows if str(r.get("状态") or "") != _p.STATUS_NA]
        if not applicable:
            lines.append(f"{_NA_MARK} <font color='grey'>{module}　不适用</font>")
            continue
        done_n = sum(1 for r in applicable if str(r.get("状态") or "") == _p.STATUS_DONE)
        # 模块的状态标记取该模块最紧急的一行 —— 与卡片主题色同一套判据
        mark = _DONE_MARK if done_n == len(applicable) else _card_module_mark(applicable, today)
        lines.append(f"{mark} **{module}**　{done_n}/{len(applicable)}")
    elements.append({"tag": "markdown", "content": "\n".join(lines) if lines else "清单还在准备中。"})

    if doc_url.strip():
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "action",
                "layout": "flow",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开我的入职卡"},
                        "type": "primary",
                        "url": doc_url.strip(),
                    }
                ],
            }
        )
    elements.append(
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "💡 在清单里逐项打勾即可，进度会自动同步"}]}
    )
    return _shell("入职卡", elements, _card_template(rows, today)), {}


def _card_module_mark(rows: list[dict[str, Any]], today: date | None) -> str:
    """一个模块的状态标记: 有逾期→红, 有当天到期→黄, 否则绿。"""
    marks = {_due_state(r, today) for r in rows}
    if _DUE_LATE in marks:
        return _DUE_LATE
    if _DUE_TODAY in marks:
        return _DUE_TODAY
    return _DUE_OK


def module_card(
    module: str,
    rows: list[dict[str, Any]],
    progress_text: str,
    due_text: str,
    sop_url: str,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    head = f"<font color='grey'>{due_text}</font>\n{_progress_bar(progress_text)}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": head}, {"tag": "hr"}]
    rows_elements, handlers = _rows_section(rows, today)
    elements.extend(rows_elements)
    elements.extend(_footer(sop_url))
    return _shell(f"入职卡 · {module}", elements, _card_template(rows, today)), handlers


def _role_button(label: str, action: str, role: str) -> list[dict[str, Any]]:
    """一个角色选项 —— 与条目行同样的「左文右框」形态, 视觉上保持一致。"""
    return _two_col(f"👤 **{label}**", {"action": action, "role": role})


def role_card(
    due_text: str,
    dev_rows: list[dict[str, Any]] | None = None,
    sop_url: str = "",
    progress_text: str = "",
    role_answered: bool = False,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """开发环境卡 —— 一张卡装完角色选择与 5 个开发项。

    刻意为之: 不再先发角色卡、答完再发第二张。multi_use 的消费粒度是单个 action,
    点掉一个角色按钮只会退休那一个按钮, 其余行(含 5 个开发项)照旧可点, 所以两类
    动作可以共存于同一张卡。两类 action 各自映射到自己的 handler,
    Channel 按 action 名分发, 互不干扰。
    """
    rows = dev_rows or []
    head = f"<font color='grey'>{due_text}</font>"
    if progress_text:
        head += f"\n{_progress_bar(progress_text)}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": head}, {"tag": "hr"}]
    handlers: dict[str, str] = {}

    # role_confirmed 这一项就是「角色是否已答」本身。角色未答时它由下面那两个角色
    # 按钮代表, 不再单独给它一个勾选按钮 —— 否则同一件事有两个可点的动作, 而点角色
    # 按钮时工具已经把这一项标完成了。答完之后它作为已完成行正常显示。
    role_rows = [r for r in rows if _item_id_of(r) == _ROLE_CONFIRMED_ITEM]
    item_rows = [r for r in rows if _item_id_of(r) != _ROLE_CONFIRMED_ITEM]

    if not role_answered:
        elements.append({"tag": "markdown", "content": "**你是不是技术人员？**"})
        elements.extend(_role_button("研发人员", ACTION_ROLE_DEV, "dev"))
        elements.extend(_role_button("非研发人员", ACTION_ROLE_NONDEV, "nondev"))
        elements.append({"tag": "hr"})
        handlers[ACTION_ROLE_DEV] = HANDLER_ROLE
        handlers[ACTION_ROLE_NONDEV] = HANDLER_ROLE
        rows = item_rows
    else:
        # 答完角色: 已完成的 role_confirmed 行摆在最前, 让「这一勾已落地」可见。
        rows = role_rows + item_rows

    rows_elements, rows_handlers = _rows_section(rows, today)
    elements.extend(rows_elements)
    handlers.update(rows_handlers)
    elements.extend(_footer(sop_url))

    template = _card_template(rows, today) if role_answered else "blue"
    return _shell("入职卡 · 开发环境", elements, template), handlers


def role_settled_card(
    is_dev: bool,
    rows: list[dict[str, Any]],
    due_text: str,
    sop_url: str,
    role_confirmed_row: dict[str, Any] | None = None,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """角色已答后的开发环境卡。

    刻意为之: 现在角色与 5 个开发项同在一张卡上(见 role_card), 所以角色被点掉后
    框架已经就地把那个按钮改成「● ~~研发人员~~」, 剩下的开发项按钮原样可点 ——
    正常路径下不需要再发一张卡。本函数保留是为了两种场景:
      1) 非研发: 5 项被标不适用, 需要一张终态卡说明「这部分不用做」;
      2) 非研发→研发 的反悔路径: 原卡上两个角色按钮都已被消费, 5 项刚被复活成
         未完成却没有可点的按钮了, 只能补发一张带按钮的新卡
         (edit_card 不重新注册回调, 编辑出来的按钮全是死的)。
    """
    lead_rows = [role_confirmed_row] if role_confirmed_row else []
    if not is_dev:
        elements: list[dict[str, Any]] = []
        for row in lead_rows:
            row_elements, _action = _row_elements(row, today)
            elements.extend(row_elements)
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": "你选择了「非研发人员」，这部分不需要完成。"})
        return _shell("入职卡 · 开发环境 ✅ 不适用", elements, "grey"), {}
    all_rows = lead_rows + rows
    done = sum(1 for r in all_rows if str(r.get("状态") or "") == _p.STATUS_DONE)
    return role_card(
        due_text,
        dev_rows=all_rows,
        sop_url=sop_url,
        progress_text=f"{done}/{len(all_rows)}",
        role_answered=True,
        today=today,
    )


def remind_card(
    name: str,
    day_index: int,
    progress: Any,
    sop_url: str,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """入职提醒卡 —— 只在第 1、2 天发(见 rookie_sop_remind.decide_remind), 颜色带出紧迫度:
    第 1 天绿、第 2 天(及以后, 若被调用)红。「已完成 N / M 项」下面加一行提醒文案,
    第 2 天的文案更急一些, 但不在卡面上声称「已通知 HR」——那是否属实取决于
    hr_notify_id 是否配置, 卡片本身不该替调用方担保一件它不知道结果的事。
    """
    is_day1 = day_index <= 1
    header = f"入职第 {day_index} 天\n{_progress_bar(f'{progress.done}/{progress.total}')}"
    # 第 2 天的文案点明「今天收尾时仍未完成, HR 会来了解原因」—— 这不是威胁,
    # 而是把既定规则说在前面: 19:00 的异常提醒确实只报第 2 天结束仍未完成的人
    # (见 rookie_sop_digest.active_rookies), 让人知道时间线比事后被问更公平。
    # 措辞刻意用「了解原因」而不是「上报/通报」: 卡住往往有正当理由(等权限、
    # 等人带), HR 要做的是把堵点解开, 不是问责。
    urgency = (
        "🟢 今天是入职第一天，抽空把清单上的事项过一遍～"
        if is_day1
        else (
            "🔴 入职第二天了，清单还没完成，请尽快处理。\n"
            "如果今天收尾时仍未完成，HR 会来了解原因——卡在哪里就直接说，"
            "缺权限、缺人带都能帮你推。"
        )
    )
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": header},
        {"tag": "markdown", "content": urgency},
    ]
    handlers: dict[str, str] = {}

    for label, rows in (("⚠️ 已逾期", progress.overdue), ("📌 今天到期", progress.due_today)):
        if not rows:
            continue
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**{label} {len(rows)} 项**"})
        for row in rows:
            row_elements, action = _row_elements(row, today)
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
    return _shell("入职卡 · 提醒", elements, "green" if is_day1 else "red"), handlers


def hr_feedback_card(name: str, progress: Any, sop_url: str = "") -> tuple[dict[str, Any], dict[str, str]]:
    """入职第 2 天仍未完成时, 单独给 HR 的一张即时反馈卡。

    与 19:00 的异常提醒(digest_card)不同: 那份只报「第 2 天结束仍未完成」的人, 覆盖全体
    新人; 这张是「这个人到第二天还没完成」的单点及时提醒, 只读, 不挂任何回调。
    """
    remaining = max(progress.total - progress.done, 0)
    lines = [f"⚠️ **{name}** 入职第二天了，还有 {remaining}/{progress.total} 项未完成。"]
    for label, rows in (("已逾期", progress.overdue), ("今天到期", progress.due_today)):
        if rows:
            titles = "、".join(str(r.get("项") or "") for r in rows)
            lines.append(f"**{label}**：{titles}")
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(lines)}]
    elements.extend(_footer(sop_url))
    return _shell("入职卡 · 进度提醒", elements, "orange"), {}


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
        elif int(row.get("入职第N天") or 0) >= 2:
            # 第 2 天起还没做完就该提醒 —— 这才是 active_rookies 的入选条件。
            # 原先只看「有没有逾期项」: 清单多数项的 window_days 是 3-7 天, 第 2 天时
            # 大部分项还没到截止日, 于是 0/28 的人也被判成「正常」、卡片是蓝的,
            # HR 完全看不出严重性(用户反馈「2 天后没完成的没有红色提醒」)。
            icon = "🔴"
            tail = f"入职第 {row.get('入职第N天')} 天仍未完成（{row.get('进度')}），需了解原因"
            attention.append(f"{name} 第 {row.get('入职第N天')} 天仍未完成（{row.get('进度')}）")
        else:
            icon = "🕐"
            tail = f"入职第 {row.get('入职第N天')} 天，进行中"
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
    # 有人第 2 天起仍未完成 → 红; 仅有逾期项 → 橙; 都没有 → 蓝。
    # HR 要的是「一眼看出今天有没有人需要我去问」, 所以最严重的那一档必须是红色。
    overdue_only = any(int(r.get("逾期项数") or 0) > 0 for r in overview_rows)
    stalled = any(
        int(r.get("入职第N天") or 0) >= 2
        and str(r.get("状态") or "") != "已出新手村"
        and int(r.get("逾期项数") or 0) == 0
        for r in overview_rows
    )
    template = "red" if stalled else ("orange" if overdue_only else "blue")
    return _shell("新人入职进度提醒", elements, template), {}
