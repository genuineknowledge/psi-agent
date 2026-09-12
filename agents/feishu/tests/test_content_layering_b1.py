"""B-1 判据(飞书侧) —— skills 目录的**两个消费者**都跟着分层。

skills 目录被两处消费, 语义完全不同:

1. 提示词里的技能索引(``system._build_skills_index``)—— 模型**读**的;
2. 飞书 API 护栏规则(``_feishu_spec.rules_for_layers``)—— 真实调用时**执行**的。

只让前者分层是本卡最贵的失败方向: 每人的覆盖在索引里*看得见*, 于是看起来生效了, 而
真实 API 调用仍只受官方规则约束 —— 该拒的没拒, 且全程无报错。故护栏必须有自己的判据,
且判据必须落在它声称的那一层: 断言 ``rules_for_layers`` 的返回、以及"请求根本没被发出",
而不是断言索引里有那个名字(那测的是消费者 1)。

``TestGuardrailLayering`` 里那条 ``test_personal_rule_actually_refuses_the_call`` 是这组
的核心: 它走完整的 ``call_api_impl``, 用 ``_Recorder`` 证明违规调用**一个请求都没发出**。
只验 ``rules_for_layers`` 返回了个人层那条, 不足以证明它真的吃劲。
"""

from __future__ import annotations

import importlib
import os
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from loguru import logger

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

SYSTEMS_DIR = Path(__file__).resolve().parents[1] / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

_spec: Any = importlib.import_module("_feishu_spec")
_api: Any = importlib.import_module("_feishu_api_impl")
_impl: Any = importlib.import_module("_feishu_impl")
_system: Any = importlib.import_module("system")

from psi_agent.session.content_roots import CONTENT_ROOTS_ENV  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    """护栏缓存按阶梯做键, 用例之间必须清 —— 否则上一条的阶梯会喂给下一条。"""
    _spec.reset_cache()
    yield
    _spec.reset_cache()


@pytest.fixture
def probe_lines():
    """收 loguru 的 INFO 文本(本仓库用 loguru, ``caplog`` 收不到)。"""
    collected: list[str] = []
    handle = logger.add(lambda m: collected.append(m.record["message"]), level="INFO")
    try:
        yield collected
    finally:
        logger.remove(handle)


def _probe(lines: list[str], kind: str) -> str:
    matched = [m for m in lines if "layer_source" in m and f" {kind}: " in m]
    assert len(matched) == 1, f"expected exactly one {kind} probe line, got {matched}"
    return matched[0]


class _Recorder:
    """记下每一个交给 ``_invoke`` 的请求 —— "被拒绝"因此是可证的(零请求)。"""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        return {"ok": True, "data": {}}


def _write_skill(root: Path, name: str, *, rules: str = "", description: str = "") -> None:
    """``<root>/skills/<name>/SKILL.md``, 可选带 ``rules`` 块。"""
    target = root / "skills" / name
    target.mkdir(parents=True, exist_ok=True)
    front = textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description or name}
        ---
        """)
    block = f"\n```rules\n{textwrap.dedent(rules)}```\n" if rules else ""
    (target / "SKILL.md").write_text(front + block, encoding="utf-8")


def _declare_roots(monkeypatch: pytest.MonkeyPatch, *roots: tuple[str, Path]) -> None:
    """设 ``PSI_CONTENT_ROOTS``, 按给定顺序(先声明的最远)。"""
    monkeypatch.setenv(CONTENT_ROOTS_ENV, os.pathsep.join(f"{name}={path}" for name, path in roots))


class TestSkillsIndexLayering:
    """消费者 1 —— 提示词里的技能索引。"""

    def test_index_merges_across_declared_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_lines
    ) -> None:
        """声明 2 个内容根 + agent 根: 三层的 skill 都进索引, 探针报 3 of 3。

        ``1 of 1`` 没变就是没落地 —— 这条同时钉住行为(名字都在)与可观测(数字变了)。
        """
        official, users, agent = tmp_path / "official", tmp_path / "users", tmp_path / "agent"
        _write_skill(official, "official-skill")
        _write_skill(users, "users-skill")
        _write_skill(agent, "agent-skill")
        _declare_roots(monkeypatch, ("official", official), ("users", users))
        # global 根指向一个不存在的目录, 免得开发机上真的 ~/.agent/skills 混进来。
        monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(tmp_path / "no-global")))

        xml = anyio.run(lambda: _system._build_skills_index(anyio.Path(str(agent))))

        assert "official-skill" in xml
        assert "users-skill" in xml
        assert "agent-skill" in xml
        line = _probe(probe_lines, "skills")
        # 声明 2 + agent 1 + global 1 = 4 声明; global 目录不存在故只看见 3。
        assert "3 from 3 of 4 roots" in line
        assert "official=1" in line
        assert "users=1" in line

    def test_nearest_root_wins_same_skill_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_lines
    ) -> None:
        """同名 skill 两层都有: 近的赢, 且被覆盖那层的计数仍如实上报。

        覆盖行为得看得见 —— 各层贡献之和(2)大于合并后的条数(1), 这个差就是"发生了覆盖"。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(official, "shared", description="OFFICIAL_VERSION")
        _write_skill(users, "shared", description="USERS_VERSION")
        _declare_roots(monkeypatch, ("official", official))
        monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(tmp_path / "no-global")))

        xml = anyio.run(lambda: _system._build_skills_index(anyio.Path(str(users))))

        assert "USERS_VERSION" in xml
        assert "OFFICIAL_VERSION" not in xml
        line = _probe(probe_lines, "skills")
        # 各层贡献之和是 2(official 1 + agent 1), 而合并后只有 1 个 skill —— 这个差就是
        # "发生了同名覆盖"。声明 3 个(global + official + agent), global 目录不存在故只
        # 看见 2 个。
        assert "2 from 2 of 3 roots" in line, "各层贡献必须报去重前的数, 否则覆盖看不见"
        assert "official=1" in line

    def test_missing_layer_differs_from_empty_layer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_lines
    ) -> None:
        """ "层不存在" 与 "层存在但空" 不同形 —— B-0 的原始动机, B-1 不许退化掉。"""
        official, empty, agent = tmp_path / "official", tmp_path / "empty", tmp_path / "agent"
        _write_skill(official, "only")
        (empty / "skills").mkdir(parents=True)  # 存在但空
        _write_skill(agent, "mine")
        _declare_roots(monkeypatch, ("official", official), ("empty", empty), ("absent", tmp_path / "absent"))
        monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(tmp_path / "no-global")))

        anyio.run(lambda: _system._build_skills_index(anyio.Path(str(agent))))

        line = _probe(probe_lines, "skills")
        assert "empty=0" in line, "存在但空的层必须被看见并报 0"
        assert "absent" not in line, "不存在的层不该出现在逐层计数里"

    def test_snapshot_manifest_carries_layer_identity(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """快照清单带层名 —— 否则"同名同字节但换了层"命中旧快照。

        造法: 先只有官方层, 建立快照; 再在个人层放一份**逐字相同**的同名 skill。清单只记
        sha256 时两次的 manifest 完全相同, 快照命中, 而"哪一层赢了"已经变了 —— B-2 的派生
        要把来源层写进 frontmatter, 缓存认不出层变化就会渲染上一次的索引。
        """
        official, agent = tmp_path / "official", tmp_path / "agent"
        _write_skill(official, "shared", description="SAME_BYTES")
        agent.mkdir(parents=True, exist_ok=True)
        _declare_roots(monkeypatch, ("official", official))
        monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(tmp_path / "no-global")))

        first = anyio.run(lambda: _system._build_skills_index(anyio.Path(str(agent))))
        assert "SAME_BYTES" in first

        # 个人层放一份逐字相同的副本 —— 字节没变, 赢的那层变了。
        _write_skill(agent, "shared", description="SAME_BYTES")
        snapshot = agent / _system._SKILLS_SNAPSHOT_FILE
        before = snapshot.read_text(encoding="utf-8") if snapshot.exists() else ""

        anyio.run(lambda: _system._build_skills_index(anyio.Path(str(agent))))

        after = snapshot.read_text(encoding="utf-8")
        assert before != after, "层变了而清单没变: 快照认不出层身份"
        assert _system._AGENT_ROOT_NAME in after

    def test_unreadable_layer_warns_instead_of_looking_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """目录存在但读不了: 告警, 不再与"这层本来就没内容"同形。

        ``_collect_skill_dirs`` 原来用 ``suppress(OSError)`` 把两者压成同一个空列表 ——
        这正是 ``layer_probe`` 模块 docstring 点名的两个坑之一。
        """
        skills = tmp_path / "broken" / "skills"
        skills.mkdir(parents=True)

        async def _boom(self: anyio.Path) -> AsyncIterator[anyio.Path]:
            raise OSError("permission denied")
            yield  # pragma: no cover — 让它是个 async generator

        monkeypatch.setattr(anyio.Path, "iterdir", _boom, raising=True)
        with caplog.at_level("WARNING"):
            found = anyio.run(lambda: _system._collect_skill_dirs(anyio.Path(str(skills))))

        assert found == []
        assert any("could not be read" in r.message for r in caplog.records), "读不了必须告警, 不能静默返回空"

    def test_single_root_index_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_lines) -> None:
        """未设 ``PSI_CONTENT_ROOTS``: 仍是 global + agent 两根, 探针形状不变。"""
        agent = tmp_path / "agent"
        _write_skill(agent, "mine")
        monkeypatch.delenv(CONTENT_ROOTS_ENV, raising=False)
        monkeypatch.setattr(_system, "_GLOBAL_AGENT_SKILLS_DIR", anyio.Path(str(tmp_path / "no-global")))

        xml = anyio.run(lambda: _system._build_skills_index(anyio.Path(str(agent))))

        assert "mine" in xml
        line = _probe(probe_lines, "skills")
        assert "1 from 1 of 2 roots" in line
        assert f"{agent.name}=1" in line, "单根下层名仍是目录名"


_STRICT_RULE = """\
- endpoint: GET /open-apis/demo/list
  fields:
    page_size: {max: 10}
"""

_LOOSE_RULE = """\
- endpoint: GET /open-apis/demo/list
  fields:
    page_size: {max: 500}
"""


class TestGuardrailLayering:
    """消费者 2 —— 真实飞书 API 调用的护栏。判据落在**调用被拒**上。"""

    def test_personal_rule_wins_over_official(self, tmp_path: Path) -> None:
        """``rules_for_layers`` 返回个人层那条, 不是官方那条。"""
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(official, "feishu-demo", rules=_LOOSE_RULE)
        _write_skill(users, "feishu-demo-personal", rules=_STRICT_RULE)

        rule = _spec.rules_for_layers(
            [("official", official / "skills"), ("users", users / "skills")],
            "GET",
            "/open-apis/demo/list",
        )

        assert rule is not None
        assert rule.fields["page_size"]["max"] == 10, "个人层没赢: 护栏没跟着分层"

    def test_personal_rule_actually_refuses_the_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """个人层的更严规则**真的**拦住调用 —— 零请求发出。

        这条是本文件的核心。只验 ``rules_for_layers`` 的返回值等于验了"表读对了", 而 1.4
        担心的失败是"表读对了但没执行"。用 ``_Recorder`` 断言一个请求都没发出, 判据才落在
        它声称的那一层。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(official, "feishu-demo", rules=_LOOSE_RULE)
        _write_skill(users, "feishu-demo-personal", rules=_STRICT_RULE)
        recorder = _Recorder()
        monkeypatch.setattr(_impl, "_invoke", recorder)
        monkeypatch.setattr(
            _api,
            "_skills_ladder",
            lambda: [("official", str(official / "skills")), ("users", str(users / "skills"))],
        )

        res = anyio.run(
            lambda: _api.call_api_impl(method="GET", uri="/open-apis/demo/list", query_json='{"page_size": 50}')
        )

        assert res.get("ok") is False, f"个人层的 max:10 没吃劲: {res}"
        assert recorder.requests == [], "被拒的调用竟然发出了请求"

    def test_official_rule_still_applies_where_personal_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """个人层没说话的 endpoint, 官方规则仍然吃劲 —— 分层不是"个人层顶替官方层"。

        与上一条成对: 只验"近的赢"会让"近层一存在就把官方全部丢掉"的实现照样绿, 而那是
        护栏整体失效, 比覆盖不生效更贵。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(official, "feishu-demo", rules=_STRICT_RULE)
        _write_skill(users, "feishu-mine", rules="- endpoint: GET /open-apis/other/thing\n  token: user\n")
        recorder = _Recorder()
        monkeypatch.setattr(_impl, "_invoke", recorder)
        monkeypatch.setattr(
            _api,
            "_skills_ladder",
            lambda: [("official", str(official / "skills")), ("users", str(users / "skills"))],
        )

        res = anyio.run(
            lambda: _api.call_api_impl(method="GET", uri="/open-apis/demo/list", query_json='{"page_size": 50}')
        )

        assert res.get("ok") is False, "官方规则被个人层的存在整体抹掉了"
        assert recorder.requests == []

    def test_guardrail_probe_reports_every_root(self, tmp_path: Path, probe_lines) -> None:
        """护栏那行探针也要报 N of M —— 单根恒 1 of 1 就是"护栏没跟着分层"的判据。"""
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(official, "feishu-demo", rules=_LOOSE_RULE)
        _write_skill(users, "feishu-mine", rules=_STRICT_RULE)

        _spec.rules_for_layers(
            [("official", official / "skills"), ("users", users / "skills")],
            "GET",
            "/open-apis/demo/list",
        )

        line = _probe(probe_lines, "feishu_api_rules")
        assert "2 of 2 roots" in line
        assert "official=1" in line
        assert "users=1" in line

    def test_cache_key_is_the_whole_ladder(self, tmp_path: Path) -> None:
        """缓存按**整条阶梯**做键, 不是按某一个目录。

        原来 ``lru_cache`` 按目录字符串做键。分层后答案是"每个根 + 顺序"的函数: 两条阶梯
        共用同一个顶层目录时, 按目录做键会把一条的规则喂给另一条。
        """
        official, users, agent = tmp_path / "official", tmp_path / "users", tmp_path / "agent"
        _write_skill(official, "feishu-demo", rules=_LOOSE_RULE)
        _write_skill(users, "feishu-demo", rules=_STRICT_RULE)
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "skills").mkdir(parents=True, exist_ok=True)

        loose = _spec.rules_for_layers(
            [("official", official / "skills"), ("agent", agent / "skills")], "GET", "/open-apis/demo/list"
        )
        strict = _spec.rules_for_layers(
            [("users", users / "skills"), ("agent", agent / "skills")], "GET", "/open-apis/demo/list"
        )

        assert loose is not None and strict is not None
        assert loose.fields["page_size"]["max"] == 500
        assert strict.fields["page_size"]["max"] == 10, "两条阶梯共用顶层目录时串味了"

    def test_override_replaces_whole_rule(self, tmp_path: Path) -> None:
        """同一 endpoint 整体覆盖, 不做字段级 merge。

        官方那条带 ``token: user``, 个人层同 endpoint 那条不带。字段级 merge 会把 token
        带过来, 整体覆盖不会 —— 护栏上的"第三份内容"意味着用谁都没授权过的 token 去调。
        """
        official, users = tmp_path / "official", tmp_path / "users"
        _write_skill(
            official,
            "feishu-demo",
            rules="- endpoint: GET /open-apis/demo/list\n  token: user\n  fields:\n    page_size: {max: 500}\n",
        )
        _write_skill(users, "feishu-mine", rules=_STRICT_RULE)

        rule = _spec.rules_for_layers(
            [("official", official / "skills"), ("users", users / "skills")], "GET", "/open-apis/demo/list"
        )

        assert rule is not None
        assert rule.fields["page_size"]["max"] == 10
        assert rule.token == "", "字段级 merge 把官方那条的 token 带过来了"

    def test_single_root_ladder_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_lines) -> None:
        """未声明内容根: 阶梯只有 agent 根一级, 层名仍是 ``skills``, 探针仍 1 of 1。"""
        agent = tmp_path / "agent"
        _write_skill(agent, "feishu-demo", rules=_STRICT_RULE)
        monkeypatch.delenv(CONTENT_ROOTS_ENV, raising=False)
        monkeypatch.setattr(_api, "_skills_dir", lambda: str(agent / "skills"))

        ladder = _api._skills_ladder()
        assert ladder == [("skills", str(agent / "skills"))]

        _spec.rules_for_layers(ladder, "GET", "/open-apis/demo/list")
        line = _probe(probe_lines, "feishu_api_rules")
        assert "1 of 1 roots" in line
        assert "skills=1" in line

    def test_ladder_puts_agent_root_on_top(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """声明了内容根: agent 根排在最后(最近), 且层名是声明的名字。"""
        official, agent = tmp_path / "official", tmp_path / "agent"
        _declare_roots(monkeypatch, ("official", official))
        monkeypatch.setattr(_api, "_skills_dir", lambda: str(agent / "skills"))

        ladder = _api._skills_ladder()

        assert [name for name, _ in ladder] == ["official", _system._AGENT_ROOT_NAME]
        assert ladder[-1][1] == str(agent / "skills")

    def test_missing_agent_layer_does_not_shadow_nearer_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agent 根下的 ``skills`` **不存在**时, 个人层的规则仍然生效。

        这是 B-3 的真实形态: 上生产时 ``/workspace/skills`` 会被腾名, 而 ``_skills_ladder``
        照旧把它追加在**最近**那一级(见该函数 151-152 行)。于是"最高优先级的那一层指向一个
        不存在的目录"成为常态。

        怕的是它不被静默跳过而是参与判定, 把个人层的规则遮蔽掉 —— 那样表现是提示词索引里
        每人的覆盖看着生效, 而真实 API 调用失去护栏约束(该拒的没拒), 静默且朝最贵的方向错。
        上面那些判据都假设 agent 层目录存在, 覆盖不到这个形态。
        """
        official, users, agent = tmp_path / "official", tmp_path / "users", tmp_path / "agent"
        _write_skill(official, "feishu-demo", rules=_LOOSE_RULE)
        _write_skill(users, "feishu-demo-personal", rules=_STRICT_RULE)
        _declare_roots(monkeypatch, ("official", official), ("users", users))
        # 腾名: 目录压根不建, 与生产 `mv skills skills.pre-layering` 之后同形。
        monkeypatch.setattr(_api, "_skills_dir", lambda: str(agent / "skills"))
        assert not (agent / "skills").exists(), "这条判据的前提是该目录不存在"

        ladder = _api._skills_ladder()
        assert ladder[-1][0] == _system._AGENT_ROOT_NAME, "不存在的 agent 层仍应在阶梯最近处"

        rule = _spec.rules_for_layers(ladder, "GET", "/open-apis/demo/list")

        assert rule is not None, "不存在的 agent 层遮蔽了个人层: 真实调用会失去护栏"
        assert rule.fields["page_size"]["max"] == 10, "命中的不是个人层那条"
