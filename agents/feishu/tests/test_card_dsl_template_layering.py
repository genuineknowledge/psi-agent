"""card-dsl 模板按内容层就近解析 —— 分层断掉的第 5 处硬编码 skills 路径。

这处的断法与前 4 处不同, 值得单独记一笔。前 4 处都是"目录不存在"才断; 这处**目录在、
只是内容不全**: 原实现是"第一个 ``isdir`` 为真的候选目录就是模板目录", 而生产上
``/workspace/skills/card-dsl/templates`` 确实存在(2026-09-14 14:33 有人往里放了一个
``remind-card.xml``), 于是解析就此终止, ``/content/official`` 下的另外三个模板
(``meeting-summary-card`` / ``review-card`` / ``todo-card``)全部看不见。

判据里用的模板名一律是 ``layer-probe-card`` —— **刻意选一个仓库里不存在的名字**。用真名
(如 ``review-card``)会让 ``__file__`` 兜底那层悄悄接住请求, 于是就算分层解析整个坏掉,
判据照样全绿。这一点是实测撞出来的, 不是预先想到的。

后果是静默的: ``render_template`` 只回一个 "not found", 看着像调用方把模板名写错了。
牵连会议总结卡(``_meeting_card``)、``feishu_card_render``、台账对账(
``feishu_todo_ledger_reconcile``)与评价卡(``_review_card_impl``)。

所以本文件的判据刻意都做成"**近层目录存在但缺这个文件**"的形状 —— 只断言"目录存不存在"
或"找得到就行"会全绿, 抓不到这个缺陷。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

REPO_TEMPLATES = Path(__file__).resolve().parents[1] / "skills" / "card-dsl" / "templates"

# ruff: noqa: PLC0415
# 工具模块必须在 ``sys.path`` 插入之后才导得到, 所以导入留在函数体内。


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """声明 ``official`` + ``enterprise`` 两层, 返回 ``(official, enterprise)`` 的模板目录。

    **agent 根一并挪到 tmp 下**: ``_template_dirs()`` 把 agent 根放在最前(最近), 而仓库里那个
    agent 包真的装着 ``skills/card-dsl/templates``。不挪的话判据会命中仓库那份真文件而不是声明
    的层, "分层生效"这件事根本没被测到。挪开之后的形状也正是生产: ``/workspace`` 那层只有一个
    模板, 其余在 ``/content/official``。
    """
    import _card_dsl

    official = tmp_path / "official" / "skills" / "card-dsl" / "templates"
    enterprise = tmp_path / "enterprise" / "skills" / "card-dsl" / "templates"
    for d in (official, enterprise):
        d.mkdir(parents=True)

    roots = f"official={tmp_path / 'official'}{os.pathsep}enterprise={tmp_path / 'enterprise'}"
    monkeypatch.setenv("PSI_CONTENT_ROOTS", roots)

    empty_agent = tmp_path / "agent"
    (empty_agent / "skills" / "card-dsl" / "templates").mkdir(parents=True)
    monkeypatch.setattr(_card_dsl, "agent_dir", lambda raw="": str(empty_agent))
    return official, enterprise


def _minimal_template(title: str) -> str:
    return f'<card title="{title}"><info label="标记" value="{{mark}}"/></card>'


def test_template_found_in_far_layer_when_near_dir_exists_but_lacks_it(layers: tuple[Path, Path]) -> None:
    """近层目录**存在但没有这个模板**时, 必须继续往远层找。

    这正是生产上的形状, 也是原实现失手的地方: 它认准第一个存在的目录就不再往下看。
    ``enterprise`` 目录由 fixture 建好且为空 —— 判据靠的就是"空目录不能终止解析"。
    """
    official, enterprise = layers
    assert enterprise.is_dir(), "近层目录必须存在, 否则测的是另一种情形(目录缺失)"
    assert not list(enterprise.iterdir()), "近层必须是空的, 这条判据的全部要点在此"
    (official / "layer-probe-card.xml").write_text(_minimal_template("远层"), encoding="utf-8")

    import _card_dsl

    out = _card_dsl.render_template(template_name="layer-probe-card", values_json=json.dumps({"mark": "X"}))
    assert out["ok"] is True, f"近层目录存在就停止解析了 —— 远层模板没找到: {out.get('error')}"
    assert out["card"]["header"]["title"]["content"] == "远层"


def test_nearer_layer_wins_over_far_layer(layers: tuple[Path, Path]) -> None:
    """同名模板在两层都有时, 就近者胜。"""
    official, enterprise = layers
    (official / "layer-probe-card.xml").write_text(_minimal_template("官方"), encoding="utf-8")
    (enterprise / "layer-probe-card.xml").write_text(_minimal_template("企业"), encoding="utf-8")

    import _card_dsl

    out = _card_dsl.render_template(template_name="layer-probe-card", values_json=json.dumps({"mark": "X"}))
    assert out["ok"] is True, out.get("error")
    assert out["card"]["header"]["title"]["content"] == "企业", "声明序里 enterprise 在后=更近, 它该赢"


def test_agent_root_beats_declared_layers(layers: tuple[Path, Path]) -> None:
    """agent 根(``/workspace``)比声明的内容层更近 —— 与 ``layers_for`` 同一口径。"""
    official, _enterprise = layers
    (official / "layer-probe-card.xml").write_text(_minimal_template("官方"), encoding="utf-8")

    import _card_dsl

    agent_templates = Path(_card_dsl.agent_dir()) / "skills" / "card-dsl" / "templates"
    (agent_templates / "layer-probe-card.xml").write_text(_minimal_template("agent"), encoding="utf-8")

    out = _card_dsl.render_template(template_name="layer-probe-card", values_json=json.dumps({"mark": "X"}))
    assert out["ok"] is True, out.get("error")
    assert out["card"]["header"]["title"]["content"] == "agent"


def test_missing_everywhere_reports_the_dirs_it_searched(layers: tuple[Path, Path]) -> None:
    """全层都没有时报错要带上查过的目录。

    分层之后"模板找不到"最常见的原因是投放漏了某一层, 而不是名字写错。报错里没有目录清单,
    人只能去猜是哪一层缺 —— 生产上这一步靠的就是错误消息。
    """
    import _card_dsl

    out = _card_dsl.render_template(template_name="layer-probe-card", values_json=json.dumps({"mark": "X"}))
    assert out["ok"] is False
    assert "layer-probe-card" in out["error"]
    assert "已查" in out["error"], "报错必须列出查过的目录"
    assert str(_card_dsl.agent_dir()) in out["error"], "agent 根那层也要在清单里"


def test_no_declared_roots_falls_back_to_legacy_layout() -> None:
    """未声明分层时行为与改动前一致: 仍从 agent 根 / ``__file__`` 相邻处解析。

    这条守的是"分层是增量、不是替换" —— 单根部署(desktop、本地测试)不能因此坏掉。
    """
    import _card_dsl

    assert os.environ.get("PSI_CONTENT_ROOTS") in (None, ""), "本条判据要求环境里没有声明层"
    dirs = _card_dsl._template_dirs()
    assert len(dirs) == 2, f"未声明层时只该有 agent 根与 __file__ 兜底两个候选, 实得 {dirs}"
    out = _card_dsl.render_template(template_name="remind-card", values_json=json.dumps({}))
    assert out["ok"] is True, f"仓库自带的 remind-card 必须仍能渲染: {out.get('error')}"


def test_repo_templates_are_all_reachable_without_layers() -> None:
    """仓库里现有的每个模板都得能被解析到 —— 防"改完解析逻辑漏掉某个"。"""
    import _card_dsl

    names = sorted(p.stem for p in REPO_TEMPLATES.glob("*.xml"))
    assert names, "仓库里应当有模板, 没有说明路径写错了"
    for name in names:
        assert _card_dsl._resolve_template(name) is not None, f"模板 {name} 解析不到"


def test_template_name_traversal_still_rejected(layers: tuple[Path, Path]) -> None:
    """路径穿越校验不能因为改了解析逻辑而丢掉。

    改动把"拼一个目录"换成了"逐层拼文件", 拼接点变多, 所以这条原有护栏要显式钉住。
    """
    import _card_dsl

    for bad in ("../secret", "a/b", "a\\b", "..", "../../etc/passwd"):
        out = _card_dsl.render_template(template_name=bad, values_json=json.dumps({}))
        assert out["ok"] is False, f"{bad!r} 应当被拒"
        assert out["error"] == "invalid template_name", f"{bad!r} 走到了文件解析: {out['error']}"
