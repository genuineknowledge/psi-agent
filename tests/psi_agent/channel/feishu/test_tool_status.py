"""``_tool_status`` 的判据: 白名单别名、兜底、以及并发工具的状态行。

这一层是纯函数 + 一个小状态机, 与飞书 SDK 无关; 卡片上真的出现了什么由
``test_feishu_tool_progress.py`` 盯 —— 那里走 ``_stream_reply`` 与真的
``MarkdownStreamController``。两层各测各的, 不互相替代。
"""

from __future__ import annotations

from pathlib import Path

from psi_agent.channel.feishu._tool_status import (
    GENERIC_TOOL_LABEL,
    TOOL_ALIASES,
    ToolStatusTracker,
    status_line_for,
)
from psi_agent.session.tool_exposure import DISCOVERY_TOOLS, MANIFEST_NAME, parse_manifest_text

_FEISHU_TOOLS = Path(__file__).resolve().parents[4] / "agents" / "feishu" / "tools"


def test_known_tool_maps_to_chinese_alias():
    assert status_line_for(["feishu_doc_read"]) == "⏳ 正在读飞书文档…"


def test_unknown_tool_falls_back_without_leaking_its_name():
    """映射表未命中要走通用兜底, 且**不能**把工具名当文案。

    工具名本身也是信息泄漏线索(内部工具的存在与命名), 且英文名对用户无意义。
    """
    line = status_line_for(["some_internal_probe_v2"]) or ""
    assert line == f"⏳ {GENERIC_TOOL_LABEL}…"
    assert "some_internal_probe_v2" not in line
    assert "probe" not in line


def test_missing_tool_name_also_falls_back():
    """旧流没有 ``tool_name`` 字段 —— 传 ``None`` 不能炸, 也走兜底。"""
    assert status_line_for([None]) == f"⏳ {GENERIC_TOOL_LABEL}…"


def test_concurrent_tools_render_a_count_not_a_list():
    """并发跑多个工具时报个数, 不铺开列名。

    agent 在一个 task group 里并发执行工具, 所以 ``tool_call`` 会连着来好几条。
    铺开列名会让这一行随并发度变长、把卡片挤走, 所以只报「其中一个 + 还有几个」。
    """
    line = status_line_for(["read", "bash", "feishu_doc_read"])
    assert line == "⏳ 正在读取文件…(另有 2 个工具在跑)"


def test_concurrent_unknown_tools_still_leak_nothing():
    line = status_line_for(["secret_tool_a", "secret_tool_b"]) or ""
    assert "secret_tool_a" not in line and "secret_tool_b" not in line
    assert line == f"⏳ {GENERIC_TOOL_LABEL}…(另有 1 个工具在跑)"


def test_no_running_tools_has_no_status_line():
    assert status_line_for([]) is None


def test_alias_table_covers_the_feishu_packs_exposed_tools():
    """被暴露的工具必须全有别名 —— 缺一个就在生产卡片上显示成通用兜底。

    换锚说明: 原先锚在内核常量 ``TMPFIX_M2_CORE_TOOLS`` 上, 那份名单已随分层暴露删除。
    现在锚在飞书包自己声明的暴露清单 (``agents/feishu/tools/EXPOSED.txt``) 上 —— 同一批
    工具, 同一个风险 (模型最可能直接调这些, 缺别名就兜底), 只是名单搬去了它该在的那一层。

    ``DISCOVERY_TOOLS`` 由内核无条件暴露而不写在清单里, 所以要单独并进来一起要求别名:
    它们照样会出现在状态行上。
    """
    exposed = parse_manifest_text((_FEISHU_TOOLS / MANIFEST_NAME).read_text(encoding="utf-8"))
    missing = sorted((exposed | DISCOVERY_TOOLS) - set(TOOL_ALIASES))
    assert missing == [], f"已暴露但缺别名: {missing}"


def test_the_anchor_manifest_is_actually_there():
    """锚必须存在且非空 —— 文件没了会让上面那条判据静默变成「空集全覆盖」。"""
    path = _FEISHU_TOOLS / MANIFEST_NAME
    assert path.is_file(), f"暴露清单不在: {path}"
    assert len(parse_manifest_text(path.read_text(encoding="utf-8"))) > 20


def test_aliases_carry_no_ascii_tool_names():
    """别名是给用户看的中文, 不能是工具名的英文换皮。"""
    leaked = sorted(name for name, alias in TOOL_ALIASES.items() if name.split("_")[-1] in alias)
    assert leaked == []


# -- 状态机 -------------------------------------------------------------------


def test_tracker_tracks_call_then_result():
    t = ToolStatusTracker()
    assert t.on_tool_call("read") == "⏳ 正在读取文件…"
    # 结果回来后这个工具就不在跑了, 状态行随之消失。
    assert t.on_tool_result("read") is None


def test_tracker_holds_line_while_one_of_two_still_runs():
    """并发时先回来的那个不该把状态行整条抹掉 —— 另一个还在跑。"""
    t = ToolStatusTracker()
    t.on_tool_call("read")
    t.on_tool_call("bash")
    assert t.on_tool_result("read") == "⏳ 正在执行命令…"


def test_tracker_result_without_matching_call_does_not_go_negative():
    """结果先到 / 名字对不上时不能把计数带成负数, 否则后续状态行永久错。"""
    t = ToolStatusTracker()
    assert t.on_tool_result("read") is None
    assert t.on_tool_call("bash") == "⏳ 正在执行命令…"


def test_tracker_counts_repeated_calls_of_the_same_tool():
    """同一工具并发两次要算两个 —— 一个结果回来时另一份还在跑。"""
    t = ToolStatusTracker()
    t.on_tool_call("read")
    t.on_tool_call("read")
    assert t.on_tool_result("read") == "⏳ 正在读取文件…"
    assert t.on_tool_result("read") is None
