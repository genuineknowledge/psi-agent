"""B-0 — 层来源探针: 每类内容实际从哪个根取到了什么。

方案 B(official/enterprise/users 三层内容)最危险的失败模式不是报错, 而是**静默
只看见一层**: 分层声明了 3 个根, 代码却只读了 1 个, 加载照样成功、日志照样干净,
只是每人的覆盖永远不生效。本文件的判据全部围绕"这种情况能不能被看见"。

关键区分, 每条都对应一个已经烧过的坑:

* ``roots_declared`` 与实际看见的根数分开报 —— 声明 3 个只看见 1 个是**缺陷**,
  声明 3 个看见 3 个而其中两个计数为 0 是**事实**; 混成一个数字就再也分不开。
* 逐层计数报的是**各层各自贡献了多少**(去重前), 不是合并后的结果 —— 只有这样
  "两层各 5 个、其中 2 个同名" 才能与 "两层各 4 个、无同名" 区分开。
* "目录不存在"与"目录存在但空" 必须区分 —— ``_collect_skill_dirs`` 与
  ``load_rules`` 都用 ``suppress(OSError)`` / ``continue`` 把两者压成同一个空列表。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from psi_agent.session import layer_probe


@pytest.fixture
def probe_lines():
    """收 loguru 的 INFO 文本。

    用 ``logger.add`` 而不是 pytest 的 ``caplog``: 本仓库用 loguru, 它默认不走
    stdlib logging, ``caplog`` 会**一条都收不到** —— 断言"报了某行"的用例会红得
    莫名, 而断言"不该报 chosen"的阴性用例反而假绿。与
    ``test_feishu_workspace_drift.py`` 同一写法。
    """
    collected: list[str] = []
    handle = logger.add(lambda m: collected.append(m.record["message"]), level="INFO")
    try:
        yield collected
    finally:
        logger.remove(handle)


def _messages(lines: list[str]) -> list[str]:
    return [m for m in lines if "layer_source" in m]


class TestReport:
    """探针输出的形状 —— 它是判据的载体, 形状变了下游判据就全瞎。"""

    def test_declared_and_seen_are_separate_numbers(self, probe_lines) -> None:
        """声明 3 个而只看见 1 个: 必须两个数字都在, 这正是"分层没落地"的判据。"""
        layer_probe.report("skills", roots_declared=3, per_root=[("official", 12)])
        (line,) = _messages(probe_lines)
        # "1 of 3" 是缺陷信号; 若只报 total 或只报 declared, 这条信息就丢了。
        assert "1 of 3 roots" in line
        assert "12 from" in line

    def test_empty_layer_is_seen_but_counted_zero(self, probe_lines) -> None:
        """声明 3 看见 3、其中两层为 0: 是事实不是缺陷, 与上一条必须长得不一样。"""
        layer_probe.report(
            "skills",
            roots_declared=3,
            per_root=[("official", 12), ("enterprise", 0), ("users", 0)],
        )
        (line,) = _messages(probe_lines)
        assert "3 of 3 roots" in line
        # 逐层计数必须逐层出现, 否则 "enterprise 是空的" 与 "enterprise 没被读"
        # 在日志里再次同形。
        assert "official=12" in line
        assert "enterprise=0" in line
        assert "users=0" in line

    def test_no_root_seen_at_all(self, probe_lines) -> None:
        """一个根都没看见: 报 0 of N 而不是静默不报 —— 静默才是原始缺陷。"""
        layer_probe.report("systems", roots_declared=1, per_root=[])
        (line,) = _messages(probe_lines)
        assert "0 from 0 of 1 roots" in line
        assert "(none)" in line

    def test_single_value_kind_reports_which_layer_won(self, probe_lines) -> None:
        """systems 是单值语义(一个 system.py 赢, 不合并), 故必须报是哪层赢的。"""
        layer_probe.report(
            "systems",
            roots_declared=2,
            per_root=[("official", 1)],
            chosen="official",
        )
        (line,) = _messages(probe_lines)
        assert "chosen=official" in line

    def test_no_winner_when_nothing_was_seen(self, probe_lines) -> None:
        """一个根都没看见时不报 chosen —— 报了就是在说"某层赢了", 而其实什么都没加载。

        调用方传空 chosen 是这条的前提(见 ``_feishu_spec._cached`` /
        ``system_prompt._load_module``): 若哪天有人无条件传层名, "护栏规则来自
        official"与"护栏规则一条都没加载"就会在日志里同形。
        """
        layer_probe.report("feishu_api_rules", roots_declared=1, per_root=[], chosen="")
        (line,) = _messages(probe_lines)
        assert "0 from 0 of 1 roots" in line
        assert "chosen=" not in line

    def test_merge_kind_omits_chosen(self, probe_lines) -> None:
        """合并语义(skills)没有单一赢家, 不该报 chosen —— 报了就是在暗示错的模型。"""
        layer_probe.report("skills", roots_declared=2, per_root=[("official", 3)])
        (line,) = _messages(probe_lines)
        assert "chosen=" not in line

    def test_total_differs_from_per_root_sum_under_override(self, probe_lines) -> None:
        """各层之和 = total: 探针报的是**各层贡献**, 调用方负责传去重前的数。

        这条钉住的是语义而不是算术: 若哪天有人改成传去重后的数, 同名覆盖就再也
        看不出来了(两层各 5 个、合并后 8 个, 与两层各 4 个无覆盖不可区分)。
        """
        layer_probe.report("skills", roots_declared=2, per_root=[("a", 5), ("b", 5)])
        (line,) = _messages(probe_lines)
        assert "10 from 2 of 2 roots" in line


class TestRootName:
    """层名从**声明**来, 不从路径来 —— 与 ``content_roots.py`` 的 layer_id 一致。"""

    def test_declared_name_wins_over_path_basename(self) -> None:
        declared = {"official": Path("X:/mnt/pkg-v3")}
        assert layer_probe.root_name(Path("X:/mnt/pkg-v3"), declared) == "official"

    def test_child_path_resolves_to_its_declared_root(self) -> None:
        """探针常拿到 ``<root>/skills`` 这种子路径, 也必须归到声明的层名上。"""
        declared = {"users": Path("X:/mnt/data")}
        assert layer_probe.root_name(Path("X:/mnt/data/ou_123/skills"), declared) == "users"

    def test_falls_back_to_basename_when_undeclared(self) -> None:
        """没有声明表时退回目录名: 单根阶段(现在)就是这条路径。"""
        assert layer_probe.root_name(Path("X:/mnt/workspace")) == "workspace"

    def test_unrelated_path_does_not_borrow_a_declared_name(self) -> None:
        """不在任何声明根下的路径, 绝不能被误挂到某一层上。"""
        declared = {"official": Path("X:/mnt/pkg")}
        assert layer_probe.root_name(Path("X:/other/place"), declared) == "place"
