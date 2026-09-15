"""The missing-manifest gate — "no manifest anywhere" must be loud, not silent.

The narrowing mechanism shipped in 2026-09 and never once took effect in
production. It reads ``EXPOSED.txt`` from each layer's tools dir; production had
no such file in any layer (``find -name 'EXPOSED*'`` returned nothing), so every
layer took ``read_manifest``'s ``None`` branch and ``LAYERED`` exposed the full
surface. For weeks every request carried 289774 chars of tool schemas — 47% of
the un-trimmable floor — while the only number in the logs read
``tools_exposed=232 of 232 tier=layered``, which says *the mechanism is on*.

That is the failure these criteria exist for, and it is a reporting failure
rather than a logic one: ``select_exposed`` did exactly what it documents. So
what is pinned here is that the **degraded state announces itself**, and that
the one distinction able to tell "nobody shipped a manifest" apart from "this
layer declares nothing on purpose" survives into the log line. ``/workspace/tools``
is hand-deployed and not in git, so there is no gate upstream of this one.
"""

from __future__ import annotations

from loguru import logger

from psi_agent.session.tool_exposure import (
    MANIFEST_NAME,
    ExposureTier,
    report_manifests,
)


class _Captured:
    """收 loguru 的日志文本与级别。

    用 ``logger.add`` 而不是 pytest 的 ``caplog``: 本仓库用 loguru, 它默认不走 stdlib
    logging, ``caplog`` 会**一条都收不到** —— 而本文件里断言「不该报警」的阴性用例在那种
    情况下照样绿, 是假阴性。写这份判据时就先踩了一次: 五条全红才发现收不到。与
    ``gateway/test_feishu_workspace_drift.py`` 同一写法。
    """

    def __init__(self, level: str = "DEBUG") -> None:
        self.records: list[tuple[str, str]] = []
        self._handle = logger.add(
            lambda m: self.records.append((m.record["level"].name, m.record["message"])),
            level=level,
        )

    def stop(self) -> None:
        logger.remove(self._handle)

    @property
    def text(self) -> str:
        return "\n".join(msg for _, msg in self.records)

    @property
    def warnings(self) -> list[str]:
        return [msg for level, msg in self.records if level == "WARNING"]


def _report(manifests: dict[str, frozenset[str] | None], tier: ExposureTier) -> _Captured:
    cap = _Captured()
    try:
        report_manifests(manifests, tier=tier)
    finally:
        cap.stop()
    return cap


def test_no_manifest_in_any_layer_warns() -> None:
    """The production state. Under LAYERED this degrades to full exposure, so the
    line has to carry a WARNING — an INFO here is what let it run for weeks."""
    cap = _report({"agent": None}, ExposureTier.LAYERED)
    assert cap.warnings, f"没有清单时不报 WARNING —— 这正是生产静默全放过的原因: {cap.records}"
    assert MANIFEST_NAME in cap.text, f"警告没点名要找的文件, 读日志的人不知道该投什么: {cap.text}"
    assert "no-op" in cap.text, f"警告没说清后果是收窄根本没生效: {cap.text}"


def test_a_layer_declaring_nothing_is_not_reported_as_missing() -> None:
    """``None`` vs ``frozenset()``: an empty manifest is a choice ("expose none of
    mine"), a missing one may be a forgotten deploy. Collapsed into a count they
    are indistinguishable, and the forgetting is the failure that happened."""
    cap = _report({"official": frozenset()}, ExposureTier.LAYERED)
    assert "undeclared" not in cap.text, f"空清单被报成了「没有清单」, 两者混掉就再分不开: {cap.text}"
    assert "official=0" in cap.text, f"声明了 0 个应当如实报成 0: {cap.text}"


def test_the_line_separates_declaring_layers_from_undeclared_ones() -> None:
    """A mixed ladder is the real shape: the shared roots declare, the agent layer
    may not. Per-layer detail is what makes "which one is missing" answerable by
    grep instead of by guessing."""
    cap = _report(
        {"official": frozenset({"bash"}), "users": None, "agent": frozenset({"bash", "read"})},
        ExposureTier.LAYERED,
    )
    assert "2 of 3" in cap.text, f"没报出「3 层里 2 层声明了」: {cap.text}"
    assert "users=undeclared" in cap.text, f"没点名是哪一层缺清单: {cap.text}"
    assert "agent=2" in cap.text and "official=1" in cap.text, f"每层声明数没如实报出: {cap.text}"


def test_off_tier_says_narrowing_is_disabled() -> None:
    """Under OFF the absence of manifests is irrelevant, so warning about it would
    be noise that trains readers to ignore the line that matters."""
    cap = _report({"agent": None}, ExposureTier.OFF)
    assert "disabled" in cap.text, f"OFF 档该明说收窄是关着的: {cap.text}"
    assert not cap.warnings, f"OFF 档不该为缺清单报警: {cap.warnings}"


def test_declared_tier_with_no_manifest_still_warns() -> None:
    """DECLARED with no manifest is the opposite degradation — it would strip the
    array to the discovery hatch alone. Also worth a warning, different wording:
    calling that a "no-op" would be wrong."""
    cap = _report({"agent": None}, ExposureTier.DECLARED)
    assert cap.warnings, f"DECLARED 档缺清单同样是异常态, 不能不报: {cap.records}"
    assert "no-op" not in cap.text, f"DECLARED 档缺清单不是「没生效」而是「几乎全砍」, 措辞不能照抄: {cap.text}"
