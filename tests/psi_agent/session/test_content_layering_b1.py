"""B-1 判据 —— skills / triggers / systems 按声明顺序查多个根。

B-0 建了探针(每类内容报一行"从几个根取到了什么"), B-1 让查找**真的**跨根。所以本
文件的判据分两类, 缺一类都不成立:

1. **行为**: 近的层真的赢了(nearest-wins), 远的层真的还在被读。
2. **可观测**: 探针那行真的从 ``1 of 1`` 变成 ``N of M``。

两类必须成对: 只验行为, 则"分层生效但探针瞎了"照样绿, 而探针是 B-3 上线后唯一的
核验手段; 只验探针, 则"日志报 3 of 3 而实际只读了一层"照样绿 —— 后者正是本卡最贵的
失败方向。

三条已定规则在这里的落点:

* **同名整体覆盖**, 不做字段级 merge —— 字段级会产出谁都没写过的第三份内容。
  ``test_override_replaces_whole_entry`` 用"近层缺某字段"钉住: 字段级 merge 会把远层
  那个字段补进来, 整体覆盖不会。
* **systems 是单值**(一个 system.py 赢, 不合并), 与 triggers/skills 的合并语义不同,
  故判据形状也不同 —— 报 ``chosen``。
* 派生 / 墓碑属 B-2, 本文件不测, 但 ``test_lower_layer_still_reported_under_override``
  钉住"被覆盖的那层计数仍如实上报", 这是 B-2 能看见自己在覆盖谁的前提。

单根(未设 ``PSI_CONTENT_ROOTS``)必须逐字不变: ``TestSingleRootUnchanged``。它验的不是
"能跑", 而是**探针那行的形状**也不变 —— 现有 B-0 判据建立在那个形状上。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from loguru import logger

from psi_agent.session import content_roots as cr
from psi_agent.session.content_roots import AGENT_ROOT_NAME, ContentRoot
from psi_agent.session.system_prompt import SystemPrompt
from psi_agent.session.trigger_registry import TriggerRegistry


@pytest.fixture
def probe_lines():
    """收 loguru 的 INFO 文本。

    ``logger.add`` 而不是 pytest 的 ``caplog``: 本仓库用 loguru, 它默认不走 stdlib
    logging, ``caplog`` 一条都收不到 —— 断言"报了某行"会莫名红, 而断言"不该报"的
    阴性用例反而假绿。与 ``test_layer_probe.py`` 同一写法。
    """
    collected: list[str] = []
    handle = logger.add(lambda m: collected.append(m.record["message"]), level="INFO")
    try:
        yield collected
    finally:
        logger.remove(handle)


def _probe(lines: list[str], kind: str) -> str:
    """探针里 *kind* 那一行(唯一)。多于一行即为缺陷: 一类内容报两次会让计数翻倍。"""
    matched = [m for m in lines if "layer_source" in m and f" {kind}: " in m]
    assert len(matched) == 1, f"expected exactly one {kind} probe line, got {matched}"
    return matched[0]


def _write_trigger(root: Path, name: str, *, event: str = "demo.event", extra: str = "") -> None:
    """一个最小可加载的 TRIGGER.md。*extra* 追加 frontmatter 行。"""
    target = root / "triggers" / name
    target.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(f"""\
        ---
        name: {name}
        event: {event}
        {extra}
        ---
        body of {name}
        """)
    (target / "TRIGGER.md").write_text(body, encoding="utf-8")


def _write_system(root: Path, marker: str) -> None:
    """一个 system.py, ``system_prompt_builder`` 返回 *marker*。

    返回值带 marker 是关键: 只断言"加载到了某个 system.py"会被 ``sys.path`` 上另一份
    同名模块假绿(已踩过, 见 ``a1-layer-fallback-follows-open-order``), 必须**调用**
    它并比对返回内容。
    """
    target = root / "systems"
    target.mkdir(parents=True, exist_ok=True)
    (target / "system.py").write_text(
        textwrap.dedent(f"""\
            async def system_prompt_builder():
                return {marker!r}
            """),
        encoding="utf-8",
    )


def _roots(*pairs: tuple[str, Path]) -> list[ContentRoot]:
    """``(名字, 路径)`` 按给定顺序升序建根 —— 最后一个最近。"""
    return [ContentRoot(name=name, path=path, priority=i * 10) for i, (name, path) in enumerate(pairs)]


class TestTriggersAcrossRoots:
    """triggers: 合并语义, 同名整体覆盖。"""

    @pytest.mark.anyio
    async def test_merges_entries_from_every_root(self, tmp_path: Path, probe_lines) -> None:
        """两层各有各的 trigger: 两个都在, 且探针报 2 of 2。

        判据同时压住行为和可观测: 只数 registry 里几个会漏掉"探针瞎了", 只看日志会漏掉
        "日志对而加载错"。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_trigger(official, "from-official")
        _write_trigger(users, "from-users")

        registry = await TriggerRegistry.load_content_roots(_roots(("official", official), ("users", users)))

        assert sorted(t.name for t in registry.triggers) == ["from-official", "from-users"]
        line = _probe(probe_lines, "triggers")
        assert "2 from 2 of 2 roots" in line
        assert "official=1" in line
        assert "users=1" in line

    @pytest.mark.anyio
    async def test_nearest_root_wins_same_name(self, tmp_path: Path, probe_lines) -> None:
        """同名: 近的(users)赢。方向错了这条就红 —— 这是 nearest-wins 的方向判据。"""
        official, users = tmp_path / "official", tmp_path / "users"
        _write_trigger(official, "shared", event="official.event")
        _write_trigger(users, "shared", event="users.event")

        registry = await TriggerRegistry.load_content_roots(_roots(("official", official), ("users", users)))

        # 一条, 不是两条: 身份是**名字**。若按路径去重, 两份都会活下来并各自触发一次
        # —— 覆盖会静默变成"多触发一次", 朝着最贵的方向错。
        assert len(registry.triggers) == 1
        assert registry.triggers[0].event == "users.event"

    @pytest.mark.anyio
    async def test_lower_layer_still_reported_under_override(self, tmp_path: Path, probe_lines) -> None:
        """被覆盖的那层, 计数仍如实上报 —— 否则"覆盖发生了"这件事在日志里看不见。

        探针报**各层贡献**(去重前), 所以 total 大于合并后的条数。若哪天有人改成报去重
        后的数, "两层各 1 个且同名"就与"只有近层 1 个"同形, 而这两件事的运维含义完全
        不同(后者意味着官方层根本没被读到)。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_trigger(official, "shared", event="official.event")
        _write_trigger(users, "shared", event="users.event")

        registry = await TriggerRegistry.load_content_roots(_roots(("official", official), ("users", users)))

        assert len(registry.triggers) == 1
        line = _probe(probe_lines, "triggers")
        assert "official=1" in line
        assert "users=1" in line
        assert "2 from 2 of 2 roots" in line

    @pytest.mark.anyio
    async def test_override_replaces_whole_entry(self, tmp_path: Path) -> None:
        """整体覆盖, 不是字段级 merge。

        远层的 trigger 带 ``source: feishu``, 近层同名那份**不带**。字段级 merge 会把
        ``source`` 补进来; 整体覆盖后它必须是空 —— "谁都没写过的第三份内容"就是这样
        产生的。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_trigger(official, "shared", extra="source: feishu")
        _write_trigger(users, "shared")

        registry = await TriggerRegistry.load_content_roots(_roots(("official", official), ("users", users)))

        (trigger,) = registry.triggers
        assert trigger.source == "", "字段级 merge 把远层的 source 带过来了"

    @pytest.mark.anyio
    async def test_missing_layer_differs_from_empty_layer(self, tmp_path: Path, probe_lines) -> None:
        """ "层不存在" 与 "层存在但空" 输出不同形 —— B-0 探针存在的原始动机。

        本卡不许把它退化掉: 前者该看不见这层(2 of 3), 后者该看见且计数为 0(``users=0``)。
        混成一个数字就再也分不开"分层没落地"与"这层确实没内容"。
        """
        official, empty, absent = tmp_path / "official", tmp_path / "empty", tmp_path / "absent"
        _write_trigger(official, "only")
        (empty / "triggers").mkdir(parents=True)  # 存在但空

        registry = await TriggerRegistry.load_content_roots(
            _roots(("official", official), ("empty", empty), ("absent", absent))
        )

        assert [t.name for t in registry.triggers] == ["only"]
        line = _probe(probe_lines, "triggers")
        # 声明 3 个, 看见 2 个(absent 那层连目录都没有)。
        assert "1 from 2 of 3 roots" in line
        assert "empty=0" in line, "存在但空的层必须被看见并报 0"
        assert "absent" not in line, "不存在的层不该出现在逐层计数里"

    @pytest.mark.anyio
    async def test_refresh_targets_the_writable_top_root(self, tmp_path: Path) -> None:
        """``work_dir`` 指最近的根 —— 它是唯一用户可写的那层。

        指错了会让 ``refresh()`` 去重载只读的官方层: 用户新建的 trigger 永远不出现,
        且不报错。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_trigger(official, "a")
        _write_trigger(users, "b")

        registry = await TriggerRegistry.load_content_roots(_roots(("official", official), ("users", users)))

        assert registry._work_dir == users / "triggers"


class TestSystemsSingleValue:
    """systems: **单值** —— 一个 system.py 赢, 不合并。"""

    @pytest.mark.anyio
    async def test_nearest_root_with_a_module_wins(self, tmp_path: Path, probe_lines) -> None:
        """两层都有 system.py: 近的赢, 且 ``chosen`` 报的就是它。

        判据落在**调用 builder 拿返回值**上, 不是"加载成功": 只比对 marker 存在与否会被
        ``sys.path`` 上另一份同名模块假绿(见 ``a1-layer-fallback-follows-open-order``)。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_system(official, "OFFICIAL_SYSTEM")
        _write_system(users, "USERS_SYSTEM")

        prompt = await SystemPrompt.from_content_roots(
            _roots(("official", official), ("users", users)), session_id="s1"
        )

        assert await prompt._builder() == "USERS_SYSTEM"
        line = _probe(probe_lines, "systems")
        assert "chosen=users" in line

    @pytest.mark.anyio
    async def test_falls_through_to_a_lower_root(self, tmp_path: Path, probe_lines) -> None:
        """近层没有 system.py 时, 逐层往下找 —— 不是"近层没有就没有"。"""
        official, users = tmp_path / "official", tmp_path / "users"
        _write_system(official, "OFFICIAL_SYSTEM")
        (users / "systems").mkdir(parents=True)  # 有目录, 没 system.py

        prompt = await SystemPrompt.from_content_roots(
            _roots(("official", official), ("users", users)), session_id="s2"
        )

        assert await prompt._builder() == "OFFICIAL_SYSTEM"
        line = _probe(probe_lines, "systems")
        assert "chosen=official" in line

    @pytest.mark.anyio
    async def test_lower_module_still_reported_when_shadowed(self, tmp_path: Path, probe_lines) -> None:
        """被遮蔽的官方 system.py 仍如实上报 —— 回退时要问的正是"官方那份还在不在"。

        单值语义下很容易写成"找到第一个就 return", 那样下层有没有 system.py 就不可见了。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_system(official, "OFFICIAL_SYSTEM")
        _write_system(users, "USERS_SYSTEM")

        await SystemPrompt.from_content_roots(_roots(("official", official), ("users", users)), session_id="s3")

        line = _probe(probe_lines, "systems")
        assert "official=1" in line
        assert "users=1" in line
        assert "2 from 2 of 2 roots" in line

    @pytest.mark.anyio
    async def test_agent_path_follows_the_winning_root(self, tmp_path: Path) -> None:
        """``agent_path`` 是**赢的那层**, 不是最近的层。

        hooks 用它解析 ``SOUL.md`` / ``USER.md``(``_agent_kwargs``)。指成最近的层会让
        官方 system.py 去读个人层的身份文件 —— 静默串层。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_system(official, "OFFICIAL_SYSTEM")
        (users / "systems").mkdir(parents=True)

        prompt = await SystemPrompt.from_content_roots(
            _roots(("official", official), ("users", users)), session_id="s4"
        )

        assert prompt._agent_path == official

    @pytest.mark.anyio
    async def test_no_module_anywhere_reports_no_winner(self, tmp_path: Path, probe_lines) -> None:
        """逐层都没有: 报 0 of N 且**不报 chosen** —— 报了就是在说"某层赢了"。"""
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()

        prompt = await SystemPrompt.from_content_roots(_roots(("official", a), ("users", b)), session_id="s5")

        assert await prompt._builder() == ""  # 默认空 prompt
        line = _probe(probe_lines, "systems")
        assert "0 from 0 of 2 roots" in line
        assert "chosen=" not in line


class TestLadderConstruction:
    """阶梯构造 —— 四类内容共用一份, 免得悄悄分岔。"""

    def test_agent_root_goes_on_top(self, tmp_path: Path) -> None:
        """agent 包是最近的一层, 且它的层名是**名字**不是路径。

        原来 ``agent.py`` 用 ``str(agent_root.resolve())`` 当名字, 那会每个 workspace 各
        铸一个 layer id, 正好废掉 ``layer_id`` 引入时要省的那次编译。
        """
        roots = cr.parse_content_roots(f"official={tmp_path / 'o'}")
        ladder = cr.roots_with_agent_top(tmp_path / "agent", roots)

        assert [r.name for r in ladder] == ["official", AGENT_ROOT_NAME]
        assert ladder[-1].priority > ladder[0].priority

    def test_unset_env_yields_agent_root_alone(self, tmp_path: Path) -> None:
        """未声明任何根 → 单级阶梯, 就是原来那一个目录。单根不变的结构前提。"""
        ladder = cr.roots_with_agent_top(tmp_path / "agent", [])

        assert len(ladder) == 1
        assert ladder[0].path == tmp_path / "agent"

    def test_agent_root_already_declared_is_not_duplicated(self, tmp_path: Path) -> None:
        """agent 根已在声明里: 不追加第二份, 保留声明的名字与排位。

        重复追加会让同一目录报成两层、计数翻倍, "覆盖了几个"这个数字随之失去意义;
        更糟的是同一份字节拿到两个 layer id, 又回到缓存分裂。
        """
        agent = tmp_path / "users"
        roots = cr.parse_content_roots(f"official={tmp_path / 'o'}{__import__('os').pathsep}users={agent}")
        ladder = cr.roots_with_agent_top(agent, roots)

        assert [r.name for r in ladder] == ["official", "users"]


class TestSingleRootUnchanged:
    """未设 ``PSI_CONTENT_ROOTS`` 时行为与日志形状完全不变。

    B-0 的判据建立在那个形状上, 换了词或换了数字都会让下游判据假红/假绿。
    """

    @pytest.mark.anyio
    async def test_trigger_single_root_probe_shape(self, tmp_path: Path, probe_lines) -> None:
        """单目录 ``load()``: 仍报 1 of 1, 层名仍是目录名。"""
        _write_trigger(tmp_path, "only")

        registry = await TriggerRegistry.load(tmp_path / "triggers")

        assert [t.name for t in registry.triggers] == ["only"]
        line = _probe(probe_lines, "triggers")
        assert "1 from 1 of 1 roots" in line
        assert "triggers=1" in line

    @pytest.mark.anyio
    async def test_system_single_root_probe_shape(self, tmp_path: Path, probe_lines) -> None:
        """单根 ``from_workspace()``: 仍报 1 of 1 且 ``chosen`` 是目录名。"""
        _write_system(tmp_path, "ONLY_SYSTEM")

        prompt = await SystemPrompt.from_workspace(tmp_path, session_id="s6")

        assert await prompt._builder() == "ONLY_SYSTEM"
        line = _probe(probe_lines, "systems")
        assert "1 from 1 of 1 roots" in line
        assert f"chosen={tmp_path.name}" in line

    @pytest.mark.anyio
    async def test_one_root_ladder_reports_one_of_one(self, tmp_path: Path, probe_lines) -> None:
        """走分层入口但只有一个根: 报 1 of 1 —— 与单根路径同形, 不多不少。

        这条钉住"分层代码在位但未声明内容根"这个生产将要处在的状态(B-1 上线、B-3 未上)。
        """
        _write_trigger(tmp_path, "only")
        ladder = cr.roots_with_agent_top(tmp_path, [])

        registry = await TriggerRegistry.load_content_roots(ladder)

        assert [t.name for t in registry.triggers] == ["only"]
        line = _probe(probe_lines, "triggers")
        assert "1 from 1 of 1 roots" in line
